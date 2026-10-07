#!/usr/bin/env bash
# Sunday run (M7), started by ~/Library/LaunchAgents/com.dhakakacchi.intel.weekly.plist (Sundays 12:00, or at the
# next wake if the Mac was asleep). Keeps the Mac awake while it runs; safe to run by hand.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH" MLFLOW_DISABLE_AGENT_HINT=1
mkdir -p data/logs
LOG="data/logs/weekly_$(date +%Y%m%d_%H%M).log"
echo "== $(date -u +%FT%TZ) start" >> "$LOG"
caffeinate -i uv run intel weekly >> "$LOG" 2>&1
code=$?
echo "== $(date -u +%FT%TZ) exit $code" >> "$LOG"
if [ "$code" -ne 0 ]; then
  osascript -e 'display notification "Sunday training failed - see dk-intelligence/data/logs" with title "DK Intelligence"' || true
fi
find data/logs -name 'weekly_*.log' -mtime +90 -delete
exit "$code"
