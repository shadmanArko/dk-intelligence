"""Operations: start the whole machine, or one part of it, from the console (ADR 0040).

A FIXED list of actions (no command is ever built from page input). Each action is a sequence of steps; a driver process
runs them one after another and writes its progress to `data/studio/ops/current.json`, so the page can show it and a
closed browser tab does not stop it:

    ml.weekly      the Sunday run: pull, market, ledger, train 7 models, judge, package, upload, knowledge pack, report
    ml.pull        only the warehouse pull
    ml.market      only the market snapshot
    ml.only.<step> one model alone (the same data steps and judge, nothing uploaded, not this week's training)
    agents.drafts  the studio's run, drafts into the Calendar (tokens: asks first)
    agents.render  the studio's run, media only, nothing sent
    all            ml.weekly, then agents.drafts (the second only if the first succeeded)

Each step is the same command the schedule or the owner would type; the programs keep their own locks, so two runs of
the same kind can never overlap. Nothing here approves or posts anything.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intel.studio.config import REPO

PY = [sys.executable, "-c", "from intel.cli import app; app()"]
STOPPED = (130, 143, -15)  # exit codes of a run the owner stopped (it ended cleanly, or was ended by SIGTERM)


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
    models: tuple[str, ...] = ()  # the models a single-model action trains


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


def _single_model_actions() -> dict[str, Action]:
    """One action per model plug-in (the two scorers share one: they are trained together). The ids come from the code's
    own plug-ins, never from the page."""
    from intel.plugins import discover

    out: dict[str, Action] = {}
    for p in discover():
        names = ", ".join(p.models)
        step = Step(f"ml.only.{p.step}", f"Retrain {names}", [*PY, "weekly", "--only", p.step, "--no-upload"])
        out[step.id] = Action(
            step.id, "model", f"Retrain {names.replace('_', ' ')}",
            "Pull fresh data, train and judge this model only. A candidate: nothing is uploaded or deployed, and it "
            "does not count as this week's training. The full ML run deploys.",
            "about 1 to 3 minutes, no tokens", (step,), False, tuple(p.models))  # fmt: skip
    return out


ACTIONS.update(_single_model_actions())


def catalog() -> list[dict[str, Any]]:
    return [{"id": a.id, "group": a.group, "label": a.label, "what": a.what, "cost": a.cost, "confirm": a.confirm,
             "models": list(a.models), "steps": [{"id": s.id, "label": s.label} for s in a.steps]}
            for a in ACTIONS.values()]  # fmt: skip


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
        row.update(
            state="done" if code == 0 else "stopped" if code in STOPPED else "failed", finished_at=_now(), exit=code
        )
        _write(path, state)
        if code != 0:
            for rest in state["steps"]:
                if rest["state"] == "waiting":
                    rest["state"] = "skipped"
            break
    state.update(state="done" if code == 0 else "stopped" if code in STOPPED else "failed", finished_at=_now())
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


# ---------- stopping ----------


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except ProcessLookupError:
        return None


def _lock_pid(path: Path, alive: Callable[[int], bool] = _alive) -> int | None:
    """The process named by a lock file (`<pid> <run>` or just `<pid>`), if it is alive."""
    try:
        pid = int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return pid if alive(pid) else None


def stop_all(
    studio_lock: Path,
    ml_lock_pid: Path,
    ops_dir: Path,
    grace_s: float = 25.0,
    *,
    alive: Callable[[int], bool] = _alive,
    pgid_of: Callable[[int], int | None] = _pgid,
    term: Callable[[int], None] = lambda pid: os.kill(pid, signal.SIGTERM),
    term_group: Callable[[int], None] = lambda pg: os.killpg(pg, signal.SIGTERM),
    kill_group: Callable[[int], None] = lambda pg: os.killpg(pg, signal.SIGKILL),
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Stop what this Mac is running: the studio run, the ML run, and the operation that started them.

    1. Ask each run to stop: SIGTERM to its main process. It writes its own ending ("stopped by the owner") and
       exits, so its work is kept and Resume works.
    2. Give it `grace_s` to do so.
    3. Stop what is left: the operation's driver, and the agents and renderers the runs started (they are in a process
       group of their own, never this console's). Last resort: SIGKILL for what ignored the first request.
    Only processes named by this program's own lock files are ever signalled."""
    me = os.getpgid(0)
    stopped: list[str] = []
    targets = [("the studio run", _lock_pid(studio_lock, alive)), ("the ML run", _lock_pid(ml_lock_pid, alive))]
    now = current(ops_dir)
    driver = int(now["pid"]) if now and now.get("state") == "running" and alive(int(now.get("pid") or 0)) else None
    groups: set[int] = set()
    for name, pid in [*targets, ("the operation", driver)]:
        if pid is None:
            continue
        pg = pgid_of(pid)
        if pg and pg not in (0, 1, me):
            groups.add(pg)
        stopped.append(name)
    for _name, pid in targets:
        if pid is not None:
            _quietly(term, pid)
    waited = 0.0
    while waited < grace_s and any(pid is not None and alive(pid) for _name, pid in targets):
        sleep(0.5)
        waited += 0.5
    if driver is not None:
        _quietly(
            term, driver
        )  # the driver ends with its children; it cannot write a final state, so it reads as stopped
    for pg in groups:  # the runs' agents, renderers and tunnels (a group the system refuses to signal is skipped)
        _quietly(term_group, pg)
    sleep(1.0)
    for pg in groups:
        if any(pid is not None and alive(pid) for _name, pid in [*targets, ("the operation", driver)]):
            _quietly(kill_group, pg)
    return stopped


def _quietly(fn: Callable[[int], None], arg: int) -> None:
    with contextlib.suppress(OSError):  # already gone, or not ours to signal; our own locks' processes were signalled
        fn(arg)
