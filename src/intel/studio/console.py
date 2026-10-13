"""The Studio Console: watch a run (or look at an old one) and start one with a button. This Mac only (127.0.0.1).

    uv run python -m intel.studio.console        → http://127.0.0.1:8771

It only READS a run's folder (`state.json`, `events.jsonl`, `summary.json`, thumbnails); the Run button starts the same
command line the schedule uses. A run that creates drafts in the owner's Calendar asks for a confirmation first.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from intel.studio import config as studio_config
from intel.studio import coverage, handover, telemetry, today
from intel.studio.config import REPO
from intel.studio.runlog import read_events

DATA = REPO / "data" / "studio"
PORT = 8771
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
app = FastAPI(title="Studio Console", docs_url=None, redoc_url=None)


def _folder(run_id: str) -> Path:
    folder = DATA / "runs" / run_id
    if not RUN_ID.match(run_id) or not (folder / "state.json").exists():
        raise HTTPException(404, "no such run")
    return folder


def _running() -> str | None:
    lock = DATA / ".running"
    return lock.read_text().split()[-1] if lock.exists() else None


def run_view(folder: Path) -> dict[str, Any]:
    """One run as the page draws it: counters, phases, and a card per idea."""
    events = read_events(folder / "events.jsonl")
    state = json.loads((folder / "state.json").read_text())
    start = next((e for e in events if e["node"] == "orchestrator" and e["kind"] == "start"), {})
    end = next((e for e in reversed(events) if e["node"] == "orchestrator" and e["kind"] == "end"), None)
    running = _running() == folder.name
    phases = [{"phase": e["phase"], "status": e["status"], "seconds": e.get("seconds")}
              for e in events if e["kind"] == "phase"]  # fmt: skip
    tokens = int((end or {}).get("tokens") or 0) or sum(
        int(e.get("tokens_in") or 0) + int(e.get("tokens_out") or 0) for e in events if e["kind"] == "end"
    )  # a finished run states its total; a running one is summed from its agents' reports
    cap = json.loads((folder / "config.json").read_text())["budgets"]["max_tokens_per_run"] if (
        folder / "config.json").exists() else None  # fmt: skip
    ideas = []
    for idea_id, row in sorted(state["ideas"].items()):
        idea = row["idea"]
        ideas.append({
            "id": idea_id, "date": idea["date"], "format": idea["format"], "window": idea["window"],
            "template": (row.get("treatment") or {}).get("template"),
            "platforms": sorted(idea["slots"]), "slots": idea["slots"], "angle": row.get("angle"),
            "pillar": row.get("pillar"), "playbook_note": row.get("playbook_note"), "stage": row["stage"],
            "captions": row.get("captions", {}),
            "options": row.get("options", {}), "holds": row.get("holds", {}), "outcome": row.get("outcome"),
            "hook": ((row.get("design") or {}).get("spec") or {}).get("content", {}).get("hook"),
            "thumb": f"/api/runs/{folder.name}/thumb/{row['thumb']}" if row.get("thumb") else None,
        })  # fmt: skip
    return {
        "id": folder.name, "publish": start.get("publish"), "trigger": start.get("trigger"), "plan": start.get("plan"),
        "status": "running" if running else (end or {}).get("status", "stopped"), "error": (end or {}).get("error"),
        "tokens": tokens, "cap": cap, "phases": phases, "ideas": ideas, "dropped": state.get("dropped", []),
        "summary": json.loads((folder / "summary.json").read_text()) if (folder / "summary.json").exists() else None,
        "seconds": events[-1]["ms"] // 1000 if events else 0,
    }  # fmt: skip


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    out = []
    for folder in sorted((p for p in (DATA / "runs").glob("*") if (p / "state.json").exists()), reverse=True)[:30]:
        view = run_view(folder)
        done = sum(1 for i in view["ideas"] if i["outcome"])
        out.append({"id": view["id"], "status": view["status"], "publish": view["publish"], "tokens": view["tokens"],
                    "ideas": f"{done}/{len(view['ideas'])}", "seconds": view["seconds"]})  # fmt: skip
    return out


@app.get("/api/runs/{run_id}")
def run(run_id: str) -> dict[str, Any]:
    return run_view(_folder(run_id))


@app.get("/api/runs/{run_id}/events")
def events(run_id: str, after: int = 0) -> dict[str, Any]:
    ev = read_events(_folder(run_id) / "events.jsonl")
    return {"next": len(ev), "events": [e for e in ev[after:] if e["kind"] != "thought"][-200:]}


@app.get("/api/runs/{run_id}/thumb/{name}")
def thumb(run_id: str, name: str) -> FileResponse:
    path = _folder(run_id) / name
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,90}\.jpg", name) or not path.is_file():
        raise HTTPException(404, "no such picture")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {"running": _running()}


class Start(BaseModel):
    publish: str = "render"
    days: int | None = None
    start: str | None = None  # the first day to plan, YYYY-MM-DD (default: the day after what is already scheduled)
    resume: str | None = None
    confirm: bool = False


@app.post("/api/run")
def start(body: Start) -> dict[str, Any]:
    if body.publish not in ("render", "dry-run", "drafts"):
        raise HTTPException(422, "publish must be render, dry-run or drafts")
    if body.publish == "drafts" and not body.confirm:
        raise HTTPException(409, "drafts go to your Calendar: confirm first")
    if _running():
        raise HTTPException(409, "a run is already in progress")
    cmd = [sys.executable, "-c", "from intel.cli import app; app()", "studio", "run", "--publish", body.publish,
           "--trigger", "button"]  # fmt: skip
    if body.days:
        cmd += ["--days", str(min(max(body.days, 1), 30))]
    if body.start:
        try:
            dt.date.fromisoformat(body.start)
        except ValueError:
            raise HTTPException(422, "start must be a date like 2026-10-24") from None
        cmd += ["--from", body.start]
    if body.resume:
        if not RUN_ID.match(body.resume):
            raise HTTPException(422, "bad run id")
        cmd += ["--resume", body.resume]
    subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)  # noqa: S603
    return {"started": True}


# ---------- Today: everything that waits for the owner, and the living picture of a run ----------

WEB = Path(__file__).parent / "web"
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,60}$")


def _reels() -> Path:
    from intel.studio import media

    return media.REELS


def _client() -> handover.PublishClient | None:
    env = handover.load_env()
    return handover.PublishClient(env["DK_PUBLISH_URL"], env["DK_PUBLISH_KEY"]) if len(env) == 2 else None


def _rows() -> list[dict[str, Any]] | None:
    client = _client()
    return coverage.fetch_rows(client, dt.datetime.now(dt.UTC)) if client else None


def _readers(rows: list[dict[str, Any]] | None) -> today.Readers:
    from intel.studio import schedule as schedule_mod

    summary = DATA / "last-summary.md"
    return today.Readers(
        calendar=lambda: rows,
        pending_proposals=lambda: today.pending_proposals(_reels()),
        unlabelled_footage=lambda: today.unlabelled_footage(_reels()),
        last_summary=lambda: summary.read_text() if summary.exists() else None,
        running=_running,
        schedule_installed=lambda: schedule_mod.PLIST.exists(),
        now=lambda: dt.datetime.now(dt.UTC),
    )


@app.get("/api/today")
def api_today() -> dict[str, Any]:
    cfg = studio_config.load()
    rows = _rows()
    cov = coverage.coverage(cfg, rows, dt.datetime.now(dt.UTC))
    return today.build(_readers(rows), cfg.schedule, today.owner_decisions(), cov)


@app.get("/api/coverage")
def api_coverage() -> dict[str, Any]:
    """How far ahead the studio's posts are scheduled and where a new plan would start (no tokens)."""
    return coverage.coverage(studio_config.load(), _rows(), dt.datetime.now(dt.UTC))


def _latest_folder() -> Path | None:
    running = _running()
    if running and (DATA / "runs" / running / "state.json").exists():
        return DATA / "runs" / running
    folders = sorted((p for p in (DATA / "runs").glob("*") if (p / "state.json").exists()), reverse=True)
    return folders[0] if folders else None


@app.get("/api/telemetry")
def api_telemetry() -> dict[str, Any]:
    """Mission control: the run that is working, else the newest one, as numbers (see telemetry.py)."""
    folder = _latest_folder()
    if folder is None:
        return {"run": None, "status": "idle", "running": False, "percent": 0, "stages": [], "matrix": [], "bars": [],
                "roles": {}, "lanes": list(telemetry.LANES), "phases": [], "series": [], "log": [], "working": [],
                "elapsed_s": 0, "eta_s": None, "now_ms": 0, "ideas": 0, "ideas_done": 0, "ideas_held": 0,
                "tokens": 0, "cap": None, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "retries": 0,
                "tool_calls": 0}  # fmt: skip
    view = run_view(folder)
    events = read_events(folder / "events.jsonl")
    began = telemetry.started_at(events)
    now_ms = (
        int((dt.datetime.now(dt.UTC) - began).total_seconds() * 1000) if began and view["status"] == "running" else None
    )
    return telemetry.telemetry(view, events, now_ms)


@app.get("/api/telemetry/demo")
def api_telemetry_demo(tick: int = 0) -> dict[str, Any]:
    return telemetry.demo(max(0, tick))


def _run_npm(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["npm", "run", "-s", "curate", "--", *args], cwd=_reels(), capture_output=True, text=True,  # noqa: S603, S607
                          timeout=300)  # fmt: skip


@app.post("/api/curate/accept-all")
def curate_accept_all() -> dict[str, Any]:
    pending = today.pending_proposals(_reels())
    if not pending:
        raise HTTPException(409, "nothing is waiting")
    out = _run_npm("--accept-all")
    if out.returncode != 0:
        raise HTTPException(500, out.stderr[-200:] or "the accept failed")
    return {"message": f"accepted {len(pending)} proposals"}


@app.post("/api/curate/reject-all")
def curate_reject_all() -> dict[str, Any]:
    ids = [str(p.get("id")) for p in today.pending_proposals(_reels())]
    for i in ids:
        if not ID.match(i):
            continue  # only plain ids ever reach the command line
        _run_npm("--reject", i)
    return {"message": f"rejected {len(ids)} proposals"}


@app.post("/api/curate/enrich")
def curate_enrich() -> dict[str, Any]:
    if _running():
        raise HTTPException(409, "a run is in progress: wait for it")
    cmd = [
        sys.executable,
        "-c",
        "from intel.cli import app; app()",
        "studio",
        "curate",
        "--scope",
        "enrich",
        "--limit",
        "20",
    ]
    subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)  # noqa: S603
    return {"message": "the curator is looking at 20 clips; proposals appear here in a few minutes"}


@app.post("/api/schedule/install")
def schedule_install() -> dict[str, Any]:
    import shutil

    from intel.studio import config as studio_config
    from intel.studio import schedule as schedule_mod

    uv = shutil.which("uv")
    if uv is None:
        raise HTTPException(500, "uv is not on the PATH")
    cfg = studio_config.load().schedule
    schedule_mod.install(cfg, uv, f"{Path(uv).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    return {"message": f"installed: a check every day at {cfg.run_at}"}


@app.post("/api/performance/analyst")
def performance_analyst() -> dict[str, Any]:
    cmd = [sys.executable, "-c", "from intel.cli import app; app()", "performance", "analyst"]
    subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)  # noqa: S603
    return {"message": "writing the note: data/performance/<latest>/analyst.md"}


NAV = (("/", "Today"), ("/live", "Mission control"), ("/runs", "Runs"), ("/guide", "Guide"))


def _nav(active: str) -> str:
    """The top bar every page shares. The review page lives on the publisher: it opens in a new tab."""
    client_url = handover.load_env().get("DK_PUBLISH_URL", "").rstrip("/")
    links = "".join(f'<a href="{href}"{" class=on" if href == active else ""}>{label}</a>' for href, label in NAV)
    review = f'<a href="{client_url}/review" target="_blank" rel="noopener">Review drafts ↗</a>' if client_url else ""
    return (
        "<nav id=topbar><b>Dhaka Kacchi · Studio</b>" + links + "<span class=sp></span>" + review + "</nav>"
        "<style>#topbar{display:flex;flex-wrap:wrap;align-items:center;gap:4px;padding:8px 16px;background:#120708;"
        "border-bottom:1px solid #3a1f21;position:sticky;top:0;z-index:50;font:14px system-ui,sans-serif}"
        "#topbar b{color:#f4b02e;margin-right:14px;letter-spacing:.02em}"
        "#topbar a{color:#d9c9b0;text-decoration:none;padding:6px 12px;border-radius:8px}"
        "#topbar a:hover{background:#2b1013;color:#fff}#topbar a.on{background:#f4b02e;color:#1a0a0b;font-weight:600}"
        "#topbar .sp{flex:1}</style>"
    )


def _page(name: str, active: str) -> str:
    return (WEB / name).read_text().replace("<!--NAV-->", _nav(active))


@app.get("/guide", response_class=HTMLResponse)
def guide() -> str:
    path = REPO / "docs" / "OWNER_GUIDE.md"
    text = path.read_text() if path.exists() else "no guide yet"
    esc = text.replace("&", "&amp;").replace("<", "&lt;")
    style = "font:15px/1.6 system-ui;max-width:760px;margin:24px auto;padding:0 16px"
    head = "<!doctype html><meta charset=utf-8><title>Studio guide</title>"
    body = f"<body style='margin:0;background:#f6f4ef;color:#1d1b16'>{_nav('/guide')}"
    return f"{head}{body}<main style='{style}'><pre style='white-space:pre-wrap;font:inherit'>{esc}</pre></main></body>"


@app.get("/live", response_class=HTMLResponse)
def live_page() -> str:
    return _page("mission.html", "/live")


@app.get("/runs", response_class=HTMLResponse)
def runs_page() -> str:
    return _page("runs.html", "/runs")


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return _page("today.html", "/")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
