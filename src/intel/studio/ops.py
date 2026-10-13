"""Operations: start the whole machine, or one part of it, from the console (ADR 0040).

A FIXED list of actions (no command is ever built from page input). Each action is a sequence of steps; a driver process
runs them one after another and writes its progress to `data/studio/ops/current.json`, so the page can show it and a
closed browser tab does not stop it:

    ml.weekly      the Sunday run: pull, market, ledger, train 7 models, judge, package, upload, knowledge pack, report
    ml.pull        only the warehouse pull
    ml.market      only the market snapshot
    agents.drafts  the studio's run, drafts into the Calendar (tokens: asks first)
    agents.render  the studio's run, media only, nothing sent
    all            ml.weekly, then agents.drafts (the second only if the first succeeded)

Each step is the same command the schedule or the owner would type; the programs keep their own locks, so two runs of
the same kind can never overlap. Nothing here approves or posts anything.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intel.studio.config import REPO

PY = [sys.executable, "-c", "from intel.cli import app; app()"]


@dataclass(frozen=True)
class Step:
    id: str
    label: str
    cmd: list[str]


@dataclass(frozen=True)
class Action:
    id: str
    group: str  # all | ml | agents
    label: str
    what: str
    cost: str
    steps: tuple[Step, ...]
    confirm: bool  # spends tokens or changes your Calendar: the page asks first


WEEKLY = Step("ml.weekly", "Sunday ML run", ["/bin/bash", str(REPO / "scripts" / "weekly.sh")])
DRAFTS = Step(
    "agents.drafts", "Studio run: drafts", [*PY, "studio", "run", "--publish", "drafts", "--trigger", "button"]
)

ACTIONS: dict[str, Action] = {
    a.id: a
    for a in (
        Action("all", "all", "Run everything",
               "The ML run (data to deployment), then the studio makes the next days' drafts.",
               "about 5 minutes of ML, then about 13 minutes and about 650,000 tokens for the studio",
               (WEEKLY, DRAFTS), True),
        Action("ml.weekly", "ml", "Run the ML part",
               "Pull the warehouse, market snapshot, ledger, train and judge the 7 models, package, upload, "
               "knowledge pack.",
               "about 5 minutes, no tokens", (WEEKLY,), False),
        Action("agents.drafts", "agents", "Run the agent part",
               "Plan, direct, write, design, render and hand over drafts for the next days.",
               "about 13 minutes and about 650,000 tokens", (DRAFTS,), True),
        Action("agents.render", "agents", "Agent part, media only",
               "The same run, but nothing is sent to the Calendar.",
               "about 13 minutes and about 650,000 tokens", (
                   Step("agents.render", "Studio run: media only",
                        [*PY, "studio", "run", "--publish", "render", "--trigger", "button"]),), True),
        Action("ml.pull", "ml", "Pull the warehouse",
               "Only the read-only snapshot of the VPS warehouse, with its data checks.",
               "about 20 seconds", (Step("ml.pull", "Pull the warehouse", [*PY, "pull"]),), False),
        Action("ml.market", "ml", "Market snapshot",
               "Only the market snapshot from the data platform.",
               "about 20 seconds", (Step("ml.market", "Market snapshot", [*PY, "market", "pull"]),), False),
    )
}  # fmt: skip


def catalog() -> list[dict[str, Any]]:
    return [{"id": a.id, "group": a.group, "label": a.label, "what": a.what, "cost": a.cost, "confirm": a.confirm,
             "steps": [{"id": s.id, "label": s.label} for s in a.steps]} for a in ACTIONS.values()]  # fmt: skip


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def current(ops_dir: Path) -> dict[str, Any] | None:
    """The operation in progress or the last one. A driver that died leaves `running`; it is shown as `stopped`."""
    f = ops_dir / "current.json"
    if not f.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(f.read_text())
    except ValueError:
        return None
    if data.get("state") == "running":
        try:
            os.kill(int(data.get("pid") or 0), 0)
        except (ProcessLookupError, ValueError):
            data["state"] = "stopped"
        except PermissionError:
            pass
    return data


def drive(action_id: str, ops_dir: Path, run: Callable[..., int] | None = None, wait: float = 0.0) -> int:
    """Run an action's steps in order, writing progress after each change. Stops at the first failing step."""
    action = ACTIONS[action_id]
    runner = run or (lambda cmd: subprocess.run(cmd, cwd=REPO, check=False).returncode)  # noqa: S603
    state: dict[str, Any] = {
        "action": action_id, "label": action.label, "state": "running", "pid": os.getpid(), "started_at": _now(),
        "steps": [{"id": s.id, "label": s.label, "state": "waiting", "started_at": None, "finished_at": None,
                   "exit": None} for s in action.steps],
    }  # fmt: skip
    path = ops_dir / "current.json"
    _write(path, state)
    code = 0
    for step, row in zip(action.steps, state["steps"], strict=True):
        row.update(state="running", started_at=_now())
        _write(path, state)
        time.sleep(wait)
        code = runner(step.cmd)
        row.update(state="done" if code == 0 else "failed", finished_at=_now(), exit=code)
        _write(path, state)
        if code != 0:
            for rest in state["steps"]:
                if rest["state"] == "waiting":
                    rest["state"] = "skipped"
            break
    state.update(state="done" if code == 0 else "failed", finished_at=_now())
    _write(path, state)
    return code


def start(action_id: str, ops_dir: Path, busy: Callable[[], str | None]) -> dict[str, Any]:
    """Start the driver in its own process. `busy` says why not (a run of this kind is working), or None."""
    if action_id not in ACTIONS:
        raise KeyError(action_id)
    now = current(ops_dir)
    if now and now.get("state") == "running":
        raise RuntimeError(f"{now['label']} is already running")
    why = busy()
    if why:
        raise RuntimeError(why)
    subprocess.Popen(  # noqa: S603
        [*PY[:2], "from intel.studio.ops import main; main()", action_id], cwd=REPO, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)  # fmt: skip
    return {"started": action_id}


def main() -> None:
    """Entry of the driver process: `python -c "from intel.studio.ops import main; main()" <action>`."""
    action_id = sys.argv[-1]
    if action_id not in ACTIONS:
        raise SystemExit(f"unknown action {action_id!r}")
    raise SystemExit(drive(action_id, REPO / "data" / "studio" / "ops"))
