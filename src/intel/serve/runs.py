"""M7 on the VPS: weekly run reports from the Mac -> Telegram; Monday report; reminder if the Mac stops training.

Run reports arrive through the same upload-only inbox as packages (`weekly-<run_id>.run.json`), so the Mac needs
no extra credential. They are validated strictly before use (size, schema, allowed values).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from intel.serve.db import connect, kv_get, kv_set, log_event

log = logging.getLogger("intel.runs")
H = html.escape
MAX_BYTES = 64_000
REMIND_AFTER = dt.timedelta(days=14)


class Scores(BaseModel):
    model_config = ConfigDict(extra="ignore")
    balanced_accuracy: float | None = None
    roc_auc: float | None = None
    brier: float | None = None
    n: int | None = None


class RunReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["weekly_run"]
    run_id: str = Field(pattern=r"^\d{8}T\d{6}Z$")
    status: Literal["pass", "fail", "baseline", "error"]
    started_at: dt.datetime
    finished_at: dt.datetime
    failed_step: str | None = Field(default=None, max_length=40)
    error: str | None = Field(default=None, max_length=500)
    pull_id: str | None = Field(default=None, max_length=40)
    market_id: str | None = Field(default=None, max_length=40)
    market_fallback: bool = False
    version: str | None = Field(default=None, max_length=80)
    package: str | None = Field(default=None, max_length=160)
    posts: int | None = None
    challenger: Scores | None = None
    champion: Scores | None = None
    gain: float | None = None
    failed_gates: list[str] = Field(default_factory=list, max_length=20)
    live: str | None = Field(default=None, max_length=300)


def scan_run_reports(dsn: str, inbox: Path) -> list[RunReport]:
    new = []
    for f in sorted(inbox.glob("weekly-*.run.json")):
        try:
            data = f.read_bytes()
            if len(data) > MAX_BYTES:
                raise ValueError("report too large")
            digest = hashlib.sha256(data).hexdigest()
            with connect(dsn) as c:
                if c.execute("select 1 from training_run where file_sha256=%s", (digest,)).fetchone():
                    continue
            rep = RunReport.model_validate(json.loads(data))
        except (OSError, ValueError, ValidationError) as e:
            key = f"bad_run_report:{f.name}"
            if not kv_get(dsn, key):
                kv_set(dsn, key, True)
                log_event(dsn, "run_report_invalid", file=f.name, error=str(e)[:300])
            continue
        with connect(dsn) as c:
            cur = c.execute(
                """insert into training_run (run_id, status, failed_step, version, package, report, file_sha256)
                   values (%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                (rep.run_id, rep.status, rep.failed_step, rep.version, rep.package, rep.model_dump_json(), digest),
            )
        if cur.rowcount:
            log_event(dsn, "run_report", run_id=rep.run_id, status=rep.status)
            new.append(rep)
    return new


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.3f}"


def run_message(r: RunReport) -> str:
    when = r.finished_at.strftime("%a %d %b %H:%M UTC")
    if r.status == "error":
        return (
            f"⚠️ <b>Sunday training failed</b> at step <b>{H(r.failed_step or '?')}</b> ({when}).\n"
            f"<code>{H((r.error or '')[:300])}</code>\nThe current model keeps running."
        )
    sc = ""
    if r.challenger and r.champion:
        sc = (
            f"\n<pre>              new    now\nbalanced acc  {_pct(r.challenger.balanced_accuracy)}  "
            f"{_pct(r.champion.balanced_accuracy)}\nAUC           {_pct(r.challenger.roc_auc)}  "
            f"{_pct(r.champion.roc_auc)}\ntest posts    {r.challenger.n or 0:>5}  {r.champion.n or 0:>5}</pre>"
        )
    extra = "\n(market data from an older snapshot: the data platform was not reachable)" if r.market_fallback else ""
    live = f"\nLive check: {H(r.live)}" if r.live else ""
    if r.status == "pass":
        return (
            f"🚀 <b>Sunday training: a better model passed</b> ({when}). The approval message follows.{sc}{live}{extra}"
        )
    if r.status == "baseline":
        return f"🧪 <b>Sunday training: baseline packaged</b> ({when}).{sc}{extra}"
    gates = ", ".join(r.failed_gates) or "?"
    return (
        f"📉 <b>Sunday training: no improvement this week</b> ({when}). Failed checks: {H(gates)}. "
        f"The current model keeps running; nothing to do.{sc}{live}{extra}"
    )


def weekly_report(dsn: str, public_url: str | None) -> str:
    from intel.serve.measure import live_summary

    with connect(dsn) as c:
        act = c.execute(
            """select distinct on (a.name) a.name, a.activated_at, p.version from model_activation a
               join model_package p on p.id = a.package_id order by a.name, a.id desc"""
        ).fetchall()
        week = c.execute(
            """select (select count(*) from ledger_post where first_seen_at > now() - interval '7 days') as scored,
                      (select count(*) from post_outcome where recorded_at > now() - interval '7 days') as outcomes,
                      (select count(*) from approval where decision is null and expires_at > now()) as pending"""
        ).fetchone()
        last = c.execute("select * from training_run order by received_at desc limit 1").fetchone()
    live = live_summary(dsn, "7d")
    lines = ["📊 <b>Weekly report</b>"]
    lines += [f"• In use: {H(a['name'])} <code>{H(a['version'])}</code> since {a['activated_at']:%d %b}" for a in act]
    lines.append(f"• Last 7 days: {week['scored']} new posts scored, {week['outcomes']} results recorded")
    if live["models"]:
        names = {"content_scorer": "New model", "harness_predictor": "Current predictor"}
        lines.append(
            "• Right after 7 days (all time): "
            + " · ".join(
                f"{names.get(n, n)} {m['accuracy']:.0%} of {m['n']}" for n, m in sorted(live["models"].items())
            )
        )
    else:
        lines.append("• No 7-day results yet")
    if last:
        lines.append(
            f"• Last training: {last['received_at']:%a %d %b}, {H(last['status'])}"
            + (f" at {H(last['failed_step'])}" if last["failed_step"] else "")
        )
    else:
        lines.append("• No Sunday training received yet")
    if week["pending"]:
        lines.append(f"• ⏳ {week['pending']} model(s) waiting for your decision")
    if public_url:
        lines.append(f'\n<a href="{H(public_url)}/posts">See every post</a>')
    return "\n".join(lines)


def reminder_due(dsn: str, now: dt.datetime) -> bool:
    with connect(dsn) as c:
        r = c.execute(
            """select coalesce((select max(received_at) from training_run),
                               (select min(at) from event_log where kind = 'api_started')) as since"""
        ).fetchone()
    return r["since"] is not None and now - r["since"] > REMIND_AFTER


def report_tick(dsn: str, tg, public_url: str | None, now: dt.datetime | None = None) -> list[str]:
    """Called every few minutes. Sends at most one weekly report per ISO week (Monday from 09:00 Berlin) and at most
    one reminder per day. Returns what was sent (for tests)."""
    from zoneinfo import ZoneInfo

    now = now or dt.datetime.now(dt.UTC)
    local = now.astimezone(ZoneInfo("Europe/Berlin"))
    sent = []
    week_key = f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"
    if local.isoweekday() == 1 and local.hour >= 9 and kv_get(dsn, "weekly_report_sent") != week_key:
        tg.send(weekly_report(dsn, public_url))
        kv_set(dsn, "weekly_report_sent", week_key)
        sent.append("weekly_report")
    day_key = local.date().isoformat()
    if local.hour >= 10 and kv_get(dsn, "reminder_sent") != day_key and reminder_due(dsn, now):
        tg.send(
            "⏰ <b>The Mac has not trained for 14 days.</b> The VPS keeps serving the current model, but it is "
            "not learning from new posts. Wake the Mac on a Sunday, or run <code>intel weekly</code> by hand."
        )
        kv_set(dsn, "reminder_sent", day_key)
        sent.append("reminder")
    return sent
