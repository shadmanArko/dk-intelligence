"""M7: run reports -> Telegram, Monday report, 14-day reminder, ledger download, error reporting on the Mac."""

import datetime as dt
import json

import httpx
import pytest

from intel.serve.db import connect, log_event
from intel.serve.runs import report_tick, run_message, scan_run_reports
from tests.test_measure import intel  # noqa: F401  (fixture)
from tests.test_serve import DSN

pytestmark = pytest.mark.skipif(not DSN, reason="INTEL_TEST_PG_DSN not set")


def report(**kw) -> dict:
    base = {
        "kind": "weekly_run",
        "format_version": 1,
        "run_id": "20261011T100000Z",
        "status": "fail",
        "started_at": "2026-10-11T10:00:00+00:00",
        "finished_at": "2026-10-11T10:20:00+00:00",
        "challenger": {"balanced_accuracy": 0.61, "roc_auc": 0.7, "brier": 0.22, "n": 80},
        "champion": {"balanced_accuracy": 0.62, "roc_auc": 0.7, "brier": 0.21, "n": 80},
        "failed_gates": ["better"],
        "live": "3 test posts have live 7-day results (need 30)",
    }
    return base | kw


class Tg:
    def __init__(self):
        self.sent = []

    def send(self, text, buttons=None):
        self.sent.append(text)
        return 1


def test_run_reports_are_validated_and_recorded_once(intel, tmp_path):  # noqa: F811
    (tmp_path / "weekly-20261011T100000Z.run.json").write_text(json.dumps(report()))
    (tmp_path / "weekly-20261011T110000Z.run.json").write_text(json.dumps(report(run_id="x", status="hacked")))
    (tmp_path / "weekly-20261011T120000Z.run.json").write_text("{" + " " * 70_000 + "}")
    got = scan_run_reports(intel, tmp_path)
    assert [r.run_id for r in got] == ["20261011T100000Z"]
    assert scan_run_reports(intel, tmp_path) == []  # idempotent, bad files not re-logged
    with connect(intel) as c:
        bad = c.execute("select count(*) n from event_log where kind='run_report_invalid'").fetchone()["n"]
    assert bad == 2
    msg = run_message(got[0])
    assert "no improvement this week" in msg and "better" in msg and "0.610" in msg


def test_messages_for_pass_and_error():
    from intel.serve.runs import RunReport

    assert "approval message follows" in run_message(RunReport.model_validate(report(status="pass")))
    e = run_message(RunReport.model_validate(report(status="error", failed_step="pull", error="tunnel <down>")))
    assert "failed</b> at step <b>pull" in e and "&lt;down&gt;" in e  # escaped


def test_monday_report_once_per_week_and_reminder(intel):  # noqa: F811
    tg = Tg()
    sunday = dt.datetime(2026, 10, 11, 12, tzinfo=dt.UTC)
    log_event(intel, "api_started", workers=[])
    with connect(
        intel
    ) as c:  # relative to the simulated clock below, never the real one (it broke 2 weeks after it was written)
        c.execute("update event_log set at = %s where kind = 'api_started'", (sunday - dt.timedelta(days=20),))
    monday_8 = dt.datetime(2026, 10, 12, 6, tzinfo=dt.UTC)  # 08:00 Berlin
    monday_930 = dt.datetime(2026, 10, 12, 7, 30, tzinfo=dt.UTC)  # 09:30 Berlin
    assert report_tick(intel, tg, "https://x.ts.net", sunday) == ["reminder"]
    assert report_tick(intel, tg, "https://x.ts.net", sunday + dt.timedelta(hours=2)) == []  # once a day
    assert report_tick(intel, tg, None, monday_8) == []
    assert report_tick(intel, tg, "https://x.ts.net", monday_930) == ["weekly_report"]  # reminders from 10:00
    assert report_tick(intel, tg, "https://x.ts.net", monday_930 + dt.timedelta(hours=1)) == ["reminder"]
    assert report_tick(intel, tg, "https://x.ts.net", monday_930 + dt.timedelta(hours=2)) == []
    assert "Weekly report" in tg.sent[1] and "/posts" in tg.sent[1] and "No Sunday training" in tg.sent[1]


def test_fetch_ledger(tmp_path):
    from intel.weekly import fetch_ledger

    rows = [{"post_id": "p1", "model_name": "content_scorer", "predicted_label": 1, "outcomes": {}}]
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"rows": rows})))
    got, path = fetch_ledger("https://x.ts.net/", tmp_path, client)
    assert got == rows and path.exists() and path.with_suffix(".sha256").exists()


def test_weekly_failure_still_writes_error_report(tmp_path, monkeypatch):
    import intel.weekly as w
    from intel.settings import Settings

    monkeypatch.setattr(w, "mip_refresh_running", lambda: False)
    s = Settings(_env_file=None, intel_data_dir=tmp_path, dk_warehouse_dsn="postgresql://u:p@127.0.0.1:1/x")
    r = w.run_weekly(s, upload=False, log=lambda *_: None)
    assert r["status"] == "error" and r["failed_step"] == "pull" and "TunnelError" in r["error"]
    files = list((tmp_path / "weekly").glob("weekly-*.run.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["failed_step"] == "pull"
    st = json.loads((tmp_path / "weekly" / "current.json").read_text())
    assert (
        st["result"] == "error"
        and st["steps"]["pull"]["status"] == "failed"
        and st["steps"]["wait"]["status"] == "done"
    )
    assert not (tmp_path / "weekly" / ".lock").exists()


# ---------- schedule, catch-up, lock, progress ----------


def _hist(tmp_path, run_id, status, started, finished=None, dry=False):
    w = tmp_path / "weekly"
    w.mkdir(exist_ok=True)
    (w / f"weekly-{run_id}.run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": status,
                "started_at": started.isoformat(),
                "finished_at": (finished or started + dt.timedelta(minutes=10)).isoformat(),
                "dry_run": dry,
            }
        )
    )
    return w


def test_due_rules_and_catch_up_after_mac_was_off(tmp_path):
    from intel.weekly import is_due, last_sunday_noon

    sun_noon = dt.datetime(2026, 10, 11, 10, tzinfo=dt.UTC)  # 12:00 Berlin (CEST)
    assert last_sunday_noon(sun_noon + dt.timedelta(minutes=1)) == sun_noon
    assert last_sunday_noon(sun_noon - dt.timedelta(minutes=1)) == sun_noon - dt.timedelta(days=7)
    w = tmp_path / "weekly"
    w.mkdir()
    assert is_due(w, sun_noon + dt.timedelta(minutes=5))[0]  # never ran
    # Mac off from Saturday to Tuesday: last success the previous week -> due on Tuesday
    _hist(tmp_path, "20261004T100000Z", "fail", sun_noon - dt.timedelta(days=7))
    assert is_due(w, sun_noon + dt.timedelta(days=2))[0]
    _hist(tmp_path, "20261013T090000Z", "pass", sun_noon + dt.timedelta(days=2))
    assert not is_due(w, sun_noon + dt.timedelta(days=2, hours=1))[0]  # done this week
    assert is_due(w, sun_noon + dt.timedelta(days=7, minutes=1))[0]  # next Sunday


def test_failed_run_retried_after_6h_and_dry_runs_ignored(tmp_path):
    from intel.weekly import is_due

    t = dt.datetime(2026, 10, 11, 11, tzinfo=dt.UTC)
    w = _hist(tmp_path, "20261011T110000Z", "error", t)
    assert not is_due(w, t + dt.timedelta(hours=2))[0]
    assert is_due(w, t + dt.timedelta(hours=7))[0]
    _hist(tmp_path, "20261011T190000Z", "fail", t + dt.timedelta(hours=8), dry=True)
    assert is_due(w, t + dt.timedelta(hours=9))[0]  # a --no-upload run does not count


def test_lock_blocks_second_run_and_takes_over_stale(tmp_path):
    from intel.weekly import Lock

    with Lock(tmp_path), pytest.raises(RuntimeError, match="in progress"), Lock(tmp_path):
        pass
    (tmp_path / ".lock").mkdir()
    (tmp_path / ".lock" / "pid").write_text("999999")  # dead process
    with Lock(tmp_path):
        pass
    assert not (tmp_path / ".lock").exists()


def test_progress_files_and_upload_failure_is_harmless(tmp_path):
    from intel.weekly import Progress

    calls = []

    def flaky(path):
        calls.append(path.name)
        raise OSError("vps down")

    p = Progress("20261011T100000Z", tmp_path, flaky)
    p.start("pull")
    p.done("pull", "P1")
    p.fail("market", "boom")
    p.finish("error")
    st = json.loads((tmp_path / "current.json").read_text())
    assert st["state"] == "finished" and st["steps"]["pull"]["status"] == "done"
    assert st["steps"]["market"]["status"] == "failed" and st["steps"]["upload"]["status"] == "skipped"
    assert calls and p.upload_error.startswith("OSError")


def test_vps_progress_stall_page_and_telegram(intel, tmp_path):  # noqa: F811
    from fastapi.testclient import TestClient

    from intel.serve.app import create_app
    from intel.serve.runs import scan_status, training_overview, training_text
    from intel.settings import Settings
    from intel.weekly import Progress

    inbox = tmp_path / "inbox"
    p = Progress("20261011T100000Z", inbox)
    p.start("pull")
    assert scan_status(intel, inbox) is True and scan_status(intel, inbox) is False
    old = json.loads((inbox / "weekly-20261011T100000Z.status.json").read_text()) | {"seq": 0}
    (inbox / "weekly-20261011T100000Z.status.json").write_text(json.dumps(old))
    assert scan_status(intel, inbox) is False  # older seq ignored
    now = dt.datetime.fromisoformat(p.state["updated_at"])
    assert training_overview(intel, now)["stalled"] is None
    assert training_overview(intel, now + dt.timedelta(hours=3))["stalled"]
    assert "⏳ Pull the warehouse" in training_text(intel)

    s = Settings(
        _env_file=None,
        intel_db_dsn=intel,
        intel_inbox_dir=inbox,
        intel_store_dir=tmp_path / "store",
        intel_git_sha="abc1234",
    )
    with TestClient(create_app(s, start_workers=False)) as c:
        page = c.get("/training").text
        assert "Run 20261011T100000Z" in page and "Pull the warehouse" in page and "abc1234" in page
        assert c.get("/ready").json()["version"] == "abc1234"


def test_vps_diagram_endpoints_and_details(intel, tmp_path):  # noqa: F811
    from fastapi.testclient import TestClient

    from intel.serve.app import create_app
    from intel.serve.runs import scan_status
    from intel.settings import Settings
    from intel.weekly import Progress

    inbox = tmp_path / "inbox"
    p = Progress("20261011T100000Z", inbox)
    p.start("pull")
    p.done("pull", "40 tables", {"tables": 40, "rows": 11622, "checksums": "verified"})
    assert scan_status(intel, inbox)
    bad = json.loads((inbox / "weekly-20261011T100000Z.status.json").read_text())
    bad["seq"] = 99
    bad["steps"]["hack"] = {"label": "x", "status": "done"}
    (inbox / "weekly-20261011T100000Z.status.json").write_text(json.dumps(bad))
    assert scan_status(intel, inbox) is False  # unknown step rejected
    s = Settings(_env_file=None, intel_db_dsn=intel, intel_inbox_dir=inbox, intel_store_dir=tmp_path / "store")
    with TestClient(create_app(s, start_workers=False)) as c:
        page = c.get("/training")
        assert "id=pipeline" in page.text and "script-src 'self'" in page.headers["content-security-policy"]
        assert "script-src" not in c.get("/").headers["content-security-policy"]  # other pages stay script-free
        st = c.get("/api/training-state").json()
        assert st["progress"]["steps"]["pull"]["details"]["rows"] == 11622 and st["running"] is True
        assert c.get("/static/pipeline.css").status_code == 200 and c.get("/static/x.py").status_code == 404
        assert len(c.get("/api/pipeline").json()["nodes"]) >= 10


def test_run_message_lists_each_model():
    from intel.serve.runs import RunReport

    rep = RunReport.model_validate(
        report(
            models=[
                {"name": "content_scorer", "verdict": "fail", "new": 0.56, "current": 0.62, "failed_gates": ["better"]},
                {"name": "reach_scorer", "verdict": "pass", "new": 0.70, "current": 0.63, "failed_gates": []},
            ]
        )
    )
    msg = run_message(rep)
    assert "➖ content_scorer: fail" in msg and "✅ reach_scorer: pass" in msg


def test_the_knowledge_pack_step_reaches_the_sunday_message():
    from intel.serve.runs import RunReport, knowledge_line

    live = RunReport.model_validate(
        report(knowledge={"status": "active", "pack": "kp-1", "newly_embedded": 37, "collections": {"reviews": 1}})
    )
    assert "kp-1</code> is live (gate passed, 37 new texts embedded)" in run_message(live)
    sent = RunReport.model_validate(report(knowledge={"status": "active", "pack": "kp-1", "shipped": True}))
    assert "is live (gate passed) and sent to the VPS" in run_message(sent)
    failed = RunReport.model_validate(report(knowledge={"status": "active", "pack": "kp-1", "shipped": False}))
    assert "(upload to the VPS failed)" in run_message(failed)
    kept = RunReport.model_validate(
        report(knowledge={"status": "kept_previous", "pack": "kp-2", "gate_lost": ["q03", "q08"]})
    )
    assert "refused by its gate (lost q03, q08)" in run_message(kept)
    assert "skipped: no db" in knowledge_line(
        RunReport.model_validate(report(knowledge={"status": "skipped", "why": "no db"})).knowledge
    )
    err = RunReport.model_validate(report(knowledge={"status": "error", "error": "OSError: <disk>"}))
    assert "&lt;disk&gt;" in run_message(err)  # escaped for Telegram HTML
    assert RunReport.model_validate(report()).knowledge is None and knowledge_line(None) is None  # older Macs


def test_old_reports_are_recorded_quietly_so_the_reminder_and_the_overdue_alert_clear(intel, tmp_path):  # noqa: F811
    """After the 160-character `package` limit was lifted the VPS found a backlog of rejected reports: all are
    recorded (the 14-day reminder reads the newest), but only a recent one is worth a Telegram message."""
    from intel.serve.runs import worth_telling

    package = ", ".join(f"model_{i}-20261026T0710{i:02d}-abcdefabcdef.tar.gz" for i in range(6))
    old = report(run_id="20261009T072800Z", status="pass", finished_at="2026-10-09T07:30:00+00:00", package=package)
    new = report(run_id="20261026T070917Z", status="pass", finished_at="2026-10-26T07:11:00+00:00", package=package)
    (tmp_path / "weekly-20261009T072800Z.run.json").write_text(json.dumps(old))
    (tmp_path / "weekly-20261026T070917Z.run.json").write_text(json.dumps(new))
    got = scan_run_reports(intel, tmp_path)
    assert [r.run_id for r in got] == ["20261009T072800Z", "20261026T070917Z"]
    now = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.UTC)
    assert [worth_telling(r, now) for r in got] == [False, True]
    with connect(intel) as c:
        assert c.execute("select count(*) n from training_run").fetchone()["n"] == 2
        assert c.execute("select count(*) n from event_log where kind='run_report_invalid'").fetchone()["n"] == 0
