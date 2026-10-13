"""Running without you: when is a run due, and the launchd job that checks every day.

The daily check is cheap (one read of the publisher's calendar, no AI). A run starts only when the calendar needs
filling:
its last planned slot is fewer than `lead_days` ahead, nothing ran in the last `min_days_between` days, and not too many
drafts already wait for approval. Everything it makes is a draft; nothing here approves or posts.
"""

from __future__ import annotations

import datetime as dt
import json
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from intel.studio.config import REPO, ScheduleCfg

LABEL = "com.dhakakacchi.studio"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


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


def last_scheduled_run(runs_dir: Path) -> dt.datetime | None:
    """When the newest run that came from the schedule started (its run folder name is a UTC timestamp)."""
    newest: dt.datetime | None = None
    for folder in runs_dir.glob("*"):
        events = folder / "events.jsonl"
        if not events.exists():
            continue
        head = events.read_text().splitlines()[:1]
        if head and json.loads(head[0]).get("trigger") == "schedule":
            try:
                when = dt.datetime.strptime(folder.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
            except ValueError:
                continue
            newest = max(newest, when) if newest else when
    return newest


def plist_for(cfg: ScheduleCfg, uv: str, path_env: str) -> dict[str, Any]:
    """The launchd job: every day at `run_at`, `uv run intel studio run --if-due --trigger schedule`. Logs to data/."""
    hour, minute = (int(x) for x in cfg.run_at.split(":"))
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
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "WorkingDirectory": str(REPO),
        "EnvironmentVariables": {"PATH": path_env},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": False,
    }


def install(cfg: ScheduleCfg, uv: str, path_env: str, plist_path: Path = PLIST) -> Path:
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(plist_for(cfg, uv, path_env)))
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False, capture_output=True)  # noqa: S603, S607
        subprocess.run(["launchctl", "load", str(plist_path)], check=True, capture_output=True)  # noqa: S603, S607
    return plist_path


def uninstall(plist_path: Path = PLIST) -> bool:
    if not plist_path.exists():
        return False
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False, capture_output=True)  # noqa: S603, S607
    plist_path.unlink()
    return True
