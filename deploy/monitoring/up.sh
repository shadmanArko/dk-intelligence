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
  sed "s/__TELEGRAM_CHAT_ID__/$CHAT/" alertmanager/alertmanager.yml > "$RUNTIME/alertmanager.yml" )

export MON_UID MON_GID RUNTIME METRICS_DIR
MON_UID="$(id -u)"; MON_GID="$(id -g)"
docker compose up -d --remove-orphans
docker compose kill -s SIGHUP prometheus alertmanager >/dev/null 2>&1 || true   # pick up changed rules / config
echo "== monitoring up: $(docker compose ps --format '{{.Service}}={{.State}}' | tr '\n' ' ')"
