#!/usr/bin/env bash
# Start or refresh the monitoring stack (ADR 0016). Idempotent; deploy.sh runs it after every successful app deploy.
# Renders the Alertmanager config with the chat id from deploy/.env and writes the bot token to a 600 file (never into
# the config), then `compose up` and a config reload (SIGHUP) for Prometheus and Alertmanager.
set -euo pipefail
cd "$(dirname "$0")"

RUNTIME="${INTEL_MONITORING_RUNTIME:-/opt/dhaka-kacchi/monitoring-runtime}"
METRICS_DIR="${INTEL_METRICS_DIR:-/opt/dhaka-kacchi/metrics}"
APP_ENV=../.env
[[ -f .env ]] || { echo "deploy/monitoring/.env missing: see deploy/monitoring/README.md"; exit 2; }
[[ -f "$APP_ENV" ]] || { echo "deploy/.env missing"; exit 2; }

setting() { grep -E "^$1=" "$APP_ENV" | tail -1 | cut -d= -f2- || true; }
CHAT="$(setting TELEGRAM_CHAT_ID)"
TOKEN="$(setting TELEGRAM_BOT_TOKEN)"
[[ "$CHAT" =~ ^-?[0-9]+$ ]] || { echo "TELEGRAM_CHAT_ID missing or not a number in deploy/.env"; exit 2; }
[[ -n "$TOKEN" ]] || { echo "TELEGRAM_BOT_TOKEN missing in deploy/.env"; exit 2; }

mkdir -p "$METRICS_DIR" && chmod 755 "$METRICS_DIR"      # node-exporter (another user) reads the .prom files
( umask 077
  mkdir -p "$RUNTIME/alertmanager-data"
  printf '%s' "$TOKEN" > "$RUNTIME/telegram_token"
  PING="$(grep -E '^HEALTHCHECKS_PING_URL=' .env | cut -d= -f2- || true)"
  if [[ "$PING" =~ ^https://hc-ping\.com/[A-Za-z0-9-]+$ ]]; then RECEIVER=deadman; else RECEIVER=null; PING="http://127.0.0.1:9/unused"; fi
  printf '%s' "$PING" > "$RUNTIME/deadman_url"
  sed -e "s/__TELEGRAM_CHAT_ID__/$CHAT/" -e "s/__DEADMAN_RECEIVER__/$RECEIVER/" alertmanager/alertmanager.yml \
    > "$RUNTIME/alertmanager.yml"
  echo "== dead-man's switch: $([[ $RECEIVER == deadman ]] && echo "pinging healthchecks.io" || echo "not configured (HEALTHCHECKS_PING_URL)")" )

# Save what compose needs into .env (managed block), so EVERY `docker compose ...` in this folder works, not only
# the ones started by this script (a hand-run `docker compose exec` failed on missing variables before 2026-10-08).
managed() {
  local key="$1" value="$2"
  if grep -qE "^$key=" .env; then sed -i.bak "s|^$key=.*|$key=$value|" .env && rm -f .env.bak
  else printf '%s=%s\n' "$key" "$value" >> .env; fi
}
grep -q "^# managed by up.sh" .env || printf '\n# managed by up.sh (do not edit below)\n' >> .env
managed MON_UID "$(id -u)"
managed MON_GID "$(id -g)"
managed RUNTIME "$RUNTIME"
managed METRICS_DIR "$METRICS_DIR"
docker compose up -d --remove-orphans
docker compose kill -s SIGHUP prometheus alertmanager >/dev/null 2>&1 || true   # pick up changed rules / config
echo "== monitoring up: $(docker compose ps --format '{{.Service}}={{.State}}' | tr '\n' ' ')"

# End-to-end check: can Grafana actually query Prometheus? (Containers "running" is not enough: on 2026-10-08 every
# panel was empty because Grafana had unloaded its Prometheus plugin.)
GF_PORT="$(grep -E '^GRAFANA_PORT=' .env | cut -d= -f2- || true)"; GF_PORT="${GF_PORT:-3330}"
GF_PW="$(grep -E '^GRAFANA_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
for _ in $(seq 1 30); do
  if health="$(curl -sf -m 5 -u "admin:$GF_PW" "http://127.0.0.1:$GF_PORT/api/datasources/uid/prometheus/health")"; then
    [[ "$health" == *'"status":"OK"'* ]] && { echo "== grafana -> prometheus: OK"; exit 0; }
  fi
  sleep 2
done
echo "!! Grafana cannot query Prometheus: ${health:-no answer}"; exit 1
