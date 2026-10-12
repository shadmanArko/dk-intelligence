"""The Agent Console (lab): watch a run live, look inside every agent, and start a run with a button.

    uv run python lab/agentic-slice/console.py        → http://127.0.0.1:8770 (this Mac only)

It only READS the run logs that the engine (`run.py`) writes, plus dk_intel's own audit chain as an independent
record of each MCP call; the Run button starts the same engine the schedule uses (`--trigger button`).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

LAB = Path(__file__).resolve().parent
RUNS = LAB / "runs"
LOCK = RUNS / ".running"
PORT = 8770

app = FastAPI(title="Agent Console", docs_url=None, redoc_url=None)


def _events(run_id: str) -> list[dict[str, Any]]:
    path = RUNS / f"{run_id}.jsonl"
    if not path.is_file() or "/" in run_id or ".." in run_id:
        raise HTTPException(404, "no such run")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summary(run_id: str) -> dict[str, Any]:
    return _summary(run_id, _events(run_id))


def _summary(run_id: str, ev: list[dict[str, Any]]) -> dict[str, Any]:
    first = next((e for e in ev if e["node"] == "orchestrator" and e["kind"] == "start"), {})
    last = next((e for e in reversed(ev) if e["node"] == "orchestrator" and e["kind"] == "end"), None)
    ends = [e for e in ev if e["kind"] == "end" and e["node"] != "orchestrator"]
    return {
        "id": run_id,
        "trigger": first.get("trigger"),
        "platform": first.get("platform"),
        "status": (last or {}).get("status", "running"),
        "seconds": round(ev[-1]["ms"] / 1000) if ev else 0,
        "agents": len({e["node"] for e in ev if e["kind"] == "start" and e["node"] != "orchestrator"}),
        "tool_calls": sum(e["kind"] == "tool_call" for e in ev),
        "tokens": sum(int(e.get("tokens_in") or 0) + int(e.get("tokens_out") or 0) for e in ends),
        "tokens_cached": sum(int(e.get("tokens_cached") or 0) for e in ends),
        "proposed": (last or {}).get("proposed"),
    }


def result_of(run_id: str, ev: list[dict[str, Any]]) -> dict[str, Any]:
    """What the run produced, one card per planned post: the plan, the caption, its check, and what became of it."""
    ends = {e["node"]: e for e in ev if e["kind"] == "end"}
    delegate = next((e for e in ev if e["node"] == "orchestrator" and e["kind"] == "delegate"), {})
    planned = (ends.get("planner", {}).get("output") or {}).get("slots", [])
    items = delegate.get("slots") or [{"node": f"helper-{i + 1}", **s} for i, s in enumerate(planned[:3])]
    lead_end = ends.get("checker") or ends.get("writer-lead")  # code since v3; an agent in older runs
    lead = (lead_end or {}).get("output") or {}
    proposed = {str(x.get("slot_id")): x for x in lead.get("proposed", [])}
    skipped = {str(x.get("slot_id")): x for x in lead.get("skipped", [])}
    posts = []
    screened = next((e for e in ev if e["node"] == "orchestrator" and e["kind"] == "screened"), {})
    for d in screened.get("dropped", []):  # slots code ruled out before any AI: no caption can pass there
        posts.append({"slot_id": str(d.get("slot_id")), "agent": "code", "day": d.get("day"), "date": None,
                      "time": d.get("time"), "format": d.get("format"), "angle": None, "sources": [], "caption": None,
                      "verdict": "rewrite", "score": d.get("score"), "rounds": 0, "tried": 0,
                      "status": "not_planned", "why": d.get("why")})  # fmt: skip
    for it in items:
        h = ends.get(it["node"])
        out = (h or {}).get("output") or {}
        sid = str(it.get("slot_id"))
        if sid in proposed:
            status, why = "proposed", f"proposal {proposed[sid].get('proposal_id')} waits for you in proposals.json"
        elif sid in skipped:
            status, why = "skipped", str(skipped[sid].get("why") or "")
        elif h is not None and h.get("status") == "failed":
            status, why = "failed", str(h.get("error") or "the writer failed")
        elif h is None:
            status, why = "writing", ""
        elif lead_end is not None and lead_end.get("status") == "failed":
            status, why = "failed", "the re-check failed before proposing it"
        else:
            status, why = "checking", ""
        posts.append({
            "slot_id": sid, "agent": it["node"], "day": it.get("day"), "date": it.get("date"),
            "time": it.get("time"), "format": it.get("format"), "angle": it.get("angle"),
            "sources": out.get("sources") or it.get("sources") or [], "caption": out.get("caption"),
            "verdict": out.get("verdict"), "score": out.get("score"), "rounds": out.get("rounds"),
            "tried": out.get("tried"), "status": status, "why": why,
        })  # fmt: skip
    posts.sort(key=lambda p: p["slot_id"])
    return {"run": _summary(run_id, ev), "posts": posts}


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    ids = sorted((p.stem for p in RUNS.glob("*.jsonl")), reverse=True)[:30] if RUNS.exists() else []
    return [summary(i) for i in ids]


@app.get("/api/runs/{run_id}/result")
def result(run_id: str) -> dict[str, Any]:
    return result_of(run_id, _events(run_id))


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {"running": LOCK.read_text().strip() if LOCK.exists() else None}


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: str, start: int = 0) -> StreamingResponse:
    """Server-sent events: every line of the run log as it is written; ends when the orchestrator ends."""
    _events(run_id)

    async def gen() -> Any:
        sent = start
        for _ in range(3600):  # an hour at most
            ev = _events(run_id)
            for e in ev[sent:]:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            sent = len(ev)
            if any(e["node"] == "orchestrator" and e["kind"] == "end" for e in ev):
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}/audit")
def audit(run_id: str) -> list[dict[str, Any]]:
    """dk_intel's own hash-chained audit entries during this run: what the MCP server itself recorded."""
    from intel.mcp_server.kit.audit import AuditLog
    from intel.settings import McpSettings

    ev = _events(run_id)
    if not ev:
        return []
    since, until = ev[0]["t"], ev[-1]["t"]
    out = []
    for e in AuditLog(McpSettings().audit_dir).entries():
        if since <= str(e.get("at", "")) <= until and e.get("kind") == "tool":
            out.append({k: e.get(k) for k in ("at", "profile", "name", "status", "ms", "tokens")})
    return out[-200:]


class Start(BaseModel):
    platform: str = "instagram"


@app.post("/api/run")
async def start_run(body: Start) -> dict[str, Any]:
    if body.platform not in ("instagram", "facebook"):
        raise HTTPException(422, "platform must be instagram or facebook")
    if LOCK.exists():
        raise HTTPException(409, "a run is already in progress")
    before = {p.stem for p in RUNS.glob("*.jsonl")} if RUNS.exists() else set()
    subprocess.Popen(  # noqa: S603 - fixed arguments, no shell
        [sys.executable, str(LAB / "run.py"), "--platform", body.platform, "--trigger", "button"],
        cwd=LAB, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )  # fmt: skip
    for _ in range(40):
        await asyncio.sleep(0.25)
        new = {p.stem for p in RUNS.glob("*.jsonl")} - before
        if new:
            return {"run": sorted(new)[-1]}
    raise HTTPException(500, "the run did not start (see runs/ and the engine's output)")


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return PAGE


PAGE = (LAB / "console.html").read_text()  # the page: SVG graph, live feed, history, audit, Run button


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
