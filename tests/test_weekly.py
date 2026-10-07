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
    log_event(intel, "api_started", workers=[])
    with connect(intel) as c:
        c.execute("update event_log set at = now() - interval '20 days' where kind = 'api_started'")
    sunday = dt.datetime(2026, 10, 11, 12, tzinfo=dt.UTC)
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
