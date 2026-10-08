"""intel-api: scoring, approval page and background workers (inbox watcher, Telegram polling, expiry).

Network exposure (see deploy/): published on 127.0.0.1 only; `tailscale serve` makes the page reachable from the
owner's tailnet devices over HTTPS and adds the Tailscale-User-Login header, which every web decision requires.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from intel.models.content_scorer.predict import predict as cs_predict
from intel.serve.db import connect, kv_get, kv_set, log_event, migrate
from intel.serve.guardrail import run_guardrail
from intel.serve.lifecycle import audit_verify, stage
from intel.serve.measure import Measurer, live_summary, log_api_prediction
from intel.serve.monitor import run_monitor, scan_references
from intel.serve.observability import JsonFormatter, Metrics, StateCollector, instrument
from intel.serve.registry import REASON_CODES, Registry, RegistryError, load_bundle
from intel.serve.runs import coming_up_lines, report_tick, run_message, scan_run_reports, scan_status, training_overview
from intel.serve.telegram import Bot, Telegram, approval_buttons, approval_text
from intel.serve.views import (
    BERLIN,
    audit_html,
    community_html,
    creators_html,
    funnel_html,
    insights_html,
    ledger_rows,
    planner_html,
    posts_html,
    training_html,
    try_form_html,
)
from intel.settings import Settings, get_settings

log = logging.getLogger("intel.api")
PROBE_DRAFT = {"platform": "instagram", "content_type": "reel", "caption": "Probe: kacchi biryani, slow-cooked dum"}
TOKEN_RE = re.compile(r"bot\d{6,12}:[A-Za-z0-9_-]{30,}")
H = html.escape


class PredictRequest(BaseModel):
    """Same contract as the harness predictor (predictor/app/schemas.py), so callers can switch by URL."""

    platform: Literal["facebook", "instagram"]
    content_type: str | None = None
    caption: str = Field(default="", max_length=5000)
    planned_posted_at: dt.datetime


class BatchRequest(BaseModel):
    drafts: list[PredictRequest] = Field(min_length=1, max_length=100)


def drafts_frame(drafts: list[PredictRequest]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "platform": d.platform,
                "content_type": d.content_type,
                "caption": d.caption,
                "posted_at": d.planned_posted_at,
            }
            for d in drafts
        ]
    )


# ---------- model holder ----------


def warm_up(bundle: dict[str, Any]) -> float | None:
    """One prediction for a fixed draft right after loading (imports, first model call): returns its seconds."""
    if bundle.get("kind", "scorer") != "scorer" or "pipeline" not in bundle:
        return None
    t0 = time.perf_counter()
    try:
        row = pd.DataFrame([{**PROBE_DRAFT, "posted_at": dt.datetime.now(dt.UTC)}])
        cs_predict(bundle, row)
    except Exception:
        log.exception("warm-up prediction failed")
        return None
    return round(time.perf_counter() - t0, 4)


class Models:
    """Active bundles in memory; reloaded when the active package of a name changes."""

    def __init__(self, registry: Registry, metrics: Metrics | None = None):
        self.reg, self._lock, self.metrics = registry, threading.Lock(), metrics
        self.loaded: dict[str, dict[str, Any]] = {}

    def refresh(self) -> None:
        active = self.reg.active()
        with self._lock:
            for name, pkg in active.items():
                if self.loaded.get(name, {}).get("package_id") != pkg["id"]:
                    bundle = load_bundle(pkg)
                    self.loaded[name] = {"package_id": pkg["id"], "version": pkg["version"], "bundle": bundle}
                    seconds = warm_up(bundle)  # pay the cold start here, not on the first user request
                    if seconds is not None and self.metrics is not None:
                        self.metrics.warmup.labels(name).set(seconds)
                    log.info("loaded %s %s (warm-up %s s)", name, pkg["version"], seconds)
            for gone in set(self.loaded) - set(active):
                del self.loaded[gone]

    def get(self, name: str) -> dict | None:
        with self._lock:
            return self.loaded.get(name)

    def names(self, kind: str = "scorer") -> list[str]:
        """Active models of a kind: 'scorer' (answers posts) or 'insights' (analysis shown on pages)."""
        with self._lock:
            return sorted(n for n, m in self.loaded.items() if m["bundle"].get("kind", "scorer") == kind)


@dataclass
class AppState:
    """Everything the routes and background workers share. Built once in create_app; workers start in lifespan."""

    settings: Settings
    dsn: str
    registry: Registry
    models: Models
    telegram: Telegram | None = None
    started_at: dt.datetime | None = None
    last_inbox_scan: dt.datetime | None = None
    last_measure: dict[str, Any] | None = None
    monitoring: dict[str, Any] | None = None
    guardrail: list[dict[str, Any]] | None = None
    metrics: Metrics = field(default_factory=Metrics)


def guard(state: AppState) -> None:
    """The guardrail after each monitoring pass (ADR 0020); a rollback reloads the models immediately."""
    tg = state.telegram
    decisions = run_guardrail(
        state.dsn,
        state.registry,
        state.monitoring,
        state.settings.intel_auto_rollback,
        notify=tg.send if tg else None,
    )
    state.guardrail = [asdict(d) for d in decisions]
    for d in decisions:
        if d.action != "none":
            state.metrics.guardrail_actions.labels(d.model, d.action).inc()
    if any(d.action == "rollback" for d in decisions):
        state.models.refresh()


def active_scorers(models: Models) -> dict[str, str]:
    """name -> version of the active models that score posts (insights models are not monitored this way)."""
    return {n: str(m["version"]) for n in models.names() if (m := models.get(n)) is not None}


# ---------- background workers ----------


def notify_intake(bot_tg: Telegram | None, reg: Registry, public_url: str | None, intakes) -> None:
    for it in intakes:
        if bot_tg is None:
            continue
        try:
            if it.status == "auto_approved":
                bot_tg.send(
                    f"🔄 <b>{H(it.name)}</b> refreshed automatically (<code>{H(it.version)}</code>): it passed "
                    "every check and you approved this insight before. Undo with /rollback."
                )
            elif it.approval_id:
                a = reg.approval(it.approval_id)
                if a is None:
                    continue
                mid = bot_tg.send(
                    approval_text(a["metadata"], it.approval_id, public_url), approval_buttons(it.approval_id)
                )
                reg.set_telegram_message(it.approval_id, mid)
            elif it.status in ("invalid", "incompatible"):
                bot_tg.send(
                    f"⚠️ Package <code>{H(it.version)}</code> was refused ({H(it.status)}):\n"
                    + "\n".join(f"• {H(p)}" for p in it.problems[:5])
                )
            # test_only packages are recorded silently
        except Exception:
            log.exception("telegram notify failed")


def inbox_loop(state: AppState, stop: threading.Event) -> None:
    s: Settings = state.settings
    while not stop.is_set():
        try:
            scan_status(state.dsn, s.intel_inbox_dir)
            reports = scan_run_reports(state.dsn, s.intel_inbox_dir)  # before packages: summary first
            for rep in reports:
                if state.telegram:
                    state.telegram.send(run_message(rep))
            if scan_references(state.dsn, s.intel_inbox_dir):  # new reference profile (ADR 0018): judge again now
                state.monitoring = run_monitor(state.dsn, active_scorers(state.models))
            intakes = state.registry.scan_inbox()
            notify_intake(state.telegram, state.registry, s.intel_public_url, intakes)
            for row in state.registry.expire_due():
                if state.telegram and row["telegram_message_id"]:
                    state.telegram.edit(
                        row["telegram_message_id"], "⌛ Expired without a decision. The current model keeps running."
                    )
            state.models.refresh()
            state.last_inbox_scan = dt.datetime.now(dt.UTC)
            state.metrics.worker_ok("inbox")
        except Exception:
            state.metrics.worker_failed("inbox")
            log.exception("inbox loop error")
        stop.wait(s.intel_inbox_interval_s)


def telegram_loop(state: AppState, stop: threading.Event) -> None:
    s: Settings = state.settings
    tg = state.telegram
    if tg is None:
        return
    bot = Bot(tg, state.registry, s.intel_public_url, on_change=state.models.refresh)
    offset = kv_get(state.dsn, "telegram_offset")
    while not stop.is_set():
        try:
            for u in tg.updates(offset, timeout=25):
                offset = u["update_id"] + 1
                kv_set(state.dsn, "telegram_offset", offset)  # before handling: never replay an action
                bot.handle(u)
            state.metrics.worker_ok("telegram")  # one long poll (<= 25 s) finished cleanly
        except Exception:
            state.metrics.worker_failed("telegram")
            log.exception("telegram loop error")
            stop.wait(10)


def measure_loop(state: AppState, stop: threading.Event) -> None:
    s: Settings = state.settings
    if not s.intel_warehouse_dsn:
        return
    m = Measurer(state.dsn, s.intel_warehouse_dsn, s.intel_ledger_start, state.models, s.harness_predictor_url)
    stop.wait(20)  # let the inbox loop load the active model first
    while not stop.is_set():
        try:
            r = m.run_once()
            state.last_measure = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), **r.__dict__}
            state.metrics.predictions.labels("all", "shadow").inc(r.predictions)
            state.monitoring = run_monitor(state.dsn, active_scorers(state.models))
            guard(state)
            state.metrics.worker_ok("measure")
            if r.new_posts or r.outcomes or r.errors:
                log_event(state.dsn, "measure_run", **r.__dict__)
        except Exception:
            state.metrics.worker_failed("measure")
            log.exception("measure loop error")
            state.last_measure = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "failed": True}
        stop.wait(s.intel_measure_interval_s)


def retention_loop(state: AppState, stop: threading.Event) -> None:
    """Daily: delete what the data-protection policy no longer allows us to keep (ADR 0024); audited in the same
    transaction. Its staleness alert (IntelWorkerStalled, worker=retention) says when deletion has stopped."""
    from intel.privacy.retention import run_retention

    stop.wait(60)
    while not stop.is_set():
        try:
            for p in run_retention(state.dsn):
                if p.deleted:
                    state.metrics.retention_deleted.labels(p.table).inc(p.deleted)
                    log.info("retention: %s deleted %d rows", p.rule, p.deleted)
            state.metrics.worker_ok("retention")
        except Exception:
            state.metrics.worker_failed("retention")
            log.exception("retention loop error")
        stop.wait(state.settings.intel_retention_interval_s)


def report_loop(state: AppState, stop: threading.Event) -> None:
    s: Settings = state.settings
    tg = state.telegram
    if tg is None:
        return
    while not stop.is_set():
        try:
            op = state.models.get("occasion_planner")
            report_tick(
                state.dsn,
                tg,
                s.intel_public_url,
                coming_up=coming_up_lines(op["bundle"]) if op else None,
            )
            state.metrics.worker_ok("reports")
        except Exception:
            state.metrics.worker_failed("reports")
            log.exception("report loop error")
        stop.wait(300)


# ---------- app ----------


def create_app(settings: Settings | None = None, start_workers: bool = True) -> FastAPI:
    s = settings or get_settings()
    if not s.intel_db_dsn:
        raise RuntimeError("INTEL_DB_DSN is not set")
    registry = Registry(s.intel_db_dsn, s.intel_inbox_dir, s.intel_store_dir, s.intel_approval_days)
    app_metrics = Metrics()
    state = AppState(
        settings=s, dsn=s.intel_db_dsn, registry=registry, models=Models(registry, app_metrics), metrics=app_metrics
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for attempt in range(30):  # the database container may still be starting
            try:
                applied = migrate(state.dsn)
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(2)
        if applied:
            log.info("migrations applied: %s", applied)
        s.intel_store_dir.mkdir(parents=True, exist_ok=True)
        state.models.refresh()
        if s.telegram_bot_token and s.telegram_chat_id:
            state.telegram = Telegram(s.telegram_bot_token, s.telegram_chat_id)
        stop = threading.Event()
        threads = []
        if start_workers:
            threads.append(threading.Thread(target=inbox_loop, args=(state, stop), daemon=True, name="inbox"))
            threads.append(threading.Thread(target=retention_loop, args=(state, stop), daemon=True, name="retention"))
            if s.intel_warehouse_dsn:
                threads.append(threading.Thread(target=measure_loop, args=(state, stop), daemon=True, name="measure"))
            if state.telegram:
                threads.append(threading.Thread(target=report_loop, args=(state, stop), daemon=True, name="reports"))
            if state.telegram and s.telegram_polling:
                threads.append(threading.Thread(target=telegram_loop, args=(state, stop), daemon=True, name="telegram"))
            for t in threads:
                t.start()
        log_event(state.dsn, "api_started", workers=[t.name for t in threads])
        state.started_at = dt.datetime.now(dt.UTC)
        app.state.intel = state
        yield
        stop.set()

    app = FastAPI(title="dk-intelligence", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    state.metrics.registry.register(StateCollector(state))
    instrument(app, state.metrics)

    # ----- health -----
    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        with connect(state.dsn) as c:
            c.execute("select 1")
        return {
            "status": "ready",
            "version": s.intel_git_sha,
            "models": {n: m["version"] for n, m in state.models.loaded.items()},
        }

    @app.get("/health/deep")
    def health_deep():
        """What the prober calls every 30 s (ADR 0016): database + a real prediction for a fixed draft. Nothing is
        written to the ledger, unlike /v1/predict."""
        t0 = time.perf_counter()
        with connect(state.dsn) as c:
            c.execute("select 1")
        m = state.models.get("content_scorer")
        prediction = None
        if m is not None:
            row = pd.DataFrame([{**PROBE_DRAFT, "posted_at": dt.datetime.now(dt.UTC) + dt.timedelta(days=1)}])
            p = cs_predict(m["bundle"], row)[0]["probability"]
            if not 0 <= p <= 1:
                raise HTTPException(500, "content_scorer returned a probability outside [0, 1]")
            prediction = {"model": m["version"], "probability": round(float(p), 4)}
        return {
            "status": "ok",
            "version": s.intel_git_sha,
            "models": {n: x["version"] for n, x in state.models.loaded.items()},
            "prediction": prediction,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    @app.get("/v1/monitoring")
    def monitoring():
        """Drift, live performance and calibration of every active scorer (ADR 0018). The Mac's hourly job reads
        `retrain_requested` to train early."""
        if state.monitoring is None:  # not yet computed in this process: compute now (cheap)
            state.monitoring = run_monitor(state.dsn, active_scorers(state.models))
        return {**state.monitoring, "guardrail": {"mode": s.intel_auto_rollback, "decisions": state.guardrail or []}}

    @app.get("/metrics")
    def metrics():
        from fastapi.responses import Response
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(generate_latest(state.metrics.registry), media_type=CONTENT_TYPE_LATEST)

    # ----- scoring: same contract as the harness predictor -----
    def _score(body: PredictRequest) -> dict:
        m = state.models.get("content_scorer")
        if m is None:
            raise HTTPException(503, "no approved content_scorer model is active yet")
        if body.planned_posted_at.tzinfo is None:
            raise HTTPException(400, "planned_posted_at needs a timezone")
        with state.metrics.stage("prepare"):
            row = drafts_frame([body])
        with state.metrics.stage("predict"):
            return cs_predict(m["bundle"], row)[0]

    @app.post("/v1/predict")
    def predict(body: PredictRequest):
        r = _score(body)
        state.metrics.predictions.labels("content_scorer", "api").inc()
        try:
            with state.metrics.stage("ledger"):
                log_api_prediction(state.dsn, body, r)
        except Exception:
            log.exception("could not log api prediction")
        return {
            "label": "likely_at_or_above_typical" if r["label"] else "likely_below_typical",
            "probability": r["probability"],
            "threshold": r["threshold"],
            "model_version": r["model_version"],
            "top_reasons": r["top_reasons"],
        }

    def _score_all(body: PredictRequest) -> dict:
        row = pd.DataFrame(
            [
                {
                    "platform": body.platform,
                    "content_type": body.content_type,
                    "caption": body.caption,
                    "posted_at": body.planned_posted_at,
                }
            ]
        )
        active = {n: state.models.get(n) for n in state.models.names()}
        return {n: cs_predict(m["bundle"], row)[0] for n, m in active.items() if m is not None}

    @app.post("/v1/score/batch")
    def score_batch(body: BatchRequest):
        """Up to 100 drafts in one call, every active scorer, vectorised (ADR 0023): for planners and tools comparing
        many variants. Exploratory: not written to the ledger (only /v1/predict calls are)."""
        if any(d.planned_posted_at.tzinfo is None for d in body.drafts):
            raise HTTPException(400, "planned_posted_at needs a timezone")
        with state.metrics.stage("prepare"):
            frame = drafts_frame(body.drafts)
        out: dict[str, list[dict[str, Any]]] = {}
        for name in state.models.names():
            m = state.models.get(name)
            if m is None:
                continue
            with state.metrics.stage("predict"):
                out[name] = cs_predict(m["bundle"], frame)
            state.metrics.predictions.labels(name, "batch").inc(len(body.drafts))
        return {"drafts": len(body.drafts), "models": out}

    @app.post("/v1/score")
    def score(body: PredictRequest):
        """Every active model's answer (content_scorer: engagement question, reach_scorer: reach question)."""
        if body.planned_posted_at.tzinfo is None:
            raise HTTPException(400, "planned_posted_at needs a timezone")
        answers = _score_all(body)
        for name in answers:
            state.metrics.predictions.labels(name, "score").inc()
        return {"models": answers}

    # ----- ledger (read-only; tailnet) -----
    @app.get("/v1/ledger/summary")
    def ledger_summary(horizon: Literal["24h", "72h", "7d"] = "7d"):
        return {**live_summary(state.dsn, horizon), "last_measure": state.last_measure}

    @app.get("/v1/ledger/export")
    def ledger_export():
        """Every shadow prediction with its outcomes (no captions), for the Sunday training run on the Mac."""
        with connect(state.dsn) as c:
            rows = c.execute(
                """select p.post_id, p.platform, p.planned_at, p.model_name, p.model_version, p.probability,
                          p.threshold, p.predicted_label, p.created_at, p.features,
                          coalesce(jsonb_object_agg(o.horizon, jsonb_build_object('label', o.label, 'engagement',
                            o.engagement, 'reach', o.reach, 'age_hours', o.age_hours, 'platform_median',
                            o.platform_median, 'label_reach', o.label_reach, 'reach_median', o.reach_median)) filter (where o.horizon is not null), '{}') as outcomes
                   from prediction p left join post_outcome o on o.post_id = p.post_id
                   where p.source = 'shadow' group by p.id order by p.planned_at"""
            ).fetchall()
        return {"rows": rows}

    # ----- web approval page -----
    def _who(request: Request) -> str:
        login = request.headers.get("tailscale-user-login")
        if s.intel_require_tailscale and not login:
            raise HTTPException(403, "decisions are only accepted through the tailnet (tailscale serve)")
        return login or "local"

    def _csrf(request: Request, token: str) -> None:
        if not token or not secrets.compare_digest(token, request.cookies.get("intel_csrf", "")):
            raise HTTPException(403, "form expired, reload the page")
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            raise HTTPException(403, "cross-site request refused")

    def _page(request: Request, title: str, body: str, scripts: bool = False) -> HTMLResponse:
        token = request.cookies.get("intel_csrf") or secrets.token_urlsafe(24)
        page = PAGE.format(title=H(title), body=body.replace("{csrf}", H(token)))
        resp = HTMLResponse(page)
        resp.set_cookie("intel_csrf", token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        csp = "default-src 'none'; style-src 'unsafe-inline' 'self'; form-action 'self'"
        if scripts:  # only pages that need the live diagram may run our own (same-origin) script
            csp += "; script-src 'self'; connect-src 'self'"
        resp.headers["Content-Security-Policy"] = csp
        return resp

    @app.get("/posts", response_class=HTMLResponse)
    def posts_page(request: Request):
        summaries = {h: live_summary(state.dsn, h) for h in ("24h", "72h", "7d")}
        return _page(request, "Posts", posts_html(ledger_rows(state.dsn, s.intel_warehouse_dsn), summaries))

    @app.get("/training", response_class=HTMLResponse)
    def training_page(request: Request):
        deployed = {"sha": s.intel_git_sha, "started_at": state.started_at}
        diagram = (
            '<link rel=stylesheet href="/static/pipeline.css"><div id=pipeline class=pl-wrap '
            'data-def="/api/pipeline" data-state="/api/training-state"></div>'
            '<script src="/static/pipeline.js"></script>'
        )
        return _page(request, "Training", diagram + training_html(training_overview(state.dsn), deployed), scripts=True)

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request):
        """Lifecycle of every model package and the hash-chained audit trail (ADR 0019)."""
        return _page(request, "Audit", audit_html(*_audit_data(limit=300)))

    @app.get("/v1/audit")
    def audit_export(limit: int = 1000):
        chain, packages, entries = _audit_data(limit=min(max(limit, 1), 10_000))
        return {"chain": chain, "packages": packages, "entries": entries}

    def _audit_data(limit: int) -> tuple[dict, list[dict], list[dict]]:
        with connect(state.dsn) as c:
            chain = audit_verify(c)
            active = {
                r["package_id"]
                for r in c.execute("select distinct on (name) package_id from model_activation order by name, id desc")
            }
            packages = [
                {**r, "stage": stage(r["status"], r["id"] in active)}
                for r in c.execute(
                    "select id, name, version, purpose, verdict, status, received_at from model_package order by id desc"
                )
            ]
            entries = c.execute(
                "select id, at, actor, channel, action, subject, detail, hash from audit_log order by id desc limit %s",
                (limit,),
            ).fetchall()
        return chain, packages, entries

    @app.get("/static/{name}")
    def static_asset(name: str):
        from fastapi.responses import FileResponse

        types = {"pipeline.js": "text/javascript", "pipeline.css": "text/css"}
        if name not in types:
            raise HTTPException(404)
        return FileResponse(Path(__file__).parents[1] / "static" / name, media_type=types[name])

    @app.get("/api/pipeline")
    def api_pipeline():
        from intel.pipeline import definition

        return definition()

    @app.get("/api/training-state")
    def api_training_state():
        o = training_overview(state.dsn)
        p = o["progress"]
        return {
            "progress": p,
            "running": bool(p and p["state"] == "running" and not o["stalled"]),
            "stalled": bool(o["stalled"]),
            "due_text": o["this_week"],
            "history": [
                {
                    "run_id": r["run_id"],
                    "status": r["status"],
                    "failed_step": r["failed_step"],
                    "new": (r["report"].get("challenger") or {}).get("balanced_accuracy"),
                    "current": (r["report"].get("champion") or {}).get("balanced_accuracy"),
                }
                for r in o["runs"]
            ],
        }

    @app.get("/insights", response_class=HTMLResponse)
    def insights_page(request: Request):
        cm, wm = state.models.get("community_map"), state.models.get("weakness_miner")
        op = state.models.get("occasion_planner")
        fn = state.models.get("funnel")
        crm = state.models.get("creator_matcher")
        sections = [
            planner_html(op["bundle"] if op else None),
            community_html(cm["bundle"] if cm else None),
            funnel_html(fn["bundle"] if fn else None),
            creators_html(crm["bundle"] if crm else None),
            insights_html(wm["bundle"] if wm else None),
        ]
        body = "<hr>".join(sections)
        return _page(request, "Insights", body)

    @app.get("/try", response_class=HTMLResponse)
    def try_page(request: Request):
        return _page(request, "Try a draft", try_form_html())

    @app.post("/try", response_class=HTMLResponse)
    def try_submit(
        request: Request,
        csrf: str = Form(""),
        platform: str = Form("instagram"),
        content_type: str = Form("reel"),
        when: str = Form(""),
        caption: str = Form(""),
    ):
        _csrf(request, csrf)
        form = {"platform": platform, "content_type": content_type, "when": when, "caption": caption}
        try:
            planned = dt.datetime.fromisoformat(when).replace(tzinfo=BERLIN)
            body = PredictRequest.model_validate(  # validated like an API call (ValidationError is a ValueError)
                {"platform": platform, "content_type": content_type, "caption": caption, "planned_posted_at": planned}
            )
        except ValueError as e:
            return _page(request, "Try a draft", try_form_html(form=form, error=f"Please check the inputs: {e}"))
        try:
            result = _score(body)
        except HTTPException as e:
            return _page(request, "Try a draft", try_form_html(form=form, error=str(e.detail)))
        state.metrics.predictions.labels("content_scorer", "try").inc()
        try:
            log_api_prediction(state.dsn, body, result)
        except Exception:
            log.exception("could not log api prediction")
        return _page(request, "Try a draft", try_form_html(result=result, form=form))

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        reg = state.registry
        act, pend = reg.active(), reg.pending()
        rows = (
            "".join(
                f"<tr><td>{H(n)}</td><td><code>{H(r['version'])}</code></td><td>{r['activated_at']:%Y-%m-%d %H:%M}</td>"
                f"<td>{H(r['reason'])} by {H(r['activated_by'])}</td><td><form method=post action='/models/{H(n)}/rollback'>"
                f"<input type=hidden name=csrf value='{{csrf}}'><button class=secondary>Roll back</button></form></td></tr>"
                for n, r in sorted(act.items())
            )
            or "<tr><td colspan=5>No model approved yet.</td></tr>"
        )
        pend_html = "".join(_approval_card(p) for p in pend) or "<p class=muted>Nothing is waiting for a decision.</p>"
        pk = "".join(
            f"<tr><td><a href='/packages/{p['id']}'><code>{H(p['version'])}</code></a></td><td>{H(p['purpose'])}</td>"
            f"<td>{H(p['verdict'])}</td><td><span class='tag {H(p['status'])}'>{H(p['status'])}</span></td>"
            f"<td>{p['received_at']:%Y-%m-%d %H:%M}</td><td>{H(p['decided_by'] or '')} {H(p['reason_code'] or '')}</td></tr>"
            for p in reg.packages()
        )
        scan = state.last_inbox_scan
        return _page(
            request,
            "DK Intelligence",
            f"""
<h1>Approvals</h1>
<p class=muted>Last inbox check: {f"{scan:%H:%M:%S} UTC" if scan else "not yet"}</p>
<h2>Waiting for your decision</h2>{pend_html}
<h2>Models in use</h2><table><tr><th>Model</th><th>Version</th><th>Since (UTC)</th><th>How</th><th></th></tr>{rows}</table>
<h2>All packages</h2><table><tr><th>Version</th><th>Purpose</th><th>Verdict</th><th>Status</th><th>Received</th>
<th>Decision</th></tr>{pk}</table>""",
        )

    def _approval_card(p: dict) -> str:
        m = p["metadata"]
        sc, ch = m["scores"]["challenger"], m["scores"]["champion"]
        gates = "".join(
            f"<li class={H(g['status'])}>{H(g['gate'])}: {H(g['detail'])}</li>" for g in m["judge"]["gates"]
        )
        reasons = "".join(f"<option value='{H(k)}'>{H(v)}</option>" for k, v in REASON_CODES.items())
        note = (
            "<p><b>Baseline:</b> the current predictor's recipe in the new format. No improvement claimed.</p>"
            if m["purpose"] == "baseline"
            else ""
        )
        return f"""<div class=card><h3>{H(p["name"])} <code>{H(p["version"])}</code></h3>{note}
<table><tr><th></th><th>New</th><th>Current</th></tr>
<tr><td>Balanced accuracy</td><td>{sc["balanced_accuracy"]}</td><td>{ch["balanced_accuracy"]}</td></tr>
<tr><td>AUC</td><td>{sc["roc_auc"]}</td><td>{ch["roc_auc"]}</td></tr>
<tr><td>Brier (lower is better)</td><td>{sc["brier"]}</td><td>{ch["brier"]}</td></tr>
<tr><td>Test posts</td><td>{sc["n"]}</td><td>{ch["n"]}</td></tr></table>
<ul class=gates>{gates}</ul><p class=muted>Expires {p["expires_at"]:%Y-%m-%d %H:%M} UTC ·
<a href='/packages/{p["id"]}'>model card</a></p>
<div class=actions><form method=post action='/approvals/{p["approval_id"]}/approve'>
<input type=hidden name=csrf value='{{csrf}}'><button>Approve</button></form>
<form method=post action='/approvals/{p["approval_id"]}/reject'><input type=hidden name=csrf value='{{csrf}}'>
<select name=reason>{reasons}</select><button class=secondary>Reject</button></form></div></div>"""

    @app.get("/approvals/{approval_id}", response_class=HTMLResponse)
    def approval_page(approval_id: int, request: Request):
        a = state.registry.approval(approval_id)
        if a is None:
            raise HTTPException(404)
        return RedirectResponse(f"/packages/{a['package_id']}", status_code=303)

    @app.get("/packages/{package_id}", response_class=HTMLResponse)
    def package_page(package_id: int, request: Request):
        p = state.registry.package(package_id)
        if p is None:
            raise HTTPException(404)
        card = ""
        if p["store_path"]:
            try:
                card = (state.settings.intel_store_dir / p["name"] / p["version"] / "model_card.md").read_text()
            except OSError:
                card = ""
        problems = "".join(f"<li>{H(x)}</li>" for x in p["problems"])
        return _page(
            request,
            p["version"],
            f"""<p><a href='/'>← back</a></p>
<h1>{H(p["name"])} <code>{H(p["version"])}</code></h1>
<p>Status <span class='tag {H(p["status"])}'>{H(p["status"])}</span> · purpose {H(p["purpose"])} ·
verdict {H(p["verdict"])} · file <code>{H(p["file_name"])}</code></p>
{f"<h2>Problems</h2><ul>{problems}</ul>" if problems else ""}
<h2>Model card</h2><pre>{H(card) or "not stored (package was not accepted)"}</pre>
<h2>Metadata</h2><pre>{H(json.dumps(p["metadata"], indent=2)[:20000])}</pre>""",
        )

    def _decide(fn, request: Request):
        try:
            fn()
        except RegistryError as e:
            raise HTTPException(409, str(e)) from e
        state.models.refresh()
        return RedirectResponse("/", status_code=303)

    @app.post("/approvals/{approval_id}/approve")
    def web_approve(approval_id: int, request: Request, csrf: str = Form("")):
        _csrf(request, csrf)
        who = _who(request)
        resp = _decide(lambda: state.registry.approve(approval_id, who, "web"), request)
        _sync_telegram(approval_id, "✅ <b>Approved on the web page</b>. Undo any time with /rollback.")
        return resp

    @app.post("/approvals/{approval_id}/reject")
    def web_reject(approval_id: int, request: Request, csrf: str = Form(""), reason: str = Form("other")):
        _csrf(request, csrf)
        who = _who(request)
        resp = _decide(lambda: state.registry.reject(approval_id, who, "web", reason), request)
        _sync_telegram(approval_id, f"❌ <b>Rejected on the web page</b> ({H(REASON_CODES.get(reason, reason))}).")
        return resp

    @app.post("/models/{name}/rollback")
    def web_rollback(name: str, request: Request, csrf: str = Form("")):
        _csrf(request, csrf)
        who = _who(request)
        return _decide(lambda: state.registry.rollback(name, who, "web"), request)

    def _sync_telegram(approval_id: int, text: str) -> None:
        tg, a = state.telegram, state.registry.approval(approval_id)
        if tg and a and a["telegram_message_id"]:
            tg.edit(a["telegram_message_id"], text)

    return app


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{title}</title><style>
:root{{--bg:#fbf8f3;--fg:#2a2118;--muted:#7a6c5d;--card:#fff;--line:#e8dfd2;--acc:#9b3d12;--ok:#2f7d32;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1713;--fg:#f1e9df;--muted:#a8998a;--card:#251f1a;--line:#3a3129;
--acc:#e08b5a;--ok:#7cc47f;--bad:#f2867e}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:16px}}
h1{{font-size:1.5rem}}h2{{font-size:1.1rem;margin-top:2rem}}a{{color:var(--acc)}}.muted{{color:var(--muted)}}
table{{border-collapse:collapse;width:100%;display:block;overflow-x:auto}}td,th{{border-bottom:1px solid var(--line);
padding:6px 8px;text-align:left;white-space:nowrap}}.card{{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:12px 16px;margin:12px 0}}button{{background:var(--acc);color:#fff;border:0;border-radius:6px;
padding:8px 14px;font-weight:600;cursor:pointer}}button.secondary{{background:transparent;color:var(--acc);
border:1px solid var(--acc)}}.actions{{display:flex;gap:12px;flex-wrap:wrap}}.actions form{{display:flex;gap:6px}}
select{{padding:6px;border-radius:6px}}pre{{background:var(--card);border:1px solid var(--line);padding:12px;
overflow-x:auto;white-space:pre-wrap}}.gates li.pass::marker{{content:"✅ "}}.gates li.fail::marker{{content:"❌ "}}
.gates li.pending::marker{{content:"⏳ "}}.tag{{padding:1px 8px;border-radius:999px;border:1px solid var(--line)}}
.tag.approved{{color:var(--ok)}}.tag.rejected,.tag.invalid,.tag.incompatible{{color:var(--bad)}}
.ok{{color:var(--ok)}}.bad{{color:var(--bad)}}.up{{color:var(--ok);white-space:nowrap}}.down{{color:var(--bad);white-space:nowrap}}
nav{{display:flex;gap:16px;border-bottom:1px solid var(--line);padding-bottom:8px;margin-bottom:8px;flex-wrap:wrap}}
nav b{{margin-right:auto}}textarea,input{{width:100%;box-sizing:border-box;padding:8px;border-radius:6px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);font:inherit}}label{{display:inline-block;margin:0 12px 8px 0}}
label input{{width:auto}}
</style></head><body><nav><b>DK Intelligence</b><a href="/">Approvals</a><a href="/posts">Posts</a>
<a href="/try">Try a draft</a><a href="/insights">Insights</a><a href="/training">Training</a><a href="/audit">Audit</a></nav>{body}</body></html>"""


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())  # one JSON object per line, with the request id (ADR 0016)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # httpx/httpcore log every request URL at INFO, and Telegram puts the bot token IN the URL. Never let them.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger().addFilter(_redact_tokens)
    for h in logging.getLogger().handlers:
        h.addFilter(_redact_tokens)


def _redact_tokens(record: logging.LogRecord) -> bool:
    """Last line of defence: mask anything shaped like a Telegram bot token in any log line."""
    msg = record.getMessage()
    if TOKEN_RE.search(msg):
        record.msg, record.args = TOKEN_RE.sub("bot<redacted>", msg), None
    return True


def main() -> None:
    import uvicorn

    configure_logging()
    uvicorn.run(  # access_log off: the middleware logs one structured line per request instead
        create_app(),
        host="0.0.0.0",  # noqa: S104
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
