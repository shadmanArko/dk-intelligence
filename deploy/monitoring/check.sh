#!/usr/bin/env bash
# Validate the monitoring stack exactly as the VPS will load it (ADR 0016). Run locally and in CI.
#   promtool check config   prometheus.yml + every rule file
#   promtool test rules     unit tests: which alerts fire, when, with which text
#   amtool check-config     the rendered Alertmanager config + Telegram template
#   compose config          the compose file, with the variables up.sh provides
#   dashboards              valid JSON, unique uids, every panel query non-empty and valid PromQL
set -euo pipefail
cd "$(dirname "$0")"
PROM=$(sed -n 's/^ *image: \(prom\/prometheus:.*\)$/\1/p' docker-compose.yml)
AM=$(sed -n 's/^ *image: \(prom\/alertmanager:.*\)$/\1/p' docker-compose.yml)

echo "== promtool check config"
docker run --rm -v "$PWD/prometheus:/etc/prometheus:ro" --entrypoint promtool "$PROM" check config /etc/prometheus/prometheus.yml
echo "== promtool test rules"
docker run --rm -v "$PWD/prometheus:/p:ro" --entrypoint promtool "$PROM" test rules /p/tests/slo_test.yml /p/tests/ops_test.yml /p/tests/models_test.yml

echo "== amtool check-config"
# The rendered files live in a private temp dir (mode 700), so the tool containers run as the calling user: the same
# way up.sh runs Alertmanager on the VPS (as the deploy user). Docker Desktop on a Mac ignores these permissions;
# Linux (CI, the VPS) does not.
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for receiver in deadman null; do  # both renderings up.sh can produce must be valid
  sed -e 's/__TELEGRAM_CHAT_ID__/123456789/' -e "s/__DEADMAN_RECEIVER__/$receiver/" alertmanager/alertmanager.yml \
    > "$tmp/alertmanager-$receiver.yml"
done
printf 'placeholder' > "$tmp/telegram_token"
printf 'https://hc-ping.com/00000000-0000-0000-0000-000000000000' > "$tmp/deadman_url"
docker run --rm --user "$(id -u):$(id -g)" -v "$tmp:/runtime:ro" -v "$PWD/alertmanager/templates:/etc/alertmanager/templates:ro" \
  --entrypoint amtool "$AM" check-config /runtime/alertmanager-deadman.yml /runtime/alertmanager-null.yml

echo "== compose config"
MON_UID=1000 MON_GID=1000 RUNTIME=/tmp/runtime METRICS_DIR=/tmp/metrics GRAFANA_ADMIN_PASSWORD=x \
  docker compose config -q

echo "== dashboards"
python3 - "$tmp" <<'PY'
import json, pathlib, sys
uids, problems = set(), []
for f in sorted(pathlib.Path("grafana/dashboards").glob("*.json")):
    d = json.loads(f.read_text())
    if d["uid"] in uids:
        problems.append(f"{f.name}: duplicate uid {d['uid']}")
    uids.add(d["uid"])
    for p in d["panels"]:
        for t in p.get("targets", []):
            if not t.get("expr", "").strip():
                problems.append(f"{f.name}: panel {p['title']!r} has an empty query")
    exprs = [t["expr"] for p in d["panels"] for t in p.get("targets", []) if t.get("expr")]
    rules = {"groups": [{"name": f.stem, "rules": [{"record": f"dash:q{i}", "expr": e} for i, e in enumerate(exprs)]}]}
    pathlib.Path(sys.argv[1], f"{f.stem}.rules.yml").write_text(json.dumps(rules))  # JSON is valid YAML
print("\n".join(problems) or f"{len(uids)} dashboards OK")
sys.exit(1 if problems else 0)
PY
# every dashboard query must parse: promtool checks them as (throwaway) recording rules
docker run --rm --user "$(id -u):$(id -g)" -v "$tmp:/q:ro" --entrypoint sh "$PROM" -c 'promtool check rules /q/*.rules.yml' | grep -E "SUCCESS|FAILED"
