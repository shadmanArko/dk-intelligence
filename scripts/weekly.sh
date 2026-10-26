#!/usr/bin/env bash
# Sunday training (M7). Started by launchd (~/Library/LaunchAgents/com.dhakakacchi.intel.weekly.plist):
#   every hour, at login and on Sundays 12:00 with --if-due -> runs once per week, catches up after the Mac was off.
# Before a run starts, scripts/preflight.sh checks Tailscale and the databases (and starts what is missing).
# By hand: double-click "Run Sunday training.command" in the repo, or: scripts/weekly.sh --live
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH" MLFLOW_DISABLE_AGENT_HINT=1 PYTHONUNBUFFERED=1
LIVE=0; ARGS=()
for a in "$@"; do if [ "$a" = "--live" ]; then LIVE=1; else ARGS+=("$a"); fi; done

if [[ " ${ARGS[*]-} " == *" --if-due "* ]]; then          # cheap check first: no log file when nothing is due
  out=$(uv run intel weekly-status 2>/dev/null | grep '^this week:')
  [[ "$out" == "this week: DUE"* ]] || exit 0
fi

mkdir -p data/logs
LOG="data/logs/weekly_$(date +%Y%m%d_%H%M).log"
echo "== $(date -u +%FT%TZ) start ${ARGS[*]-}" >> "$LOG"
if ! bash scripts/preflight.sh >> "$LOG" 2>&1; then   # Tailscale + Docker databases: start them, or stay out of the way
  echo "== $(date -u +%FT%TZ) not started: the Mac is not ready (see above); launchd retries at the next hour" >> "$LOG"
  exit 75
fi
if [ "$LIVE" = 1 ]; then
  caffeinate -is uv run intel weekly ${ARGS[@]+"${ARGS[@]}"} 2>&1 | tee -a "$LOG"; code=${PIPESTATUS[0]}
else
  caffeinate -is uv run intel weekly ${ARGS[@]+"${ARGS[@]}"} >> "$LOG" 2>&1; code=$?
fi
echo "== $(date -u +%FT%TZ) exit $code" >> "$LOG"
if [ "$code" -ne 0 ]; then
  osascript -e 'display notification "Sunday training failed - see dk-intelligence/data/logs" with title "DK Intelligence"' || true
fi
find data/logs -name 'weekly_*.log' -mtime +90 -delete
exit "$code"
