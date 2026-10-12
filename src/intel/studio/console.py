"""The Studio Console: watch a run (or look at an old one) and start one with a button. This Mac only (127.0.0.1).

    uv run python -m intel.studio.console        → http://127.0.0.1:8771

It only READS a run's folder (`state.json`, `events.jsonl`, `summary.json`, thumbnails); the Run button starts the same
command line the schedule uses. A run that creates drafts in the owner's Calendar asks for a confirmation first.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

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
            "platforms": sorted(idea["slots"]), "slots": idea["slots"], "angle": row.get("angle"),
            "pillar": row.get("pillar"), "stage": row["stage"], "captions": row.get("captions", {}),
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
    if body.resume:
        if not RUN_ID.match(body.resume):
            raise HTTPException(422, "bad run id")
        cmd += ["--resume", body.resume]
    subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)  # noqa: S603
    return {"started": True}


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return (Path(__file__).parent / "console.html").read_text()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
