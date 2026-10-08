#!/bin/bash
# Double-click in Finder: starts the full Sunday training NOW, in the background, and shows its log here.
# Closing this window only stops the view; the training keeps running. Live diagram: "DK Control Room.command".
cd "$(dirname "$0")" || exit 1
if [ -f data/weekly/.lock/pid ] && kill -0 "$(cat data/weekly/.lock/pid)" 2>/dev/null; then
  echo "A training run is already in progress. Showing its log (close this window any time)."
else
  nohup /bin/bash scripts/weekly.sh > /dev/null 2>&1 &
  echo "Training started in the background (it keeps running if you close this window)."
  sleep 2
fi
echo "Live diagram: double-click 'DK Control Room.command'; VPS: https://vmd206122.tailc055f3.ts.net/training"
echo
# shellcheck disable=SC2012  # our own log names (weekly_<timestamp>.log): newest by mtime is what we want
tail -n 50 -F "$(ls -t data/logs/weekly_2*.log | head -1)"
