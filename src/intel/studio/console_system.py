"""The console's pages for the whole machine: the agent system, the ML pipeline, operations and the demo (ADR 0040).

An APIRouter included by `console.py`. Read-only except `/api/ops/start`, which starts one action from the fixed list in
`ops.py` and needs the console's session token (the guard in `console.py` checks it for every POST).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from intel import mlview
from intel.studio import ops, schedule, telemetry, trace
from intel.studio.agents import AGENTS_DIR, agent_spec
from intel.studio.config import REPO
from intel.studio.runlog import read_events

DATA = REPO / "data" / "studio"
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
router = APIRouter()


def _running() -> str | None:
    return schedule.live_run(DATA / ".running")


def _folder(run: str | None) -> Path | None:
    """The run asked for, else the one working, else the newest."""
    runs = DATA / "runs"
    if run:
        if not RUN_ID.match(run) or not (runs / run / "events.jsonl").exists():
            raise HTTPException(404, "no such run")
        return runs / run
    working = _running()
    if working and (runs / working / "events.jsonl").exists():
        return runs / working
    found = sorted((p for p in runs.glob("*") if (p / "events.jsonl").exists()), reverse=True)
    return found[0] if found else None


def _system(events: list[dict[str, Any]], run_id: str | None, running: bool, demo: bool = False) -> dict[str, Any]:
    done = any(e.get("node") == "orchestrator" and e.get("kind") == "end" for e in events)
    now_ms = events[-1]["ms"] if events else 0
    return {"run": run_id, "running": running, "finished": done, "demo": demo, "topology": trace.topology(),
            "layers": trace.layers(events), "calls": trace.calls(events), "now_ms": now_ms}  # fmt: skip


@router.get("/api/system")
def api_system(run: str | None = None) -> dict[str, Any]:
    folder = _folder(run)
    if folder is None:
        return _system([], None, False)
    return _system(read_events(folder / "events.jsonl"), folder.name, _running() == folder.name)


@router.get("/api/system/call")
def api_system_call(id: str, run: str | None = None) -> dict[str, Any]:
    folder = _folder(run)
    detail = trace.call_detail(read_events(folder / "events.jsonl"), id) if folder else None
    if detail is None:
        raise HTTPException(404, "no such call")
    return detail


@router.get("/api/system/demo")
def api_system_demo(tick: int = 0) -> dict[str, Any]:
    events, clock = telemetry.demo_events_at(tick)
    total = telemetry.demo_events()[0][-1]["ms"]
    out = _system(events, "demo", clock < total, demo=True)
    out["now_ms"] = clock
    return out


@router.get("/api/system/demo/call")
def api_system_demo_call(id: str, tick: int = 0) -> dict[str, Any]:
    detail = trace.call_detail(telemetry.demo_events_at(tick)[0], id)
    if detail is None:
        raise HTTPException(404, "no such call")
    return detail


@router.get("/api/system/agent/{name}")
def api_system_agent(name: str) -> dict[str, Any]:
    if name not in trace.AGENTS:
        raise HTTPException(404, "no such agent")
    return agent_spec(name, AGENTS_DIR)


@router.get("/api/system/agent/{name}/prompt", response_class=PlainTextResponse)
def api_system_agent_prompt(name: str) -> str:
    if name not in trace.AGENTS:
        raise HTTPException(404, "no such agent")
    return str(agent_spec(name, AGENTS_DIR)["prompt"])


# ---------- the ML layer ----------


def _ml_paths() -> tuple[Path, Path, Path]:
    from intel.settings import get_settings

    s = get_settings()
    return s.intel_data_dir / "weekly", s.intel_data_dir / "runs", s.intel_data_dir / "logs"


@router.get("/api/ml")
def api_ml() -> dict[str, Any]:
    from intel.control import history_rows, lock_alive, log_tail

    weekly, runs, logs = _ml_paths()
    return mlview.ml_state(weekly, runs, lock_alive(weekly), history_rows(weekly), log_tail(logs, 60))


@router.get("/api/ml/demo")
def api_ml_demo(tick: int = 0) -> dict[str, Any]:
    return mlview.ml_demo(tick)


@router.get("/api/ml/model/{name}")
def api_ml_model(name: str) -> dict[str, Any]:
    detail = mlview.model_detail(name, _ml_paths()[1])
    if detail is None:
        raise HTTPException(404, "no such model")
    return detail


@router.get("/api/ml/model/{name}/insights", response_class=PlainTextResponse)
def api_ml_insights(name: str) -> str:
    text = mlview.insights_text(name, _ml_paths()[1])
    if text is None:
        raise HTTPException(404, "no insights for this model")
    return text


# ---------- operations ----------

OPS_DIR = DATA / "ops"


def _busy() -> str | None:
    from intel.control import lock_alive

    if _running():
        return f"the studio is already running ({_running()})"
    if lock_alive(_ml_paths()[0]):
        return "the ML run is already working"
    return None


class StartOp(BaseModel):
    action: str
    confirm: bool = False


@router.get("/api/ops")
def api_ops() -> dict[str, Any]:
    from intel.control import lock_alive

    now = ops.current(OPS_DIR)
    return {"actions": ops.catalog(), "current": now, "studio_running": _running(),
            "ml_running": lock_alive(_ml_paths()[0])}  # fmt: skip


@router.post("/api/ops/start")
def api_ops_start(body: StartOp) -> dict[str, Any]:
    action = ops.ACTIONS.get(body.action)
    if action is None:
        raise HTTPException(422, "unknown action")
    if action.confirm and not body.confirm:
        raise HTTPException(409, f"{action.label} spends tokens or writes drafts: confirm first ({action.cost})")
    try:
        return ops.start(body.action, OPS_DIR, _busy)
    except RuntimeError as ex:
        raise HTTPException(409, str(ex)) from None
