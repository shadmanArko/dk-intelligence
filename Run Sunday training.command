#!/bin/bash
# Double-click in Finder to run the full Sunday training now (pull -> train -> judge -> report), with live output.
cd "$(dirname "$0")" || exit 1
echo "DK Intelligence - Sunday training, started $(date)"
echo "Progress is also visible on https://vmd206122.tailc055f3.ts.net/training"
echo
./scripts/weekly.sh --live
echo
read -r -p "Finished. Press Enter to close this window." _
