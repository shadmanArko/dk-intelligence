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
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from intel.serve.db import connect, kv_get, kv_set, log_event, one

if TYPE_CHECKING:
    from intel.serve.telegram import Telegram

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


class ModelResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(max_length=40)
    version: str | None = Field(default=None, max_length=80)
    verdict: str = Field(max_length=20)
    new: float | None = None
    current: float | None = None
    failed_gates: list[str] = Field(default_factory=list, max_length=20)


class DataQuality(BaseModel):
    """Summary of the data checks of one snapshot (ADR 0017); the full report stays on the Mac."""

    model_config = ConfigDict(extra="ignore")
    snapshot: str = Field(max_length=40)
    tables: int = 0
    errors: list[str] = Field(default_factory=list, max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=10)


class Skew(BaseModel):
    """Training/serving skew check of the run (ADR 0018)."""

    model_config = ConfigDict(extra="ignore")
    checked: int = 0
    mismatches: int = 0
    examples: list[str] = Field(default_factory=list, max_length=3)


class KnowledgePack(BaseModel):
    """The Sunday knowledge-pack step (P4.4/P4.6, ADR 0027): a soft step, its result never fails the run."""

    model_config = ConfigDict(extra="ignore")
    status: Literal["active", "kept_previous", "skipped", "error"]
    pack: str | None = Field(default=None, max_length=60)
    chunks: int | None = None
    newly_embedded: int | None = None
    gate_passed: bool | None = None
    gate_lost: list[str] | None = Field(default=None, max_length=50)
    why: str | None = Field(default=None, max_length=200)
    error: str | None = Field(default=None, max_length=300)


def knowledge_line(k: KnowledgePack | None) -> str | None:
    if k is None:
        return None
    if k.status == "active":
        new = f", {k.newly_embedded:,} new texts embedded" if k.newly_embedded else ""
        return f"📚 Knowledge pack <code>{H(k.pack or '?')}</code> is live (gate passed{new})"
    if k.status == "kept_previous":
        lost = ", ".join(k.gate_lost or []) or "?"
        return f"📚 Knowledge pack refused by its gate (lost {H(lost)}): last week's stays live"
    if k.status == "skipped":
        return f"📚 Knowledge pack skipped: {H(k.why or '?')}"
    return f"⚠️ Knowledge pack step failed: <code>{H((k.error or '')[:150])}</code> (last week's stays live)"


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
    models: list[ModelResult] = Field(default_factory=list, max_length=10)
    data_quality: dict[Literal["warehouse", "market"], DataQuality] = Field(default_factory=dict)
    skew: Skew | None = None
    references: list[str] = Field(default_factory=list, max_length=10)
    knowledge: KnowledgePack | None = None


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
    extra = (
        "\n(market data from an older snapshot: the newest was unreachable or failed its data checks)"
        if r.market_fallback
        else ""
    )
    if r.skew and r.skew.mismatches:
        extra += (
            f"\n⚠️ Training/serving skew: {r.skew.mismatches} of {r.skew.checked} served posts got different "
            f"features than training computes now, e.g. {H((r.skew.examples or ['?'])[0][:150])}"
        )
    warns = [w for q in r.data_quality.values() for w in q.warnings]
    if warns:
        extra += f"\n🧪 Data checks: {len(warns)} warning(s), e.g. {H(warns[0][:150])}"
    if (kline := knowledge_line(r.knowledge)) is not None:
        extra += "\n" + kline
    live = f"\nLive check: {H(r.live)}" if r.live else ""
    if r.models:
        sc += "\n" + "\n".join(
            f"{'✅' if m.verdict == 'pass' else '➖'} {H(m.name)}: {H(m.verdict)} (new {m.new} vs now {m.current})"
            + (f": failed {H(', '.join(m.failed_gates))}" if m.failed_gates else "")
            for m in r.models
        )
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
        week = one(
            c.execute(
                """select (select count(*) from ledger_post where first_seen_at > now() - interval '7 days') as scored,
                      (select count(*) from post_outcome where recorded_at > now() - interval '7 days') as outcomes,
                      (select count(*) from approval where decision is null and expires_at > now()) as pending"""
            )
        )
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
    if last and (kline := knowledge_line(_knowledge_of(last))) is not None:
        lines.append("• " + kline)
    if week["pending"]:
        lines.append(f"• ⏳ {week['pending']} model(s) waiting for your decision")
    if public_url:
        lines.append(f'\n<a href="{H(public_url)}/posts">See every post</a>')
    return "\n".join(lines)


def _knowledge_of(row: dict[str, Any]) -> KnowledgePack | None:
    report = row.get("report")
    data = json.loads(report) if isinstance(report, str) else report
    try:
        return RunReport.model_validate(data).knowledge if data else None
    except ValidationError:
        return None


def reminder_due(dsn: str, now: dt.datetime) -> bool:
    with connect(dsn) as c:
        r = one(
            c.execute(
                """select coalesce((select max(received_at) from training_run),
                               (select min(at) from event_log where kind = 'api_started')) as since"""
            )
        )
    return r["since"] is not None and now - r["since"] > REMIND_AFTER


def coming_up_lines(bundle: dict[str, Any], days: int = 14) -> list[str]:
    """The occasion planner's next 2 weeks, for the Monday report (labels already follow the content rules)."""
    ins = bundle["insights"]
    limit = (dt.date.fromisoformat(ins["from"]) + dt.timedelta(days=days)).isoformat()
    lines = [
        f"• {H(p['label'])} ({H(p['community_id'])}, {p['people']:,} Berliners) on {p['first_day']}: "
        f"start posting {p['start_posting']}"
        for p in ins["picks"]
        if p["first_day"] <= limit
    ]
    lines += [
        f"• {H(n['day'])}: {H(n['label'])} ({H(n['community'])}): respectful note only"
        for n in ins["respectful_notes"]
        if n["day"] <= limit
    ]
    return lines


def report_tick(
    dsn: str,
    tg: Telegram,
    public_url: str | None,
    now: dt.datetime | None = None,
    coming_up: list[str] | None = None,
) -> list[str]:
    """Called every few minutes. Sends at most one weekly report per ISO week (Monday from 09:00 Berlin) and at most
    one reminder per day. Returns what was sent (for tests)."""
    from zoneinfo import ZoneInfo

    now = now or dt.datetime.now(dt.UTC)
    local = now.astimezone(ZoneInfo("Europe/Berlin"))
    sent = []
    week_key = f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"
    if local.isoweekday() == 1 and local.hour >= 9 and kv_get(dsn, "weekly_report_sent") != week_key:
        text = weekly_report(dsn, public_url)
        if coming_up:
            text += "\n\n<b>Coming up (next 2 weeks)</b>\n" + "\n".join(coming_up[:8])
        tg.send(text)
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


# ---------- live progress of the Sunday run (weekly-<run_id>.status.json, re-uploaded after every step) ----------

from intel.pipeline import STEPS as _PIPELINE_STEPS  # noqa: E402

STEP_KEYS = tuple(_PIPELINE_STEPS)
STALLED_AFTER = dt.timedelta(hours=2)


class StepState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = Field(max_length=60)
    status: Literal["pending", "running", "done", "failed", "skipped"]
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    note: str | None = Field(default=None, max_length=300)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=40)


class StatusFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["weekly_status"]
    run_id: str = Field(pattern=r"^\d{8}T\d{6}Z$")
    state: Literal["running", "finished"]
    result: Literal["pass", "fail", "baseline", "error"] | None = None
    seq: int = Field(ge=0)
    started_at: dt.datetime
    updated_at: dt.datetime
    current_step: str | None = Field(default=None, max_length=20)
    steps: dict[str, StepState]

    @field_validator("steps")
    @classmethod
    def _known_steps(cls, v: dict[str, Any]) -> dict[str, Any]:
        from intel.pipeline import STEPS

        unknown = set(v) - set(STEPS)
        if unknown:
            raise ValueError(f"unknown steps {sorted(unknown)}")
        return v


def scan_status(dsn: str, inbox: Path) -> bool:
    """Keep the newest status (latest run_id, highest seq) in kv 'weekly_progress'. Returns True if it changed."""
    files = sorted(inbox.glob("weekly-*.status.json"))
    if not files:
        return False
    try:
        data = files[-1].read_bytes()
        if len(data) > MAX_BYTES:
            return False
        st = StatusFile.model_validate(json.loads(data))
    except (OSError, ValueError, ValidationError):
        return False
    cur = kv_get(dsn, "weekly_progress")
    if cur and (cur["run_id"], cur["seq"]) >= (st.run_id, st.seq):
        return False
    kv_set(dsn, "weekly_progress", json.loads(st.model_dump_json()))
    return True


def training_overview(dsn: str, now: dt.datetime | None = None) -> dict[str, Any]:
    from intel.weekly import RETRY_AFTER, last_sunday_noon

    now = now or dt.datetime.now(dt.UTC)
    prog = kv_get(dsn, "weekly_progress")
    with connect(dsn) as c:
        runs = c.execute(
            "select run_id, status, failed_step, version, report, received_at from training_run "
            "order by run_id desc limit 12"
        ).fetchall()
    anchor = last_sunday_noon(now)
    ok = [r for r in runs if r["status"] != "error" and not r["report"].get("dry_run")]
    last_ok = dt.datetime.fromisoformat(ok[0]["report"]["finished_at"]) if ok else None
    stalled = None
    if prog and prog["state"] == "running":
        age = now - dt.datetime.fromisoformat(prog["updated_at"])
        if age > STALLED_AFTER:
            stalled = age
    if last_ok and last_ok >= anchor:
        this_week = f"done ({last_ok:%a %d %b %H:%M} UTC)"
    elif prog and prog["state"] == "running" and not stalled:
        this_week = "running now"
    else:
        this_week = f"due since {anchor:%a %d %b %H:%M} UTC: starts within an hour of the Mac being on"
    return {
        "progress": prog,
        "stalled": stalled,
        "runs": runs,
        "last_ok": last_ok,
        "this_week": this_week,
        "next_anchor": anchor + dt.timedelta(days=7),
        "retry_after_h": RETRY_AFTER.seconds // 3600,
    }


def training_text(dsn: str) -> str:
    o = training_overview(dsn)
    icons = {"done": "✅", "running": "⏳", "failed": "❌", "skipped": "➖", "pending": "▫️"}
    lines = [f"<b>Sunday training</b>\nThis week: {H(o['this_week'])}"]
    p = o["progress"]
    if p:
        head = "running" if p["state"] == "running" else f"finished: {p.get('result')}"
        lines.append(f"\n<b>Run {H(p['run_id'])}</b> ({H(head)})")
        for k in STEP_KEYS:
            st = p["steps"].get(k)
            if st:
                note = f": {H(st['note'])}" if st.get("note") else ""
                lines.append(f"{icons.get(st['status'], '?')} {H(st['label'])}{note}")
        if o["stalled"]:
            lines.append(
                f"⚠️ No update for {int(o['stalled'].total_seconds() // 3600)} h: the Mac may be asleep or "
                "the run stopped. It resumes automatically when due."
            )
    lines.append(f"\nNext regular run: {o['next_anchor']:%a %d %b} 12:00 Berlin")
    return "\n".join(lines)
