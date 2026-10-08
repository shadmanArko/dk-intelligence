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
  docker build -f Dockerfile --build-arg GIT_SHA="$SHA" --build-arg BUILD_DATE="$(date -u +%FT%TZ)" -t "$IMAGE" ..
else
  echo "== pull $IMAGE"
  docker pull --quiet "$IMAGE"
fi
LABEL="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE")"
if [[ "$LABEL" != "$SHA" ]]; then
  echo "!! image was built from ${LABEL:-unknown}, expected $SHA"; record refused "$IMAGE" "label $LABEL"; exit 2
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
  INTEL_IMAGE="$1" docker compose up -d --no-build --remove-orphans
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
