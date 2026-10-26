"""scripts/preflight.sh: what the Sunday run needs on the Mac (Tailscale, Docker, the two databases) is checked first,
started if missing, and reported with a macOS notification (once a day) if it stays down. Run against stand-in
`tailscale`, `docker`, `open` and `osascript` programs: nothing here touches the real ones."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight.sh"

TAILSCALE = '#!/bin/bash\n[ -f "$STUB/ts_up" ]\n'
DOCKER = """#!/bin/bash
case "$1" in
  info) [ -f "$STUB/docker_up" ] ;;
  ps) [ -f "$STUB/docker_up" ] || exit 1
      [ -f "$STUB/db_up" ] && printf 'dk-rag\\nmip-postgres\\nfrappe_docker-frontend-1\\n' ;;
  start) shift; echo "docker start $*" >>"$STUB/calls"
         [ -f "$STUB/start_brings_dbs" ] && touch "$STUB/db_up"; exit 0 ;;
esac
"""
OPEN = """#!/bin/bash
echo "open $*" >>"$STUB/calls"
[ "$2" = Tailscale ] && [ -f "$STUB/open_starts_ts" ] && touch "$STUB/ts_up"
[ "$2" = Docker ] && [ -f "$STUB/open_starts_docker" ] && touch "$STUB/docker_up" "$STUB/db_up"
exit 0
"""
OSASCRIPT = '#!/bin/bash\necho "$*" >>"$STUB/notifications"\n'


@pytest.fixture
def mac(tmp_path):
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in (("tailscale", TAILSCALE), ("docker", DOCKER), ("open", OPEN), ("osascript", OSASCRIPT)):
        f = stub / name
        f.write_text(body)
        f.chmod(0o755)
    state = tmp_path / "state"
    env = {"PATH": f"{stub}:/usr/bin:/bin", "STUB": str(stub), "PREFLIGHT_WAIT": "1", "PREFLIGHT_POLL": "0.1",
           "PREFLIGHT_STATE": str(state), "HOME": str(tmp_path)}  # fmt: skip

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False)

    def have(*flags: str) -> None:
        for f in flags:
            (stub / f).touch()

    def read(name: str) -> str:
        p = stub / name
        return p.read_text() if p.exists() else ""

    return run, have, read, state


def test_everything_up_starts_nothing_and_says_nothing(mac):
    run, have, read, _ = mac
    have("ts_up", "docker_up", "db_up")
    r = run()
    assert r.returncode == 0 and "preflight ok" in r.stdout
    assert read("calls") == "" and read("notifications") == ""


def test_a_stopped_tailscale_and_docker_are_started_and_waited_for(mac):
    run, have, read, _ = mac
    have("open_starts_ts", "open_starts_docker")  # opening the apps brings them up
    r = run()
    assert r.returncode == 0 and "preflight ok" in r.stdout
    assert "open -ga Tailscale" in read("calls") and "open -ga Docker" in read("calls")
    assert read("notifications") == ""


def test_stopped_databases_are_started_with_docker_start(mac):
    run, have, read, _ = mac
    have("ts_up", "docker_up", "start_brings_dbs")
    r = run()
    assert r.returncode == 0 and "docker start dk-rag mip-postgres" in read("calls")


def test_what_stays_down_is_reported_once_a_day_and_blocks_the_run(mac):
    run, have, read, state = mac
    have("docker_up", "db_up")  # Tailscale stays off and opening it does nothing
    first = run()
    assert first.returncode == 1 and "Tailscale is not connected" in first.stdout
    notes = read("notifications")
    assert "DK Intelligence" in notes and "Tailscale is not connected" in notes and "retries every hour" in notes
    assert (state / ".preflight_notified").exists()
    second = run()  # the same day: still blocked, but no second notification
    assert second.returncode == 1 and read("notifications") == notes


def test_every_missing_piece_is_named(mac):
    run, _, read, _ = mac
    r = run()  # nothing is up and nothing can be started
    assert r.returncode == 1
    for piece in ("Tailscale is not connected", "Docker is not running"):
        assert piece in r.stdout
    assert piece in read("notifications")


def test_the_databases_must_both_be_up(mac):
    run, have, _, _ = mac
    have("ts_up", "docker_up")  # Docker is up but neither database is
    r = run()
    assert r.returncode == 1 and "databases are not running" in r.stdout


def test_the_weekly_script_calls_preflight_only_once_a_run_is_due_and_keeps_its_exit_code():
    weekly = (SCRIPT.parent / "weekly.sh").read_text()
    due = weekly.index('"this week: DUE"')
    assert due < weekly.index("bash scripts/preflight.sh") < weekly.index("caffeinate -is")
    assert "exit 75" in weekly and os.access(SCRIPT, os.X_OK)
