#!/usr/bin/env bash
# What the Sunday run needs on this Mac, checked BEFORE it starts (called by scripts/weekly.sh once a run is due):
#   Tailscale connected (the run reaches the VPS through it) and Docker with the dk-rag and mip-postgres databases up
#   (the market snapshot and the knowledge pack need them). Missing pieces are started if possible and waited for.
#   If something stays down: a macOS notification (at most once a day) and exit 1; the run does NOT start, and launchd
#   tries again at the next hour. A run that started without them would fail or quietly skip steps (2026-10-25).
# Not Telegram: the bot token lives on the VPS only (CLAUDE.md rule 4), and with Tailscale down the VPS is unreachable.
# Knobs (tests): PREFLIGHT_WAIT seconds to wait per piece (default 90), PREFLIGHT_POLL seconds between looks (default 3),
#   PREFLIGHT_STATE folder for the once-a-day notification marker (default data/logs).
set -uo pipefail
WAIT="${PREFLIGHT_WAIT:-90}"
POLL="${PREFLIGHT_POLL:-3}"
STATE="${PREFLIGHT_STATE:-data/logs}"
problems=()

tailscale_ok() { tailscale status >/dev/null 2>&1; }
docker_ok() { docker info >/dev/null 2>&1; }
dbs_ok() {
  local up
  up=$(docker ps --format '{{.Names}}' 2>/dev/null) || return 1
  grep -qx dk-rag <<<"$up" && grep -qx mip-postgres <<<"$up"
}
wait_for() { # poll a check until it passes or WAIT seconds have gone by
  local end=$((SECONDS + WAIT))
  while true; do
    "$@" && return 0
    ((SECONDS >= end)) && return 1
    sleep "$POLL"
  done
}

if ! tailscale_ok; then
  echo "preflight: Tailscale is not connected; opening it"
  open -ga Tailscale >/dev/null 2>&1 || true
  wait_for tailscale_ok || problems+=("Tailscale is not connected")
fi
if ! docker_ok; then
  echo "preflight: Docker is not running; opening it"
  open -ga Docker >/dev/null 2>&1 || true
  wait_for docker_ok || problems+=("Docker is not running")
fi
if docker_ok && ! dbs_ok; then
  echo "preflight: starting the dk-rag and mip-postgres databases"
  docker start dk-rag mip-postgres >/dev/null 2>&1 || true
  wait_for dbs_ok || problems+=("the dk-rag and mip-postgres databases are not running")
fi

if [ ${#problems[@]} -gt 0 ]; then
  msg=$(IFS=';'; echo "${problems[*]}")
  echo "preflight FAILED: ${msg//;/; }"
  today=$(date +%F)
  mark="$STATE/.preflight_notified"
  if [ "$(cat "$mark" 2>/dev/null)" != "$today" ]; then
    osascript -e "display notification \"${msg//;/; }. Sunday training waits and retries every hour.\" with title \"DK Intelligence\"" >/dev/null 2>&1 || true
    mkdir -p "$STATE" && echo "$today" >"$mark"
  fi
  exit 1
fi
echo "preflight ok: Tailscale and the databases are up"
