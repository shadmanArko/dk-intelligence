"""How far ahead the calendar is already planned by the studio, and where the next plan should start.

Reads the studio's own posts in the publisher (source api, any state that still holds its slot). The owner's
hand-planned Sheet posts do not count: they say nothing about what the studio has made. Pure functions over rows, so
each situation is a test; `fetch_rows` is the one place that talks to the publisher.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Any
from zoneinfo import ZoneInfo

from intel.studio.config import StudioConfig

GONE = ("cancelled", "failed", "expired", "invalid", "rejected")  # states that no longer hold their slot
WINDOW_DAYS = 7  # a week of studio posts is about 105: always under the publisher's page of 200
MAX_WINDOWS = 12  # look at most 12 weeks ahead
EMPTY_STOP = 2  # stop after this many empty weeks in a row


def fetch_rows(client: Any, now: dt.datetime) -> list[dict[str, Any]] | None:
    """The studio's posts from a few hours ago on, read one week at a time (so the result never depends on the
    publisher's ordering or page size). Stops after two empty weeks. None when the publisher cannot be reached."""
    rows: list[dict[str, Any]] = []
    start = (now - dt.timedelta(hours=6)).astimezone(dt.UTC)
    empty = 0
    try:
        for _ in range(MAX_WINDOWS):
            end = start + dt.timedelta(days=WINDOW_DAYS)
            window = {"from": start.isoformat(timespec="seconds"), "to": end.isoformat(timespec="seconds")}
            page = client.calendar(source="api", limit=200, **window).get("posts") or []
            rows += page
            empty = 0 if page else empty + 1
            if empty >= EMPTY_STOP:
                break
            start = end
    except Exception:  # noqa: BLE001 - an unreachable publisher is reported as unknown, never a crash
        return None
    seen: set[tuple[Any, ...]] = set()
    unique = []
    for r in rows:  # a post on a window edge can appear twice
        key = (r.get("post_key"), r.get("platform"), r.get("slot"))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _local_day(slot: str, tz: ZoneInfo) -> dt.date:
    when = dt.datetime.fromisoformat(slot)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return when.astimezone(tz).date()


def coverage(cfg: StudioConfig, rows: list[dict[str, Any]] | None, now: dt.datetime) -> dict[str, Any]:
    """{scheduled_through, days_ahead, days: [{date, posts, waiting}], gaps, next_start, first_possible, sentence}.

    `rows` None means the publisher could not be reached: the answer then says so and proposes the plan's own first day.
    """
    tz = ZoneInfo(cfg.slots.timezone)
    today = now.astimezone(tz).date()
    first_possible = today + dt.timedelta(days=cfg.plan.first_day_offset)
    if rows is None:
        return {"known": False, "scheduled_through": None, "days_ahead": 0, "days": [], "gaps": [],
                "first_possible": first_possible.isoformat(), "next_start": first_possible.isoformat(),
                "sentence": "Cannot reach the publisher, so I cannot tell what is already scheduled."}  # fmt: skip
    posts: Counter[dt.date] = Counter()
    waiting: Counter[dt.date] = Counter()
    for r in rows:
        if r.get("source") not in (None, "api") or not r.get("slot") or r.get("state") in GONE:
            continue
        day = _local_day(str(r["slot"]), tz)
        if day < first_possible:
            continue  # today's and earlier posts are history, not a plan
        posts[day] += 1
        if r.get("state") == "awaiting_approval":
            waiting[day] += 1
    if not posts:
        return {"known": True, "scheduled_through": None, "days_ahead": 0, "days": [], "gaps": [],
                "first_possible": first_possible.isoformat(), "next_start": first_possible.isoformat(),
                "sentence": f"Nothing is scheduled yet. A new plan starts {first_possible:%a %d %b}."}  # fmt: skip
    through = max(posts)
    covered = [first_possible + dt.timedelta(days=i) for i in range((through - first_possible).days + 1)]
    gaps = [d.isoformat() for d in covered if d not in posts]
    nxt = through + dt.timedelta(days=1)
    n = (through - today).days
    return {
        "known": True,
        "scheduled_through": through.isoformat(),
        "days_ahead": n,
        "days": [{"date": d.isoformat(), "posts": posts[d], "waiting": waiting[d]} for d in sorted(posts)],
        "gaps": gaps,
        "first_possible": first_possible.isoformat(),
        "next_start": nxt.isoformat(),
        "sentence": f"Already scheduled through {through:%a %d %b} ({n} day{'s' if n != 1 else ''} ahead, "
        f"{sum(posts.values())} posts). A new plan starts {nxt:%a %d %b}."
        + (f" Days with nothing: {', '.join(g[5:] for g in gaps)}." if gaps else ""),
    }


def resolve_start(cfg: StudioConfig, cov: dict[str, Any], wanted: dt.date | None) -> tuple[dt.date, str]:
    """The first day to plan and why: the owner's date wins (never in the past), else the day after what exists."""
    first = dt.date.fromisoformat(cov["first_possible"])
    if wanted is not None:
        day = max(wanted, first)
        note = ", moved to the first possible day" if day != wanted else ""
        return day, f"starting {day:%a %d %b} (your date{note})"
    day = dt.date.fromisoformat(cov["next_start"])
    return day, f"starting {day:%a %d %b} (the day after what is already scheduled)"
