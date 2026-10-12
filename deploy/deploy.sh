#!/usr/bin/env bash
# dk-intelligence deploy (ADR 0014), run on the VPS by /opt/dhaka-kacchi/bin/run-intel-deploy.sh (forced-command key,
# door 3) after it has pulled main. The VPS never builds: it runs the exact image CI built, scanned and pushed.
#
#   CI (door 3):  SSH_ORIGINAL_COMMAND="deploy <40-hex commit> sha256:<64-hex digest>"  -> that image, by digest
#   by hand:      ./deploy.sh                       -> the image CI built for the checked-out commit (tag sha-<commit>)
#                 ./deploy.sh --local-build         -> emergency only: build here (no scan, no registry)
#
# Steps: validate request -> skip if a newer commit is already checked out -> pull + verify the image's commit label
# -> remember the running image as :previous -> start -> smoke test inside the new container -> keep, or roll back to
# :previous and exit 1. Every attempt is appended to /opt/dhaka-kacchi/logs/intel-deploys.log.
set -euo pipefail
cd "$(dirname "$0")"

REGISTRY_IMAGE="ghcr.io/shadmanarko/dk-intelligence"
HISTORY="${INTEL_DEPLOY_HISTORY:-/opt/dhaka-kacchi/logs/intel-deploys.log}"
[[ -f .env ]] || { echo "deploy/.env missing: run the first-time setup in deploy/README.md"; exit 2; }
# shellcheck source=SCRIPTDIR/metrics.sh
source ./metrics.sh
# intel-mcp (ADR 0030) runs only when the owner enabled its compose profile: COMPOSE_PROFILES=mcp in deploy/.env
MCP_ON=false
grep -qE '^COMPOSE_PROFILES=.*\bmcp\b' .env && MCP_ON=true
pull() {  # image: up to 3 attempts; the VPS->registry link has reset mid-pull twice (postmortem 2026-10-12)
  local n
  for n in 1 2 3; do
    docker pull --quiet "$1" && return 0
    echo "!! pull attempt $n of 3 failed"
    [[ $n -lt 3 ]] && sleep "${INTEL_PULL_RETRY_S:-15}"
  done
  return 1
}
env_value() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }   # read one key; values never echoed

record() {  # outcome, image, note
  mkdir -p "$(dirname "$HISTORY")"
  printf '%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "${SHA:0:12}" "$1" "$2" "$3" >> "$HISTORY"
  write_metrics intel_deploy <<METRICS || true
# HELP intel_deploy_last_info Outcome of the last deploy attempt (deployed, rolled_back, superseded, refused, ...).
# TYPE intel_deploy_last_info gauge
intel_deploy_last_info{outcome="$1",commit="${SHA:0:12}"} 1
# HELP intel_deploy_last_timestamp_seconds When the last deploy attempt ended.
# TYPE intel_deploy_last_timestamp_seconds gauge
intel_deploy_last_timestamp_seconds $(date +%s)
METRICS
}

# ---------- 1. what to deploy ----------
REQ="${SSH_ORIGINAL_COMMAND:-}"
HEAD="$(git -C .. rev-parse HEAD)"
LOCAL_BUILD=false
if [[ "$REQ" =~ ^deploy\ ([0-9a-f]{40})\ (sha256:[0-9a-f]{64})$ ]]; then
  SHA="${BASH_REMATCH[1]}"; IMAGE="$REGISTRY_IMAGE@${BASH_REMATCH[2]}"
elif [[ -z "$REQ" && "${1:-}" == "--local-build" ]]; then
  SHA="$HEAD"; IMAGE="dk-intelligence:local"; LOCAL_BUILD=true
elif [[ -z "$REQ" && -z "${1:-}" ]]; then
  SHA="$HEAD"; IMAGE="$REGISTRY_IMAGE:sha-$HEAD"
else
  echo "!! refused: unexpected request"; exit 2   # never echo the request itself back
fi

if [[ "$SHA" != "$HEAD" ]]; then
  if git -C .. merge-base --is-ancestor "$SHA" "$HEAD"; then
    echo "== $SHA is older than the checked-out $HEAD: a newer deploy follows; nothing to do"
    record superseded "$IMAGE" "head ${HEAD:0:12}"; exit 0
  fi
  echo "!! $SHA is not on main"; record refused "$IMAGE" "not on main"; exit 2
fi

# ---------- 2. get and verify the image ----------
if $LOCAL_BUILD; then
  echo "== EMERGENCY local build of $SHA (not scanned, not from the registry)"
  docker build -f Dockerfile --target runtime --build-arg GIT_SHA="$SHA" --build-arg BUILD_DATE="$(date -u +%FT%TZ)" -t "$IMAGE" ..
else
  echo "== pull $IMAGE"
  pull "$IMAGE" || { record failed "$IMAGE" "pull failed 3 times"; exit 1; }
fi
LABEL="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE")"
if [[ "$LABEL" != "$SHA" ]]; then
  echo "!! image was built from ${LABEL:-unknown}, expected $SHA"; record refused "$IMAGE" "label $LABEL"; exit 2
fi
MCP_IMAGE=""
if $MCP_ON; then   # the same commit's intel-mcp image, checked the same way, before anything is switched
  if $LOCAL_BUILD; then
    MCP_IMAGE="dk-intelligence-mcp:local"
    docker build -f Dockerfile --target mcp --build-arg GIT_SHA="$SHA" -t "$MCP_IMAGE" ..
  else
    MCP_IMAGE="${REGISTRY_IMAGE}-mcp:sha-$SHA"
    echo "== pull $MCP_IMAGE"
    pull "$MCP_IMAGE" || { record failed "$MCP_IMAGE" "pull failed 3 times"; exit 1; }
  fi
  MCP_LABEL="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$MCP_IMAGE")"
  if [[ "$MCP_LABEL" != "$SHA" ]]; then
    echo "!! intel-mcp image was built from ${MCP_LABEL:-unknown}, expected $SHA"
    record refused "$MCP_IMAGE" "mcp label $MCP_LABEL"; exit 2
  fi
  touch mcp-revoked.txt   # the revocation file is mounted read-only into intel-mcp; it must exist
fi

# ---------- 3. remember what runs now ----------
OLD_CID="$(docker compose ps -q intel-api 2>/dev/null || true)"
EXPECT_MODELS=""
if [[ -n "$OLD_CID" ]]; then
  docker tag "$(docker inspect --format '{{.Image}}' "$OLD_CID")" dk-intelligence:previous
  EXPECT_MODELS="$(docker compose exec -T intel-api python -c \
    "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5))['models']))" \
    2>/dev/null || true)"
fi
echo "== active models before: ${EXPECT_MODELS:-unknown (old container not answering)}"

# ---------- 4. switch + smoke test ----------
start() {  # image
  INTEL_IMAGE="$1" INTEL_MCP_IMAGE="${MCP_IMAGE:-dk-intelligence-mcp:current}" \
    docker compose up -d --no-build --remove-orphans
}
mcp_roles() {  # the migration creates the rag-only roles without passwords; set them from deploy/.env (never echoed)
  INTEL_MCP_READER_PASSWORD="$(env_value INTEL_MCP_READER_PASSWORD)"
  INTEL_MCP_LOADER_PASSWORD="$(env_value INTEL_MCP_LOADER_PASSWORD)"
  export INTEL_MCP_READER_PASSWORD INTEL_MCP_LOADER_PASSWORD
  [[ -n "$INTEL_MCP_READER_PASSWORD" && -n "$INTEL_MCP_LOADER_PASSWORD" ]] || return 0
  docker compose exec -T -e INTEL_MCP_READER_PASSWORD -e INTEL_MCP_LOADER_PASSWORD intel-db \
    psql -q -v ON_ERROR_STOP=1 -U intel -d intel <<'SQL'
\getenv rp INTEL_MCP_READER_PASSWORD
\getenv lp INTEL_MCP_LOADER_PASSWORD
do $$ begin
  if not exists (select from pg_roles where rolname = 'intel_mcp_reader') then create role intel_mcp_reader login; end if;
  if not exists (select from pg_roles where rolname = 'intel_mcp_loader') then create role intel_mcp_loader login; end if;
end $$;
grant usage on schema rag to intel_mcp_reader, intel_mcp_loader;
grant select on rag.pack, rag.chunk to intel_mcp_reader;
grant select, insert, update, delete on rag.pack, rag.chunk to intel_mcp_loader;
alter role intel_mcp_reader set default_transaction_read_only = on;
alter role intel_mcp_reader password :'rp';
alter role intel_mcp_loader password :'lp';
SQL
}
mcp_smoke() {  # intel-mcp answers /healthz within 3 minutes (it warms its models at start)
  for _ in $(seq 1 "${INTEL_MCP_SMOKE_TRIES:-36}"); do
    if docker compose exec -T intel-mcp python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8766/healthz', timeout=5).status==200 else 1)" \
      >/dev/null 2>&1; then return 0; fi
    sleep "${INTEL_MCP_SMOKE_SLEEP_S:-5}"
  done
  return 1
}
smoke() {  # expected commit
  docker compose exec -T -e EXPECT_SHA="$1" -e EXPECT_MODELS="$EXPECT_MODELS" -e WAIT_S=90 intel-api python - < smoke.py
}

echo "== start ${SHA:0:12}"
start "$IMAGE"
sleep "${INTEL_DEPLOY_SETTLE_S:-5}"
if smoke "$SHA"; then
  docker tag "$IMAGE" dk-intelligence:current
  record deployed "$IMAGE" "${EXPECT_MODELS:-}"
  if $MCP_ON; then   # intel-api is proven; intel-mcp is checked on its own and never rolls intel-api back
    mcp_roles || echo "!! could not set the intel-mcp database role passwords"
    if mcp_smoke; then
      docker tag "$MCP_IMAGE" dk-intelligence-mcp:current
      record mcp_deployed "$MCP_IMAGE" "healthz ok"
      echo "== intel-mcp ${SHA:0:12} healthy"
    else
      echo "!! intel-mcp failed its health check: stopped (intel-api keeps running the new version)"
      docker compose logs --tail 60 intel-mcp || true
      docker compose stop intel-mcp || true
      record mcp_failed "$MCP_IMAGE" "healthz"
      exit 1
    fi
  fi
  docker image prune -f >/dev/null
  echo "== deployed ${SHA:0:12}"
  if [[ -f monitoring/.env ]]; then   # keep the monitoring stack in step with this commit's config (never fatal)
    monitoring/up.sh || echo "!! monitoring refresh failed (the app deploy itself succeeded)"
  fi
  exit 0
fi

echo "!! smoke test failed: logs of the new container"
docker compose logs --tail 60 intel-api || true
if [[ -n "$OLD_CID" ]]; then
  echo "== ROLLBACK to the previous image"
  start dk-intelligence:previous
  PREV_SHA="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' dk-intelligence:previous)"
  if [[ -n "$PREV_SHA" ]] && smoke "$PREV_SHA"; then
    record rolled_back "$IMAGE" "kept ${PREV_SHA:0:12}"
    echo "== rolled back: the previous version is serving"
  else
    record rollback_unverified "$IMAGE" "previous image started; smoke not confirmed"
    echo "!! previous image started, but its smoke test did not confirm it: check now"
  fi
else
  record failed "$IMAGE" "nothing to roll back to"
fi
exit 1
