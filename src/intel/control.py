"""`intel control`: the Control Room on the Mac. A local page with a Run button and the live pipeline diagram.

Security: listens on 127.0.0.1 only; refuses any Host header other than 127.0.0.1/localhost (DNS rebinding);
starting a run needs a random per-session token that only this page knows (a website you visit cannot trigger it).
A run is started exactly like launchd starts it (scripts/weekly.sh), so the lock prevents two runs at once.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import secrets
import subprocess
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from intel.pipeline import definition
from intel.settings import Settings
from intel.weekly import is_due, run_history

STATIC = Path(__file__).with_name("static")
ASSETS = {"pipeline.js": "text/javascript", "pipeline.css": "text/css"}
LOG_LINES = 150


def lock_alive(weekly_dir: Path) -> bool:
    try:
        pid = int((weekly_dir / ".lock" / "pid").read_text())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def history_rows(weekly_dir: Path, n: int = 8) -> list[dict]:
    out = []
    for r in reversed(run_history(weekly_dir)[-n:]):
        out.append(
            {
                "run_id": r.get("run_id"),
                "status": (r.get("status") or "?") + (" (dry run)" if r.get("dry_run") else ""),
                "failed_step": r.get("failed_step"),
                "new": (r.get("challenger") or {}).get("balanced_accuracy"),
                "current": (r.get("champion") or {}).get("balanced_accuracy"),
            }
        )
    return out


def log_tail(log_dir: Path, n: int = LOG_LINES) -> list[str]:
    logs = sorted(log_dir.glob("weekly_2*.log"))
    if not logs:
        return []
    lines = logs[-1].read_text(errors="replace").splitlines()
    return [ln for ln in lines if "INFO" not in ln or "intel" in ln][-n:]


def create_control_app(s: Settings, repo: Path, port: int) -> FastAPI:
    token = secrets.token_urlsafe(24)
    weekly_dir = s.intel_data_dir / "weekly"
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app = FastAPI(title="DK Intelligence Control Room", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"detail": "wrong host"}, status_code=400)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; form-action 'none'"
        )
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/", response_class=HTMLResponse)
    def page():
        return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>DK Control Room</title>
<link rel=stylesheet href="/static/pipeline.css"><link rel=stylesheet href="/static/control.css"></head><body>
<header><b>DK Intelligence · Control Room</b><span class=pl-muted>this Mac · weekly training</span>
<a href="{html.escape(s.intel_api_url or "#")}/training" target=_blank rel=noopener>VPS Training page ↗</a></header>
<div id=pipeline class=pl-wrap data-def="/api/pipeline" data-state="/api/state" data-run="/api/run"
 data-token="{token}"></div>
<script src="/static/pipeline.js"></script></body></html>"""

    @app.get("/static/{name}")
    def static(name: str):
        if name == "control.css":
            return HTMLResponse(CONTROL_CSS, media_type="text/css")
        if name not in ASSETS:
            raise HTTPException(404)
        return FileResponse(STATIC / name, media_type=ASSETS[name])

    @app.get("/api/pipeline")
    def pipeline():
        return definition()

    @app.get("/api/state")
    def state():
        cur = weekly_dir / "current.json"
        progress = json.loads(cur.read_text()) if cur.exists() else None
        running = lock_alive(weekly_dir)
        due, why = is_due(weekly_dir)
        # a run marked 'running' whose process is gone was interrupted (Mac slept/shut down, or it was killed)
        stalled = bool(progress and progress.get("state") == "running" and not running)
        return {
            "progress": progress,
            "running": running,
            "stalled": stalled,
            "due_text": ("DUE: " if due and not running else "") + why,
            "history": history_rows(weekly_dir),
            "log": log_tail(repo / "data" / "logs"),
            "now": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }

    @app.post("/api/run")
    def run(request: Request):
        if not secrets.compare_digest(request.headers.get("x-intel-token", ""), token):
            raise HTTPException(403, "missing or wrong token")
        if lock_alive(weekly_dir):
            raise HTTPException(409, "a training run is already in progress")
        subprocess.Popen(  # same entry point as launchd; the run takes the lock itself
            ["/bin/bash", str(repo / "scripts" / "weekly.sh")],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"started": True}

    # ---------- owner check of the competitor weakness miner (one clause at a time, no hints) ----------
    checks = s.intel_data_dir / "checks"

    def _sample() -> list[dict]:
        f = checks / "weakness_miner_sample.json"
        return json.loads(f.read_text()) if f.exists() else []

    def _labels() -> dict[str, dict]:
        f = checks / "weakness_miner_labels.jsonl"
        if not f.exists():
            return {}
        out = {}
        for line in f.read_text().splitlines():
            if line.strip():
                x = json.loads(line)
                out[x["key"]] = x  # last answer wins (you can redo one)
        return out

    @app.get("/check", response_class=HTMLResponse)
    def check_page():
        from intel.models.weakness_miner.mine import dictionary

        sample, done = _sample(), _labels()
        if not sample:
            return HTMLResponse(_shell("<p>No check sample yet: run the weekly training (or the miner) first.</p>"))
        todo = [x for x in sample if x["key"] not in done]
        n_done = len(sample) - len(todo)
        enough = n_done >= 150
        if not todo:
            return HTMLResponse(
                _shell(
                    f"<h2>All {len(sample)} checked. Thank you!</h2><p>The next training run "
                    "uses your answers to judge the review miner.</p><a href='/'>← Control Room</a>"
                )
            )
        item = todo[0]
        stars = "–" if item.get("rating") is None else f"{round(float(item['rating']) * 5)} of 5 stars"
        boxes = "".join(
            f"<label class=chk><input type=checkbox name=aspects value='{html.escape(k)}'> {html.escape(v)}</label>"
            for k, v in dictionary()["labels"].items()
        )
        note = (
            "<p class=ok>150 reached: enough for the judge. Continue if you like; more answers = a more "
            "reliable check.</p>"
            if enough
            else ""
        )
        return HTMLResponse(
            _shell(f"""<h2>Review check {n_done + 1} of {len(sample)}</h2>{note}
<p class=pl-muted>Read the sentence from a customer review of a Berlin restaurant that sells biryani. Tick EVERY topic it
talks about (none is fine), and say whether it praises, complains or neither. There is no right answer to
guess: your answers measure how well the computer reads reviews.</p>
<form method=post action=/check><input type=hidden name=token value='{token}'>
<input type=hidden name=key value='{html.escape(item["key"])}'>
<blockquote>{html.escape(item["clause"])}</blockquote><p class=pl-muted>Review rating: {stars}</p>
<fieldset><legend>Topics</legend><div class=grid>{boxes}</div></fieldset>
<fieldset><legend>Tone of this sentence</legend>
<label class=chk><input type=radio name=polarity value=negative required> Complaint</label>
<label class=chk><input type=radio name=polarity value=positive> Praise</label>
<label class=chk><input type=radio name=polarity value=neutral> Neither / mixed</label></fieldset>
<p><button class=pl-run>Save and next</button> <button class=link name=skip value=1 formnovalidate>Skip (unclear)</button></p>
</form>""")
        )

    @app.post("/check")
    def check_save(
        token_: str = Form("", alias="token"),
        key: str = Form(...),
        aspects: list[str] | None = Form(None),
        polarity: str = Form(""),
        skip: str = Form(""),
    ):
        if not secrets.compare_digest(token_, token):
            raise HTTPException(403, "missing or wrong token")
        from intel.models.weakness_miner.mine import dictionary

        if key not in {x["key"] for x in _sample()}:
            raise HTTPException(400, "unknown item")
        valid = set(dictionary()["labels"])
        rec = {
            "key": key,
            "aspects": sorted(a for a in aspects or [] if a in valid),
            "polarity": polarity if polarity in ("positive", "negative", "neutral") else None,
            "skip": bool(skip),
            "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
        checks.mkdir(parents=True, exist_ok=True)
        with (checks / "weakness_miner_labels.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return RedirectResponse("/check", status_code=303)

    return app


def _shell(body: str) -> str:
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Review check</title>
<link rel=stylesheet href="/static/pipeline.css"><link rel=stylesheet href="/static/control.css"></head><body>
<header><b>DK Intelligence · Control Room</b><a href="/">← Pipeline</a></header><main class=pl-wrap>{body}</main>
</body></html>"""


CONTROL_CSS = """body{background:var(--bg);color:var(--fg);margin:0 auto;max-width:1400px;padding:16px;
font:15px/1.5 system-ui,sans-serif}header{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;
border-bottom:1px solid var(--line);padding-bottom:8px}header a{color:var(--acc)}header a:first-of-type{margin-left:auto}
main{max-width:860px}blockquote{font-size:1.35rem;border-left:4px solid var(--acc);margin:16px 0;padding:8px 16px;
background:var(--card)}fieldset{border:1px solid var(--line);border-radius:10px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:4px}
.chk{display:inline-flex;gap:6px;align-items:center;padding:6px 8px;cursor:pointer}
button.link{background:none;border:0;color:var(--muted);text-decoration:underline;cursor:pointer}
.ok{color:var(--ok)}"""
