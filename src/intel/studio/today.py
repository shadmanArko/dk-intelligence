"""The Today page: everything that waits for the owner, in one list.

Pure functions over injected readers (what the publisher says, what sits in the reels folder, what the run folders
hold), so
each situation is a test. Nothing here changes anything: the console's buttons do, after a confirmation.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from intel.studio.config import REPO, ScheduleCfg


@dataclass
class Readers:
    """Everything the page looks at, behind small functions (the console wires the real ones, tests wire fakes)."""

    calendar: Callable[[], list[dict[str, Any]] | None]  # the studio's posts in the publisher; None = unreachable
    pending_proposals: Callable[[], list[dict[str, Any]]]  # the curator's proposals waiting in the reels folder
    unlabelled_footage: Callable[[], int]  # clips and photos with no mood or shot knowledge yet
    last_summary: Callable[[], str | None]
    running: Callable[[], str | None]
    schedule_installed: Callable[[], bool]
    now: Callable[[], dt.datetime]
    disk_free: Callable[[], float | None] = lambda: None  # the share of this Mac's disk that is free (0 to 1)


def _item(
    id_: str, level: str, title: str, detail: str, actions: list[dict[str, Any]] | None = None, count: int | None = None
) -> dict[str, Any]:
    return {"id": id_, "level": level, "title": title, "detail": detail, "actions": actions or [], "count": count}


def owner_decisions(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or REPO / "config" / "studio" / "owner-todo.yaml"
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    return [d for d in (data or {}).get("decisions", []) if not d.get("done")]


def build(r: Readers, cfg: ScheduleCfg, decisions: list[dict[str, Any]],
          cov: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """The checklist, most pressing first. level: todo (needs you), info (good to know), ok (nothing to do)."""
    items: list[dict[str, Any]] = []
    rows = r.calendar()
    if rows is None:
        items.append(
            _item("drafts", "info", "Drafts", "Cannot reach the publisher right now: check the review page directly.")
        )
    else:
        waiting = [x for x in rows if x.get("source") == "api" and x.get("state") == "awaiting_approval"]
        by_day = Counter(str(x.get("slot", ""))[:10] for x in waiting)
        if waiting:
            days = ", ".join(f"{d[5:]}: {n}" for d, n in sorted(by_day.items()))
            items.append(
                _item(
                    "drafts",
                    "todo",
                    f"Approve {len(waiting)} drafts",
                    f"Waiting by day: {days}. Open the review page from your 08:00 Telegram message "
                    "(it carries a fresh link) and approve or reject there.",
                    count=len(waiting),
                )
            )
        else:
            items.append(_item("drafts", "ok", "No drafts waiting", "Nothing to approve."))
    free = r.disk_free()
    if free is not None and free < 0.15:
        items.append(
            _item(
                "disk",
                "todo",
                f"This Mac's disk is nearly full ({free * 100:.0f}% free)",
                "Old generated videos, knowledge packs and upload copies can go. Your footage and photos never do.",
                [
                    {
                        "label": "Free disk space",
                        "api": "/api/ops/start",
                        "body": {"action": "cleanup", "confirm": True},
                        "confirm": "Delete old generated videos and packs that nothing needs?",
                    }
                ],
            )
        )
    pending = r.pending_proposals()
    if pending:
        lines = [
            f"{p['id']}: " + "; ".join(str(m.get("description", ""))[:70] for m in p.get("moments", [])[:2])
            for p in pending[:5]
        ]
        items.append(
            _item(
                "curator",
                "todo",
                f"Accept or reject {len(pending)} footage proposals",
                "The curator looked at these clips and proposes what is in them. " + " | ".join(lines),
                [
                    {"label": "Accept all", "api": "/api/curate/accept-all", "confirm": "Accept every proposal?"},
                    {"label": "Reject all", "api": "/api/curate/reject-all", "confirm": "Throw all proposals away?"},
                ],
                count=len(pending),
            )
        )
    left = r.unlabelled_footage()
    if left:
        items.append(
            _item(
                "enrich",
                "info",
                f"{left} clips and photos have no shot knowledge yet",
                "The curator can describe 20 more now (about 160,000 tokens, about 5 minutes). You then accept "
                "the proposals above.",
                [
                    {
                        "label": "Curator: 20 more",
                        "api": "/api/curate/enrich",
                        "confirm": "Spend about 160,000 tokens on 20 clips?",
                    }
                ],
                count=left,
            )
        )
    running = r.running()
    summary = r.last_summary()
    if running:
        items.append(_item("run", "info", "A run is working", f"Run {running} is in progress: watch it below."))
    elif summary:
        first = summary.strip().splitlines()[0] if summary.strip() else "last run"
        items.append(
            _item("run", "info", "Last run", first.lstrip("# ") + " (full report: data/studio/last-summary.md)")
        )
    start_body: dict[str, Any] = {"publish": "drafts", "confirm": True}
    if cov and cov.get("known"):
        start_body["start"] = cov["next_start"]
        short = cov["days_ahead"] < cfg.lead_days
        items.append(
            _item(
                "coverage",
                "todo" if short else "ok",
                "Plan the next days" if short else "The calendar is planned ahead",
                cov["sentence"],
                [{"label": "Open the Runs page", "href": "/runs"}] if short else [],
            )
        )
    items.append(
        _item(
            "start",
            "info",
            "Make a new plan",
            "Creates drafts for the next days (about 15 minutes, about 550,000 tokens). "
            + ("It starts the day after what is already scheduled. " if cov and cov.get("known") else "")
            + "They wait for your approval; nothing posts by itself.",
            [
                {
                    "label": "Run now (drafts)",
                    "api": "/api/run",
                    "body": start_body,
                    "confirm": "This creates drafts in your Calendar"
                    + (f", starting {cov['next_start']}" if cov and cov.get("known") else "")
                    + ". Continue?",
                }
            ],
        )
    )
    if r.schedule_installed():
        items.append(
            _item(
                "schedule",
                "ok",
                "Daily check is installed",
                f"While your Mac is on it checks every {cfg.check_every_minutes} minutes. It finishes a run that "
                f"was cut off, and after {cfg.run_at} it makes a plan only when yours is running out. A Mac that "
                "was off catches up when it is on.",
            )
        )
    else:
        items.append(
            _item(
                "schedule",
                "info",
                "Daily check is not installed",
                "Installing lets the studio top up the calendar by itself (drafts only). Do it after you have "
                "approved a day or two.",
                [
                    {
                        "label": "Install daily check",
                        "api": "/api/schedule/install",
                        "confirm": "Install the daily check on this Mac?",
                    }
                ],
            )
        )
    for d in decisions:
        items.append(_item(f"decision-{d['id']}", "todo", str(d["title"]), str(d["detail"])))
    if r.now().weekday() == 6:  # Sunday
        items.append(
            _item(
                "analyst",
                "info",
                "Weekly: what worked",
                "One call (about 5,000 tokens) writes a plain note on what your posts did.",
                [{"label": "Write the note", "api": "/api/performance/analyst", "confirm": "Write this week's note?"}],
            )
        )
    order = {"todo": 0, "info": 1, "ok": 2}
    items.sort(key=lambda i: order[i["level"]])
    return {
        "items": items,
        "open": sum(1 for i in items if i["level"] == "todo"),
        "as_of": r.now().isoformat(timespec="minutes"),
    }


def reminder_text(today: dict[str, Any]) -> tuple[str, str] | None:
    """(title, message) for the daily reminder, or None when nothing waits for the owner."""
    todo = [i for i in today["items"] if i["level"] == "todo"]
    if not todo:
        return None
    return "Studio: waiting for you", "; ".join(i["title"] for i in todo[:4])


def pending_proposals(reels: Path) -> list[dict[str, Any]]:
    folder = reels / "index" / "curation" / "pending"
    out = []
    for f in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            out.append(json.loads(f.read_text()))
        except ValueError:
            continue
    return out


def unlabelled_footage(reels: Path) -> int:
    path = reels / "index" / "assets.json"
    if not path.exists():
        return 0
    assets = json.loads(path.read_text()).get("assets", [])
    return sum(1 for a in assets if "mood" not in a and a.get("type") in ("video", "photo"))


def calendar_from(client: Any) -> list[dict[str, Any]] | None:
    try:
        rows: list[dict[str, Any]] = client.calendar(source="api", limit=200).get("posts") or []
        return rows
    except Exception:  # noqa: BLE001 - an unreachable publisher is shown as such, never a crash
        return None
