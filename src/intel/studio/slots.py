"""Slots: which days, which ideas, which window and clock time for each platform. Pure code, no I/O.

    days (real dates from today + N, never weekday names)
      → probes (a fixed caption per day × format × window, to ask the content scorer where each idea can pass)
      → assignment (the day's ideas to the windows that score best; an idea that passes nowhere is reported)
      → one `Idea` per slot with a clock time for every platform its format reaches

The scorer reads a caption's shape, not its words (measured 2026-10-12), so a probe with the usual caption tells what
any caption would score in that slot: no writer is spent on a slot that cannot pass.
"""

from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import asdict, dataclass
from typing import Any
from zoneinfo import ZoneInfo

from intel.studio.config import StudioConfig

# A caption of the usual shape (3 lines, 3 hashtags) that passes every brand rule.
PROBE = (
    "Kacchi aus Berlin, frisch aus dem Topf.\nSlow-cooked kacchi biryani from Berlin.\n"
    "#kacchibiryani #berlinfood #biryaniberlin"
)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SCORABLE = frozenset({"facebook", "instagram"})  # the content scorer's platforms (dk_intel capabilities.types.SCORABLE)


@dataclass(frozen=True)
class Idea:
    idea_id: str  # d01i1: day 1 of the plan, idea 1
    date: dt.date
    n: int
    format: str
    window: str
    slots: dict[str, str]  # platform → ISO time with the Berlin offset, only the platforms this format reaches
    job_id: str  # the publisher's post_key: one post per idea, one variant per platform
    score: float | None = None  # the scorer's chance for the primary slot (None: not scorable)

    def lead(self, primary: str) -> str:
        """The platform whose rules write and time this idea: the plan's primary one, or the first it reaches when the
        format does not go there (a 16:9 video goes to YouTube and Facebook, not Instagram)."""
        return primary if primary in self.slots else next(iter(self.slots))

    @property
    def weekday(self) -> str:
        return WEEKDAYS[self.date.weekday()]


def idea_to_dict(idea: Idea) -> dict[str, Any]:
    d = asdict(idea)
    d["date"] = idea.date.isoformat()
    return d


def idea_from_dict(d: dict[str, Any]) -> Idea:
    return Idea(**{**d, "date": dt.date.fromisoformat(d["date"])})


@dataclass(frozen=True)
class Dropped:
    idea_id: str
    date: dt.date
    n: int
    format: str
    why: str

    def as_dict(self) -> dict[str, Any]:
        return {"slot_id": self.idea_id, "date": self.date.isoformat(), "format": self.format, "why": self.why}


def plan_days(cfg: StudioConfig, today: dt.date) -> list[dt.date]:
    """The real dates of the plan, cut so that days × ideas_per_day stays within max_ideas_per_run."""
    p = cfg.plan
    n_days = min(p.horizon_days, max(1, p.max_ideas_per_run // p.ideas_per_day))
    start = today + dt.timedelta(days=p.first_day_offset)
    return [start + dt.timedelta(days=i) for i in range(n_days)]


def format_of(cfg: StudioConfig, n: int, day_index: int = 0) -> str:
    """The format of idea n on the plan's day number day_index (0-based): the rotation row for that day if there is one
    have one (so the days differ), else the same mix every day."""
    rotation = cfg.plan.format_rotation
    if rotation:
        return rotation[day_index % len(rotation)][n - 1]
    mix = cfg.plan.format_mix
    return mix[(n - 1) % len(mix)]


def clock(cfg: StudioConfig, platform: str, window: str, day: dt.date) -> str:
    """ISO time with the Berlin offset for that day (DST handled by the time zone database)."""
    hh, mm = cfg.slots.times[platform][window].split(":")
    return dt.datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=ZoneInfo(cfg.slots.timezone)).isoformat()


def job_id(day: dt.date, n: int, suffix: str = "") -> str:
    """Stable and valid for both systems, e.g. 2026w42-tue13-i1. Same slot, same key: a re-run skips what exists. A
    rejected post cannot reuse its key, so a redo passes a suffix (r2)."""
    year, week, _ = day.isocalendar()
    tail = "".join(c for c in suffix.lower() if c.isalnum())
    return f"{year}w{week:02d}-{WEEKDAYS[day.weekday()]}{day.day:02d}-i{n}" + (f"-{tail}" if tail else "")


Probe = tuple[dt.date, str, str]  # (date, format, window)


def probes(cfg: StudioConfig, days: list[dt.date]) -> list[Probe]:
    """Every (day, format, window) the assignment may need, once each."""
    seen: dict[Probe, None] = {}
    for d_index, day in enumerate(days):
        for n in range(1, cfg.plan.ideas_per_day + 1):
            for window in cfg.slots.windows:
                seen[(day, format_of(cfg, n, d_index), window)] = None
    return list(seen)


def probe_ref(p: Probe) -> str:
    return f"p{p[0]:%m%d}{p[1]}{p[2]}"[:24]  # 23 characters with the default names


def probe_drafts(cfg: StudioConfig, items: list[Probe]) -> list[dict[str, Any]]:
    """The drafts to send to dk_intel evaluate_drafts for these probes (primary platform, the usual caption)."""
    primary = cfg.plan.primary_platform
    out = []
    for day, fmt, window in items:
        draft: dict[str, Any] = {"ref": probe_ref((day, fmt, window)), "platform": primary, "caption": PROBE,
                                 "content_type": cfg.routes.formats[fmt].content_type}  # fmt: skip
        if primary in SCORABLE:
            draft["planned_posted_at"] = clock(cfg, primary, window, day)
        out.append(draft)
    return out


Scores = dict[Probe, tuple[float | None, bool]]  # probe → (probability, passes)


def read_scores(items: list[Probe], results: list[dict[str, Any]]) -> Scores:
    """dk_intel's answer → (probability, passes). Below the bar means 'rewrite' with no errors and no warnings; a probe
    the scorer did not answer for counts as passing (nothing says it cannot)."""
    got = {r.get("ref"): r for r in results}
    out: Scores = {}
    for p in items:
        r = got.get(probe_ref(p))
        if r is None:
            out[p] = (None, True)
            continue
        prob = ((r.get("scores") or {}).get("content_scorer") or {}).get("probability")
        below = r.get("verdict") == "rewrite" and not r.get("errors") and not r.get("warnings")
        out[p] = (float(prob) if isinstance(prob, int | float) else None, not below)
    return out


def assign(
    cfg: StudioConfig, days: list[dt.date], scores: Scores, suffix: str = ""
) -> tuple[list[Idea], list[Dropped]]:
    """For each day, put its ideas into distinct windows: as many passing as possible, then the highest total score,
    then the order of preference in slots.yaml. Three ideas and three windows are six permutations: brute force."""
    ideas: list[Idea] = []
    dropped: list[Dropped] = []
    windows = cfg.slots.windows
    for d_index, day in enumerate(days, start=1):
        formats = [(n, format_of(cfg, n, d_index - 1)) for n in range(1, cfg.plan.ideas_per_day + 1)]

        def rank(
            perm: tuple[str, ...], day: dt.date = day, formats: list[tuple[int, str]] = formats
        ) -> tuple[Any, ...]:
            cells = [scores.get((day, fmt, w), (None, True)) for (_, fmt), w in zip(formats, perm, strict=True)]
            return (sum(ok for _, ok in cells), sum(p or 0.0 for p, _ in cells),
                    -sum(windows.index(w) * (i + 1) for i, w in enumerate(perm)))  # fmt: skip

        best = max(itertools.permutations(windows, len(formats)), key=rank)
        for (n, fmt), window in zip(formats, best, strict=True):
            prob, ok = scores.get((day, fmt, window), (None, True))
            idea_id = f"d{d_index:02d}i{n}"
            if not ok:
                pct = round((prob or 0) * 100)
                where = window.replace("_", " ")
                why = (
                    f"a {fmt} on {WEEKDAYS[day.weekday()]} scores {pct}% in the {where}, the window left after the "
                    "day's other ideas took the better ones, below the bar: change the format of one idea that "
                    "day, or add a window"
                )
                dropped.append(Dropped(idea_id, day, n, fmt, why))
                continue
            slots = {p: clock(cfg, p, window, day) for p in cfg.platforms_for(fmt)}
            ideas.append(Idea(idea_id, day, n, fmt, window, slots, job_id(day, n, suffix), prob))
    return ideas, dropped
