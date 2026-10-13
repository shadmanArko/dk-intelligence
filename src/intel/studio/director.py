"""The creative director: one look per idea, chosen like a head of creative would, enforced like code must.

    per day (3 ideas together, so the day has contrast): the director agent sees each idea's angle, the platforms
    to and how each platform's native post is made (craft.yaml), the templates that fit, what the last posts used, and
    the footage on hand
    → a treatment per idea: template, variant, music hint, pace, why
    → code checks it: a template that fits the format, not the same look as the post before, no template over its share
      of the plan, a variant that differs from the last time. The director proposes; code disposes.

The template registry belongs to the reels engine (`list_templates.template_registry`); with an older engine that has no
registry the director works from the one video template and the slide templates it does list.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from intel.studio import requests, slots
from intel.studio.ai import AI
from intel.studio.config import StudioConfig
from intel.studio.runlog import RunLog
from intel.studio.state import RunState

SLIDE_SIZE = {"carousel": "carousel", "image": "album", "story": "story"}  # idea format → the slide size it renders at


def template_registry(vocab: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """name → meta for every template the engine offers. An engine without a registry lists its slide templates and
    has the one video template."""
    reg = vocab.get("template_registry")
    if isinstance(reg, dict) and reg:
        return {k: dict(v) for k, v in reg.items()}
    out: dict[str, dict[str, Any]] = {
        "beat-reel": {
            "name": "beat-reel",
            "family": "video",
            "formats": ["9:16", "4:5"],
            "variants": [],
            "summary": "footage cut to the beat of the music",
            "music": "beat-synced",
            "needs": "footage",
            "duration_s": [10, 30],
            "designer_brief": "",
        }  # fmt: skip
    }
    for name in vocab.get("templates", {}).get("slides") or {}:
        out[name] = {"name": name, "family": "slides", "formats": ["carousel", "album", "story"], "variants": [],
                     "summary": f"a {name} slide", "needs": "photos", "designer_brief": ""}  # fmt: skip
    return out


def candidates(cfg: StudioConfig, registry: dict[str, dict[str, Any]], idea_format: str) -> list[str]:
    """The templates that can make this format: video templates that render at its size, slide templates that have its
    size. Sorted, so a fixed registry always gives the same list."""
    route = cfg.routes.formats[idea_format]
    if route.make == "video":
        size = route.render[1]
        return sorted(n for n, m in registry.items() if m.get("family") == "video" and size in m.get("formats", []))
    size = SLIDE_SIZE.get(idea_format, "album")
    return sorted(n for n, m in registry.items() if m.get("family") == "slides" and size in m.get("formats", []))


def surface(cfg: StudioConfig, idea_format: str, platform: str) -> str:
    """The craft.yaml key for what this idea becomes on this platform (reel, carousel, feed, story, video, short)."""
    target = cfg.routes.formats[idea_format].targets[platform]
    if platform == "youtube":
        return "video" if idea_format == "landscape" else "short"
    if platform == "instagram" and idea_format == "image":
        return "feed"
    return target if target in cfg.craft.platforms.get(platform, {}) else "video"


def craft_slices(cfg: StudioConfig, idea_format: str, platforms: list[str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for p in platforms:
        entry = cfg.craft.platforms.get(p, {})
        out[p] = {"role": str(entry.get("role", "")), "how": str(entry.get(surface(cfg, idea_format, p), ""))}
    return out


class Variety:
    """What the feed has looked like: a small ledger of past templates plus this run's choices. Pure counting."""

    def __init__(self, path: Path | None, recent_window: int) -> None:
        self.path = path
        self.window = recent_window
        self.rows: list[dict[str, Any]] = json.loads(path.read_text()) if path and path.exists() else []

    def recent(self) -> list[dict[str, Any]]:
        return self.rows[-self.window :] if self.window else []

    def record(self, job_id: str, template: str, variant: str | None, now: dt.datetime | None = None) -> None:
        self.rows = [r for r in self.rows if r["job_id"] != job_id] + [
            {
                "job_id": job_id,
                "template": template,
                "variant": variant,
                "at": (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds"),
            }
        ]
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.rows[-300:], indent=1))


def _pick(options: list[str], seed: str) -> str:
    return options[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(options)]


def settle(
    cfg: StudioConfig,
    wanted: dict[str, Any] | None,
    options: list[str],
    registry: dict[str, dict[str, Any]],
    used: Counter[str],
    last_variant: dict[str, str],
    previous: str | None,
    seed: str,
    total: int,
) -> dict[str, Any]:
    """The director's wish (or none) made valid and varied. Returns {template, variant, adjusted: [reasons]}."""
    adjusted: list[str] = []
    template = str((wanted or {}).get("template") or "")
    if template not in options:
        if template:
            adjusted.append(f"{template!r} cannot make this format")
        template = ""
    share = cfg.craft.director.max_template_share
    cap = max(2, int(share * total + 0.999)) if total >= 4 else total  # never block a tiny plan
    fresh = [t for t in options if used[t] < cap and t != previous] or [t for t in options if used[t] < cap] or options
    if template and (used[template] >= cap or template == previous) and len(options) > 1:
        adjusted.append(f"{template} was used too much or just before: changed")
        template = ""
    if not template:
        least = min(used[t] for t in fresh)
        template = _pick(sorted(t for t in fresh if used[t] == least), seed)
    variants = [str(v) for v in registry.get(template, {}).get("variants", [])]
    variant = str((wanted or {}).get("variant") or "") if template == str((wanted or {}).get("template") or "") else ""
    if variants and (variant not in variants or variant == last_variant.get(template)):
        rest = [v for v in variants if v != last_variant.get(template)] or variants
        variant = _pick(rest, seed + template)
        adjusted.append(f"variant set to {variant}")
    elif not variants:
        variant = ""
    return {"template": template, "variant": variant, "adjusted": adjusted}


def compact_candidates(registry: dict[str, dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    keys = ("summary", "variants", "duration_s", "music", "needs")
    return [{"template": n, **{k: registry[n][k] for k in keys if k in registry[n]}} for n in names]


def plan_treatments(
    ai: AI,
    cfg: StudioConfig,
    state: RunState,
    log: RunLog,
    ideas: list[slots.Idea],
    registry: dict[str, dict[str, Any]],
    variety: Variety,
    inventory: dict[str, Any],
    performance: dict[str, Any] | None,
) -> None:
    """A treatment for every idea that has an angle. One director call per day; code settles each answer."""
    used: Counter[str] = Counter(r["template"] for r in variety.recent())
    last_variant: dict[str, str] = {r["template"]: r["variant"] for r in variety.rows if r.get("variant")}
    live = [i for i in ideas if "angle" in state.idea(i.idea_id) and not state.held_whole(i.idea_id)]
    total = len(live)
    previous: str | None = variety.rows[-1]["template"] if variety.rows else None
    by_day: dict[dt.date, list[slots.Idea]] = {}
    for i in live:
        by_day.setdefault(i.date, []).append(i)
    for day, day_ideas in sorted(by_day.items()):
        todo = [i for i in day_ideas if not state.idea(i.idea_id).get("treatment")]
        wished: dict[str, dict[str, Any]] = {}
        if todo:
            task = {
                "date": day.isoformat(),
                "ideas": [
                    {"idea_id": i.idea_id, "format": i.format, "platforms": list(i.slots),
                     "angle": state.idea(i.idea_id).get("angle"), "pillar": state.idea(i.idea_id).get("pillar"),
                     "templates": compact_candidates(registry, candidates(cfg, registry, i.format)),
                     "craft": craft_slices(cfg, i.format, list(i.slots))}
                    for i in todo
                ],
                "recent_templates": [r["template"] for r in variety.recent()][-12:],
                "plan_so_far": dict(used), "footage_on_hand": inventory,
                "what_worked": {p: (performance or {}).get(p, {}).get("works", [])[:2] for p in cfg.plan.platforms},
            }  # fmt: skip
            res = ai.call(f"director:{day.isoformat()}", "director", task)
            for t in (res.answer or {}).get("ideas") or []:
                if isinstance(t, dict) and t.get("idea_id"):
                    wished[str(t["idea_id"])] = t
        for i in day_ideas:
            row = state.idea(i.idea_id)
            if row.get("treatment"):
                used[row["treatment"]["template"]] += 1
                previous = row["treatment"]["template"]
                continue
            options = candidates(cfg, registry, i.format) or [
                cfg.craft.director.defaults.get(
                    "video" if cfg.routes.formats[i.format].make == "video" else "slides", ""
                )
            ]
            chosen = settle(
                cfg, wished.get(i.idea_id), options, registry, used, last_variant, previous, i.job_id, total
            )
            wish = wished.get(i.idea_id) or {}
            request = str(wish.get("style_request") or "").strip()
            if request:
                requests.add(i.idea_id, "director", request, i.format)
            treatment = {**chosen, "music": wish.get("music") if isinstance(wish.get("music"), dict) else None,
                         "pace": str(wish.get("pace") or ""), "why": str(wish.get("why") or "")[:300],
                         "directed": i.idea_id in wished}  # fmt: skip
            state.update(i.idea_id, treatment=treatment)
            used[chosen["template"]] += 1
            last_variant[chosen["template"]] = chosen["variant"]
            previous = chosen["template"]
            log.emit(i.idea_id, "treatment", **{k: v for k, v in treatment.items() if k != "why"})
