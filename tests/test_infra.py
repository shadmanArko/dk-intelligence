"""ADR 0022: the VPS playbook, applied to a fresh Ubuntu 24.04 container set up like the VPS. Converges on the first
run, changes nothing on the second, adopts hand-made cron lines without duplicating them, leaves the harness's
cron line alone, and writes the three doors exactly. Needs Docker (CI has it); skipped otherwise."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")


@pytest.fixture(scope="module")
def applied() -> str:
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{ROOT}:/repo:ro", "ubuntu:24.04", "bash", "/repo/infra/test-in-container.sh"],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    return r.stdout


def recap(out: str, run: str) -> dict[str, int]:
    line = next(x for x in out.splitlines() if x.startswith(run))
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}


def section(out: str, name: str) -> str:
    return out.split(f"== {name}\n", 1)[1].split("\n== ", 1)[0]


def test_converges_then_changes_nothing(applied):
    assert recap(applied, "RUN1")["failed"] == 0 and recap(applied, "RUN1")["changed"] > 0
    second = recap(applied, "RUN2")
    assert second["changed"] == 0 and second["failed"] == 0, second


def test_cron_adopts_hand_made_lines_and_keeps_the_harness_job(applied):
    cron = section(applied, "crontab")
    assert cron.count("/opt/dhaka-kacchi/dhaka_kacchi_ai_harness/deploy/backup.sh") == 1  # not ours: untouched
    assert cron.count("dk-intelligence/deploy/backup.sh") == 1  # adopted, not duplicated
    assert cron.count("collector/run.sh daily") == 1 and cron.count("collector/run.sh weekly") == 1
    assert "#Ansible: intel-db backup" in cron and "#Ansible: intel restore drill" in cron


def test_the_three_doors_are_written_exactly(applied):
    tunnel = section(applied, "tunnel").splitlines()
    ports = 'permitopen="127.0.0.1:5432",permitopen="127.0.0.1:5442"'
    assert tunnel[0].startswith(f'restrict,port-forwarding,{ports},from="100.107.175.13" ssh-ed25519 ')
    assert tunnel[-1] == "/usr/sbin/nologin"
    inbox = section(applied, "inbox").splitlines()
    assert inbox[0].startswith(
        'command="/usr/bin/rrsync -wo -no-del /opt/dhaka-kacchi/intel-data/inbox",restrict,from="100.107.175.13" '
        "ssh-ed25519 "
    )
    assert inbox[1] == "intelinbox:intelinbox 750" and inbox[2] == "L"  # password locked
    door3 = section(applied, "door3")
    assert door3.startswith("root 755") and "exec /opt/dhaka-kacchi/dk-intelligence/deploy/deploy.sh" in door3


def test_deploy_owned_directories(applied):
    dirs = section(applied, "dirs")
    assert "/opt/dhaka-kacchi/metrics deploy 755" in dirs and "/opt/dhaka-kacchi/monitoring-runtime deploy 700" in dirs
