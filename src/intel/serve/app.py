"""intel-api: scoring, approval page and background workers (inbox watcher, Telegram polling, expiry).

Network exposure (see deploy/): published on 127.0.0.1 only; `tailscale serve` makes the page reachable from the
owner's tailnet devices over HTTPS and adds the Tailscale-User-Login header, which every web decision requires.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from intel.models.content_scorer.predict import predict as cs_predict
from intel.serve.db import connect, kv_get, kv_set, log_event, migrate
from intel.serve.measure import Measurer, live_summary, log_api_prediction
from intel.serve.registry import REASON_CODES, Registry, RegistryError, load_bundle
from intel.serve.telegram import Bot, Telegram, approval_buttons, approval_text
from intel.settings import Settings, get_settings

log = logging.getLogger("intel.api")
H = html.escape


class PredictRequest(BaseModel):
    """Same contract as the harness predictor (predictor/app/schemas.py), so callers can switch by URL."""

    platform: Literal["facebook", "instagram"]
    content_type: str | None = None
    caption: str = Field(default="", max_length=5000)
    planned_posted_at: dt.datetime


# ---------- model holder ----------


class Models:
    """Active bundles in memory; reloaded when the active package of a name changes."""

    def __init__(self, registry: Registry):
        self.reg, self._lock, self.loaded = registry, threading.Lock(), {}

    def refresh(self) -> None:
        active = self.reg.active()
        with self._lock:
            for name, pkg in active.items():
                if self.loaded.get(name, {}).get("package_id") != pkg["id"]:
                    self.loaded[name] = {"package_id": pkg["id"], "version": pkg["version"], "bundle": load_bundle(pkg)}
                    log.info("loaded %s %s", name, pkg["version"])
            for gone in set(self.loaded) - set(active):
                del self.loaded[gone]

    def get(self, name: str) -> dict | None:
        with self._lock:
            return self.loaded.get(name)


# ---------- background workers ----------


def notify_intake(bot_tg: Telegram | None, reg: Registry, public_url: str | None, intakes) -> None:
    for it in intakes:
        if bot_tg is None:
            continue
        try:
            if it.approval_id:
                a = reg.approval(it.approval_id)
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


def inbox_loop(state: dict, stop: threading.Event) -> None:
    s: Settings = state["settings"]
    while not stop.is_set():
        try:
            intakes = state["registry"].scan_inbox()
            notify_intake(state.get("telegram"), state["registry"], s.intel_public_url, intakes)
            for row in state["registry"].expire_due():
                if state.get("telegram") and row["telegram_message_id"]:
                    state["telegram"].edit(
                        row["telegram_message_id"], "⌛ Expired without a decision. The current model keeps running."
                    )
            state["models"].refresh()
            state["last_inbox_scan"] = dt.datetime.now(dt.UTC)
        except Exception:
            log.exception("inbox loop error")
        stop.wait(s.intel_inbox_interval_s)


def telegram_loop(state: dict, stop: threading.Event) -> None:
    s: Settings = state["settings"]
    bot = Bot(state["telegram"], state["registry"], s.intel_public_url, on_change=state["models"].refresh)
    offset = kv_get(s.intel_db_dsn, "telegram_offset")
    while not stop.is_set():
        try:
            for u in state["telegram"].updates(offset, timeout=25):
                offset = u["update_id"] + 1
                kv_set(s.intel_db_dsn, "telegram_offset", offset)  # before handling: never replay an action
                bot.handle(u)
        except Exception:
            log.exception("telegram loop error")
            stop.wait(10)


def measure_loop(state: dict, stop: threading.Event) -> None:
    s: Settings = state["settings"]
    m = Measurer(s.intel_db_dsn, s.intel_warehouse_dsn, s.intel_ledger_start, state["models"], s.harness_predictor_url)
    stop.wait(20)  # let the inbox loop load the active model first
    while not stop.is_set():
        try:
            r = m.run_once()
            state["last_measure"] = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), **r.__dict__}
            if r.new_posts or r.outcomes or r.errors:
                log_event(s.intel_db_dsn, "measure_run", **r.__dict__)
        except Exception:
            log.exception("measure loop error")
            state["last_measure"] = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "failed": True}
        stop.wait(s.intel_measure_interval_s)


# ---------- app ----------


def create_app(settings: Settings | None = None, start_workers: bool = True) -> FastAPI:
    s = settings or get_settings()
    state: dict = {"settings": s}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not s.intel_db_dsn:
            raise RuntimeError("INTEL_DB_DSN is not set")
        for attempt in range(30):  # the database container may still be starting
            try:
                applied = migrate(s.intel_db_dsn)
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(2)
        if applied:
            log.info("migrations applied: %s", applied)
        s.intel_store_dir.mkdir(parents=True, exist_ok=True)
        state["registry"] = Registry(s.intel_db_dsn, s.intel_inbox_dir, s.intel_store_dir, s.intel_approval_days)
        state["models"] = Models(state["registry"])
        state["models"].refresh()
        if s.telegram_bot_token and s.telegram_chat_id:
            state["telegram"] = Telegram(s.telegram_bot_token, s.telegram_chat_id)
        stop = threading.Event()
        threads = []
        if start_workers:
            threads.append(threading.Thread(target=inbox_loop, args=(state, stop), daemon=True, name="inbox"))
            if s.intel_warehouse_dsn:
                threads.append(threading.Thread(target=measure_loop, args=(state, stop), daemon=True, name="measure"))
            if state.get("telegram") and s.telegram_polling:
                threads.append(threading.Thread(target=telegram_loop, args=(state, stop), daemon=True, name="telegram"))
            for t in threads:
                t.start()
        log_event(s.intel_db_dsn, "api_started", workers=[t.name for t in threads])
        app.state.intel = state
        yield
        stop.set()

    app = FastAPI(title="dk-intelligence", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    # ----- health -----
    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        with connect(s.intel_db_dsn) as c:
            c.execute("select 1")
        return {"status": "ready", "models": {n: m["version"] for n, m in state["models"].loaded.items()}}

    # ----- scoring: same contract as the harness predictor -----
    def _score(body: PredictRequest) -> dict:
        m = state["models"].get("content_scorer")
        if m is None:
            raise HTTPException(503, "no approved content_scorer model is active yet")
        if body.planned_posted_at.tzinfo is None:
            raise HTTPException(400, "planned_posted_at needs a timezone")
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
        return cs_predict(m["bundle"], row)[0]

    @app.post("/v1/predict")
    def predict(body: PredictRequest):
        r = _score(body)
        try:
            log_api_prediction(s.intel_db_dsn, body, r)
        except Exception:
            log.exception("could not log api prediction")
        return {
            "label": "likely_at_or_above_typical" if r["label"] else "likely_below_typical",
            "probability": r["probability"],
            "threshold": r["threshold"],
            "model_version": r["model_version"],
            "top_reasons": r["top_reasons"],
        }

    @app.post("/v1/score")
    def score(body: PredictRequest):
        return {"models": {"content_scorer": _score(body)}}

    # ----- ledger (read-only; tailnet) -----
    @app.get("/v1/ledger/summary")
    def ledger_summary(horizon: Literal["24h", "72h", "7d"] = "7d"):
        return {**live_summary(s.intel_db_dsn, horizon), "last_measure": state.get("last_measure")}

    @app.get("/v1/ledger/export")
    def ledger_export():
        """Every shadow prediction with its outcomes (no captions), for the Sunday training run on the Mac."""
        with connect(s.intel_db_dsn) as c:
            rows = c.execute(
                """select p.post_id, p.platform, p.planned_at, p.model_name, p.model_version, p.probability,
                          p.threshold, p.predicted_label, p.created_at,
                          coalesce(jsonb_object_agg(o.horizon, jsonb_build_object('label', o.label, 'engagement',
                            o.engagement, 'reach', o.reach, 'age_hours', o.age_hours, 'platform_median',
                            o.platform_median)) filter (where o.horizon is not null), '{}') as outcomes
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

    def _page(request: Request, title: str, body: str) -> HTMLResponse:
        token = request.cookies.get("intel_csrf") or secrets.token_urlsafe(24)
        page = PAGE.format(title=H(title), body=body.replace("{csrf}", H(token)))
        resp = HTMLResponse(page)
        resp.set_cookie("intel_csrf", token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        return resp

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        reg = state["registry"]
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
        scan = state.get("last_inbox_scan")
        return _page(
            request,
            "DK Intelligence",
            f"""
<h1>DK Intelligence</h1>
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
        a = state["registry"].approval(approval_id)
        if a is None:
            raise HTTPException(404)
        return RedirectResponse(f"/packages/{a['package_id']}", status_code=303)

    @app.get("/packages/{package_id}", response_class=HTMLResponse)
    def package_page(package_id: int, request: Request):
        p = state["registry"].package(package_id)
        if p is None:
            raise HTTPException(404)
        card = ""
        if p["store_path"]:
            try:
                card = (state["settings"].intel_store_dir / p["name"] / p["version"] / "model_card.md").read_text()
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
        state["models"].refresh()
        return RedirectResponse("/", status_code=303)

    @app.post("/approvals/{approval_id}/approve")
    def web_approve(approval_id: int, request: Request, csrf: str = Form("")):
        _csrf(request, csrf)
        who = _who(request)
        resp = _decide(lambda: state["registry"].approve(approval_id, who, "web"), request)
        _sync_telegram(approval_id, "✅ <b>Approved on the web page</b>. Undo any time with /rollback.")
        return resp

    @app.post("/approvals/{approval_id}/reject")
    def web_reject(approval_id: int, request: Request, csrf: str = Form(""), reason: str = Form("other")):
        _csrf(request, csrf)
        who = _who(request)
        resp = _decide(lambda: state["registry"].reject(approval_id, who, "web", reason), request)
        _sync_telegram(approval_id, f"❌ <b>Rejected on the web page</b> ({H(REASON_CODES.get(reason, reason))}).")
        return resp

    @app.post("/models/{name}/rollback")
    def web_rollback(name: str, request: Request, csrf: str = Form("")):
        _csrf(request, csrf)
        who = _who(request)
        return _decide(lambda: state["registry"].rollback(name, who, "web"), request)

    def _sync_telegram(approval_id: int, text: str) -> None:
        tg, a = state.get("telegram"), state["registry"].approval(approval_id)
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
</style></head><body>{body}</body></html>"""


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, proxy_headers=True, forwarded_allow_ips="127.0.0.1")  # noqa: S104


if __name__ == "__main__":
    main()
