"""The server image installs only the runtime dependencies (no `train` group: mlflow, pandera). Anything the VPS
imports must not need them. Regression test for a near miss on 2026-10-08: weekly.py (imported by serve/runs.py)
briefly imported the pandera-based data checks at module level."""

import subprocess
import sys

TRAIN_ONLY = ("mlflow", "pandera")
SERVER_MODULES = (
    "intel.serve.app",
    "intel.serve.runs",
    "intel.serve.registry",
    "intel.serve.measure",
    "intel.serve.telegram",
    "intel.serve.views",
    "intel.weekly",
    "intel.pipeline",
    "intel.plugins",
    "intel.package",
)


def test_server_modules_import_without_training_dependencies():
    code = (
        "import sys\n"
        f"for m in {TRAIN_ONLY!r}: sys.modules[m] = None  # any import of them raises ImportError\n"
        f"for m in {SERVER_MODULES!r}: __import__(m)\n"
        "from intel.serve.runs import training_overview  # the call path that imports weekly at runtime\n"
        "from intel.weekly import RETRY_AFTER, last_sunday_noon\n"
        "from intel.plugins import discover; discover()\n"
        "print('ok')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-2000:]
