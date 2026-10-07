#!/bin/bash
# Double-click in Finder: opens the DK Intelligence Control Room in your browser (Run button + live pipeline).
# Keep this window open while you use the page; close it (or press Ctrl+C) to stop the page. A running training
# continues even if you close it.
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH" MLFLOW_DISABLE_AGENT_HINT=1
uv run intel control
