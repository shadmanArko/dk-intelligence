"""Running without you: when is a run due, and the launchd job that checks every day.

The daily check is cheap (one read of the publisher's calendar, no AI). A run starts only when the calendar needs
filling:
its last planned slot is fewer than `lead_days` ahead, nothing ran in the last `min_days_between` days, and not too many
drafts already wait for approval. Everything it makes is a draft; nothing here approves or posts.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from intel.studio.config import REPO, ScheduleCfg

LABEL = "com.dhakakacchi.studio"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
REMIND_LABEL = "com.dhakakacchi.studio.reminder"
REMIND_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{REMIND_LABEL}.plist"


def agent_plan_ends(calendar_rows: list[dict[str, Any]]) -> str | None:
    """The last planned slot of posts the STUDIO made (source api, not cancelled or failed). The owner's own
    hand-planned Sheet posts do not count: they say nothing about whether the studio's plan runs out."""
    live = [r["slot"] for r in calendar_rows if r.get("source") == "api" and r.get("slot")
            and r.get("state") not in ("cancelled", "failed", "expired", "invalid")]  # fmt: skip
    return max(live) if live else None


def due(cfg: ScheduleCfg, overview: dict[str, Any], last_run: dt.datetime | None, now: dt.datetime,
        agent_ends: str | None = None) -> tuple[bool, str]:  # fmt: skip
    """(run now?, the reason in plain words). `overview` is the publisher's GET /v1/overview; `agent_ends` is the
    last slot of the studio's own posts (`agent_plan_ends`)."""
    if not cfg.enabled:
        return False, "the schedule is switched off (schedule.yaml: enabled: false)"
    if last_run is not None and now - last_run < dt.timedelta(days=cfg.min_days_between):
        days = (now - last_run).days
        return False, f"the last run was {days} day(s) ago; the minimum is {cfg.min_days_between}"
    waiting = len(overview.get("awaiting_approval") or [])
    if waiting >= cfg.max_awaiting:
        return (
            False,
            f"{waiting} drafts already wait for your approval (the limit is {cfg.max_awaiting}): approve first",
        )
    ends = agent_ends
    if ends:
        last = dt.datetime.fromisoformat(str(ends))
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.UTC)
        ahead = (last - now).total_seconds() / 86400
        if ahead >= cfg.lead_days:
            return False, f"the calendar is planned {ahead:.1f} days ahead; a run starts below {cfg.lead_days}"
        return True, f"the calendar is planned only {max(ahead, 0):.1f} days ahead (below {cfg.lead_days})"
    return True, "the studio has nothing planned in the calendar"


def live_run(lock: Path) -> str | None:
    """The id of the run that is working right now, or None. A lock left by a crash or a power cut names a dead process:
    that is not a run in progress."""
    if not lock.exists():
        return None
    parts = lock.read_text().split()
    try:
        os.kill(int(parts[0]), 0)
    except (ValueError, IndexError, ProcessLookupError):
        return None
    except PermissionError:  # the process exists, it is just not ours
        pass
    return parts[-1] if len(parts) > 1 else "a run"


@dataclasses.dataclass(frozen=True)
class RunInfo:
    id: str
    trigger: str | None
    publish: str | None
    finished: bool  # the run wrote its own end: done, partial or failed, all final
    last_at: dt.datetime | None  # the time of its last event
    resumes: int  # how many times it has already been picked up again


def run_info(folder: Path) -> RunInfo | None:
    events = folder / "events.jsonl"
    if not events.exists():
        return None
    lines = [json.loads(x) for x in events.read_text().splitlines() if x.strip()]
    orch = [e for e in lines if e.get("node") == "orchestrator" and e.get("kind") in ("start", "end")]
    if not orch:
        return None
    starts = [e for e in orch if e["kind"] == "start"]
    last_at = None
    if lines and lines[-1].get("t"):
        with contextlib.suppress(ValueError):
            last_at = dt.datetime.fromisoformat(str(lines[-1]["t"]))
    return RunInfo(id=folder.name, trigger=starts[0].get("trigger") if starts else None,
                   publish=starts[0].get("publish") if starts else None, finished=orch[-1]["kind"] == "end",
                   last_at=last_at, resumes=sum(1 for e in starts if e.get("resume")))  # fmt: skip


def last_scheduled_run(runs_dir: Path, abandoned_before: dt.datetime | None = None) -> dt.datetime | None:
    """When the newest run that came from the schedule started (its run folder name is a UTC timestamp). A run that was
    cut off and is older than `abandoned_before` was dropped: it does not hold back the next one."""
    newest: dt.datetime | None = None
    for folder in runs_dir.glob("*"):
        info = run_info(folder)
        if info is None or info.trigger != "schedule":
            continue
        if abandoned_before and not info.finished and (info.last_at is None or info.last_at < abandoned_before):
            continue
        try:
            when = dt.datetime.strptime(folder.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
        except ValueError:
            continue
        newest = max(newest, when) if newest else when
    return newest


@dataclasses.dataclass(frozen=True)
class Decision:
    action: str  # run | resume | giveup | skip
    reason: str
    run_id: str | None = None
    publish: str | None = None


def unfinished_run(cfg: ScheduleCfg, runs_dir: Path, now: dt.datetime) -> tuple[RunInfo | None, str]:
    """The newest scheduled run that was cut off and may still be finished, with a plain reason when there is none."""
    newest: RunInfo | None = None
    for folder in sorted(runs_dir.glob("*")):
        info = run_info(folder)
        if info and info.trigger == "schedule" and not info.finished:
            newest = info
    if newest is None:
        return None, ""
    age = (now - newest.last_at) if newest.last_at else None
    if age is None or age > dt.timedelta(hours=cfg.resume_within_hours):
        return None, f"run {newest.id} was cut off more than {cfg.resume_within_hours} hours ago: dropped"
    return newest, ""


def decide(cfg: ScheduleCfg, runs_dir: Path, lock: Path, overview: dict[str, Any], agent_ends: str | None,
           now: dt.datetime, now_local: dt.datetime) -> Decision:  # fmt: skip
    """What a check does, in this order: nothing if the schedule is off or a run is working; finish a run that was cut
    off (crash, power cut, Mac closed); else start a new one only after `run_at` and only when the calendar needs
    filling. `now` is UTC, `now_local` is the Mac's clock."""
    if not cfg.enabled:
        return Decision("skip", "the schedule is switched off (schedule.yaml: enabled: false)")
    working = live_run(lock)
    if working:
        return Decision("skip", f"run {working} is working")
    cut, note = unfinished_run(cfg, runs_dir, now)
    if cut is not None:
        if cut.resumes >= cfg.max_resumes:
            return Decision("giveup", f"run {cut.id} did not finish after {cut.resumes} tries", cut.id, cut.publish)
        why = f"run {cut.id} was cut off: finishing it (try {cut.resumes + 1} of {cfg.max_resumes})"
        return Decision("resume", why, cut.id, cut.publish)
    if f"{now_local:%H:%M}" < cfg.run_at:
        return Decision("skip", f"a new run does not start before {cfg.run_at} (it is {now_local:%H:%M})")
    last = last_scheduled_run(runs_dir, abandoned_before=now - dt.timedelta(hours=cfg.resume_within_hours))
    ok, why = due(cfg, overview, last, now, agent_ends)
    return Decision("run" if ok else "skip", (note + "; " if note else "") + why)


def plist_for(cfg: ScheduleCfg, uv: str, path_env: str) -> dict[str, Any]:
    """The launchd job: at every login and every `check_every_minutes`, `uv run intel studio run --if-due`. The check
    itself decides (a run is due, a cut-off run waits to be finished, or nothing to do), so a Mac that was off, asleep
    or crashed catches up at the next check. Logs to data/."""
    log = REPO / "data" / "studio" / "schedule.log"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            uv,
            "run",
            "--quiet",
            "--directory",
            str(REPO),
            "intel",
            "studio",
            "run",
            "--if-due",
            "--trigger",
            "schedule",
        ],  # fmt: skip
        "StartInterval": cfg.check_every_minutes * 60,
        "WorkingDirectory": str(REPO),
        "EnvironmentVariables": {"PATH": path_env},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": True,
    }


def reminder_plist_for(cfg: ScheduleCfg, uv: str, path_env: str) -> dict[str, Any]:
    """The reminder: every day at `remind_at`, `intel studio remind`, which notifies only when something waits."""
    hour, minute = (int(x) for x in cfg.remind_at.split(":"))
    log = REPO / "data" / "studio" / "schedule.log"
    return {
        "Label": REMIND_LABEL,
        "ProgramArguments": [uv, "run", "--quiet", "--directory", str(REPO), "intel", "studio", "remind"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "WorkingDirectory": str(REPO),
        "EnvironmentVariables": {"PATH": path_env},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": False,
    }


def install(
    cfg: ScheduleCfg, uv: str, path_env: str, plist_path: Path = PLIST, remind_path: Path | None = None
) -> Path:
    """The daily check, and the daily reminder next to it (`remind_path`, default beside the check's file)."""
    remind = remind_path or plist_path.with_name(f"{REMIND_LABEL}.plist")
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(plist_for(cfg, uv, path_env)))
    remind.write_bytes(plistlib.dumps(reminder_plist_for(cfg, uv, path_env)))
    if sys.platform == "darwin":
        for p in (plist_path, remind):
            subprocess.run(["launchctl", "unload", str(p)], check=False, capture_output=True)  # noqa: S603, S607
            subprocess.run(["launchctl", "load", str(p)], check=True, capture_output=True)  # noqa: S603, S607
    return plist_path


def uninstall(plist_path: Path = PLIST) -> bool:
    remind = plist_path.with_name(f"{REMIND_LABEL}.plist")
    found = False
    for p in (plist_path, remind):
        if not p.exists():
            continue
        found = True
        if sys.platform == "darwin":
            subprocess.run(["launchctl", "unload", str(p)], check=False, capture_output=True)  # noqa: S603, S607
        p.unlink()
    return found
