"""ADR 0044: the Sunday run on the homelab server waits for the data platform's status file, stamps the image's commit
without git, and checks what it needs before it starts."""

import datetime as dt
import subprocess
from pathlib import Path

import httpx
import pytest

import intel.weekly as w
from intel.settings import McpSettings, Settings

SUNDAY_1500 = dt.datetime(2026, 11, 22, 14, 0, tzinfo=dt.UTC)  # Sunday 15:00 Berlin


def status(tmp_path: Path, result: str, started: str) -> Path:
    f = tmp_path / "weekly_last_status"
    f.write_text(f"result={result}\nstarted_at={started}\nfinished_at={started}\nfailed_steps=0\n")
    return f


def test_last_sunday_midnight_is_berlin_time():
    assert w.last_sunday_midnight(SUNDAY_1500) == dt.datetime(2026, 11, 21, 23, 0, tzinfo=dt.UTC)
    saturday = dt.datetime(2026, 11, 21, 22, 59, tzinfo=dt.UTC)  # Saturday 23:59 Berlin -> the previous Sunday
    assert w.last_sunday_midnight(saturday) == dt.datetime(2026, 11, 14, 23, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("result", "started", "want"),
    [
        ("success", "2026-11-22T00:05:00Z", "success"),  # this week (Sunday 01:05 Berlin)
        ("failed", "2026-11-22T00:05:00Z", "failed"),
        ("success", "2026-11-15T00:05:00Z", "pending"),  # last week's success does not count
        ("success", "garbage", "pending"),
    ],
)
def test_mip_week_status(tmp_path, result, started, want):
    assert w.mip_week_status(status(tmp_path, result, started), SUNDAY_1500) == want


def test_mip_week_status_missing_file(tmp_path):
    assert w.mip_week_status(tmp_path / "nope", SUNDAY_1500) == "missing"


def test_wait_returns_at_once_on_success(tmp_path, monkeypatch):
    f = status(tmp_path, "success", dt.datetime.now(dt.UTC).isoformat())
    monkeypatch.setattr(w, "mip_refresh_running", lambda: pytest.fail("pgrep must not be used on the server"))
    assert w.wait_for_mip(3600, lambda *_: None, status_file=f) == 0


def test_wait_until_this_weeks_run_succeeds(tmp_path, monkeypatch):
    f = status(tmp_path, "success", "2020-01-05T00:05:00Z")  # an old week: pending
    sleeps: list[int] = []

    def sleep(n):  # the data platform finishes while we sleep
        sleeps.append(n)
        status(tmp_path, "success", dt.datetime.now(dt.UTC).isoformat())

    monkeypatch.setattr(w.time, "sleep", sleep)
    logs: list[str] = []
    assert w.wait_for_mip(3600, logs.append, poll_s=300, status_file=f) == 300
    assert sleeps == [300] and "pending" in logs[0]


def test_wait_fails_when_the_week_never_succeeds(tmp_path, monkeypatch):
    f = status(tmp_path, "failed", dt.datetime.now(dt.UTC).isoformat())
    monkeypatch.setattr(w.time, "sleep", lambda n: None)
    with pytest.raises(RuntimeError, match=r"has not succeeded after 1 h \(status: failed"):
        w.wait_for_mip(3600, lambda *_: None, poll_s=600, status_file=f)


def test_weekly_run_uses_the_status_file(tmp_path, monkeypatch):
    f = status(tmp_path, "failed", dt.datetime.now(dt.UTC).isoformat())
    monkeypatch.setattr(w.time, "sleep", lambda n: None)
    s = Settings(
        _env_file=None, intel_data_dir=tmp_path / "data", mip_status_file=f, dk_warehouse_dsn="postgresql://u:p@x:1/x"
    )
    r = w.run_weekly(s, upload=False, wait_max_s=600, log=lambda *_: None)
    assert r["status"] == "error" and r["failed_step"] == "wait" and "status: failed" in r["error"]


def test_git_state_without_git_uses_the_image_commit(tmp_path, monkeypatch):
    from intel import package

    def no_git(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(package.subprocess, "run", no_git)
    monkeypatch.setenv("INTEL_GIT_SHA", "abc1234")
    assert package.git_state(tmp_path) == {"commit": "abc1234", "dirty": False}
    monkeypatch.setenv("INTEL_GIT_SHA", "unknown")
    assert package.git_state(tmp_path)["commit"] is None


def test_git_state_still_prefers_git(tmp_path, monkeypatch):
    from intel import package

    monkeypatch.setattr(
        package.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="deadbeef\n", stderr="")
    )
    monkeypatch.setenv("INTEL_GIT_SHA", "abc1234")
    assert package.git_state(tmp_path)["commit"] == "deadbeef"


def _ready(tmp_path: Path) -> tuple[Settings, McpSettings]:
    mip = tmp_path / "mip"
    for f in ("config/keywords/social_content.yaml", "config/content/playbook.yaml", "docs/brand/facts.yaml"):
        (mip / f).parent.mkdir(parents=True, exist_ok=True)
        (mip / f).write_text("x: 1\n")
    key = tmp_path / "key"
    key.write_text("k")
    s = Settings(
        _env_file=None,
        intel_data_dir=tmp_path / "data",
        mip_dir=mip,
        mip_status_file=status(tmp_path, "success", "2026-11-22T00:05:00Z"),
        mip_reader_dsn="postgresql://r:secret@mip-postgres:5432/mip",
        intel_api_url="https://api.example",
        dk_warehouse_ssh="intel@vps",
        dk_warehouse_ssh_key=key,
        dk_inbox_ssh="intelinbox@vps",
        dk_inbox_ssh_key=key,
    )
    return s, McpSettings(_env_file=None, intel_rag_admin_dsn="postgresql://b:secret@dk-rag:5432/rag")


def test_preflight_ok(tmp_path):
    from intel.server import preflight

    s, m = _ready(tmp_path)
    assert preflight(s, m, db=lambda *a: None, api=lambda *a: None) == []


def test_preflight_names_every_problem_without_secrets(tmp_path):
    from intel.server import _db, preflight

    s, m = _ready(tmp_path)
    s.mip_status_file = tmp_path / "gone"
    s.dk_inbox_ssh_key = tmp_path / "no-key"
    (s.mip_dir / "docs/brand/facts.yaml").unlink()
    got = preflight(s, m, db=_db, api=lambda *a: "intel-api: unreachable")
    text = "\n".join(got)
    assert "status file" in text and "facts.yaml" in text and "door 2" in text and "intel-api" in text
    assert "mip: cannot connect" in text and "knowledge db: cannot connect" in text
    assert "secret" not in text  # DSNs are redacted


def test_api_check(tmp_path):
    from intel.server import _api

    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert _api("https://x.ts.net/", ok) is None
    assert "503" in (_api("https://x.ts.net", bad) or "")
    assert "not set" in (_api(None) or "")
