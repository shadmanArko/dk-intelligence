#!/bin/sh
# One systemd start (hourly + Sunday 12:00): cheap "is it due?" first, then the preflight, then the run. Mirrors the
# Mac's scripts/weekly.sh. Exit 0 = nothing due or done; 1 = the run failed (its report says where); 75 = not ready.
set -u
if ! intel weekly-status 2>/dev/null | grep -q '^this week: DUE'; then
  echo "not due: $(intel weekly-status 2>/dev/null | grep '^this week:' || echo 'status unknown')"
  exit 0
fi
intel server-preflight || exit 75
exec intel weekly --if-due --wait-hours "${INTEL_WAIT_HOURS:-8}"
