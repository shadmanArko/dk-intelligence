"""deploy/tidy-disk.sh (ADR 0042): the weekly VPS tidy, run for real against a fake `docker` and a folder of dumps."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
exit 0
"""


def _run(tmp_path: Path, docker_script: str = FAKE_DOCKER) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    bin_dir, backups = tmp_path / "bin", tmp_path / "backups"
    bin_dir.mkdir()
    backups.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(docker_script)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    old = time.time() - 20 * 86400
    for name, age in (("intel_2026-09-01_020000.sql.gz", old), ("intel_2026-10-12_020000.sql.gz", time.time() - 86400),
                      ("harness_2026-09-01.sql.gz", old), ("notes.txt", old)):  # fmt: skip
        f = backups / name
        f.write_text("x")
        os.utime(f, (age, age))
    log = tmp_path / "docker.log"
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    r = subprocess.run(
        ["bash", str(ROOT / "deploy" / "tidy-disk.sh")], capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(log),
             "TIDY_BACKUP_DIR": str(backups), "INTEL_METRICS_DIR": str(metrics)},
    )  # fmt: skip
    return r, log.read_text().splitlines() if log.exists() else [], backups


def test_the_weekly_tidy_prunes_unused_docker_things_older_than_three_days_and_never_volumes(tmp_path):
    r, calls, _ = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "docker container prune -f --filter until=72h" in calls
    assert "docker image prune -a -f --filter until=72h" in calls
    assert "docker builder prune -af --filter until=72h" in calls
    assert not any("volume" in c or "system prune" in c for c in calls)  # the databases live in volumes
    assert "disk tidy: freed" in r.stdout
    assert "intel_disk_tidy_last_success_timestamp_seconds" in (tmp_path / "metrics" / "disk_tidy.prom").read_text()


def test_only_our_own_old_database_dumps_are_removed(tmp_path):
    r, _, backups = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    left = sorted(p.name for p in backups.iterdir())
    assert left == [
        "harness_2026-09-01.sql.gz",
        "intel_2026-10-12_020000.sql.gz",
        "notes.txt",
    ]  # old intel dump gone, rest kept


def test_a_failing_prune_is_reported_but_never_stops_the_rest(tmp_path):
    failing = """#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
[[ "$1" == image ]] && exit 1
exit 0
"""
    r, calls, _ = _run(tmp_path, docker_script=failing)
    assert r.returncode == 0 and "image prune failed" in r.stdout
    assert any(c.startswith("docker builder prune") for c in calls)  # it went on after the failure
