"""Everything a run reads before any AI is spent, as code calls (0 tokens): the grounding pack, the scorer's answer for
every (day, format, window), the reels vocabulary and footage inventory, the confirmed prices and each platform's
hashtag range. One dict, so it can be frozen to a file and replayed (`--inputs`) for a fair comparison of two versions.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from intel.studio import media, slots
from intel.studio.config import StudioConfig
from intel.studio.ports import ToolPort

EXAMPLES = 2
CHUNK = (
    20  # dk_intel evaluate_drafts takes at most 20 drafts per call: every step that checks many items chunks by this
)


def chunks[T](items: list[T], size: int = CHUNK) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class InputError(Exception):
    """An input a run cannot do without is missing; the message says which."""


def _ok(name: str, value: Any) -> Any:
    if isinstance(value, dict) and "error" in value:
        raise InputError(f"{name}: {value['error']}")
    return value


def evaluate(tools: ToolPort, drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """dk_intel evaluate_drafts for any number of drafts (chunks of 20), results in order. Raises on a refusal."""
    out: list[dict[str, Any]] = []
    for part in chunks(drafts):
        res = _ok("evaluate_drafts", tools.call("dk_intel_writer", "evaluate_drafts", {"drafts": part}))
        out += res.get("results", [])
    return out


def fetch_inputs(
    tools: ToolPort, cfg: StudioConfig, today: dt.date, performance_root: Path | None = None
) -> dict[str, Any]:
    days = slots.plan_days(cfg, today)
    items = slots.probes(cfg, days)
    primary = cfg.plan.primary_platform

    def pack() -> dict[str, Any]:
        args = {"platform": primary, "days": min(cfg.plan.horizon_days, 30)}
        out = _ok("grounding_pack", tools.call("dk_intel_planner", "grounding_pack", args))
        out.pop("prov", None)
        return dict(out)

    def probe_results() -> list[dict[str, Any]]:
        return evaluate(tools, slots.probe_drafts(cfg, items))

    def reels() -> tuple[dict[str, Any], dict[str, Any]]:
        vocab = _ok("list_templates", tools.call("dk_reels_creative", "list_templates", {}))
        inv = _ok("inventory", tools.call("dk_reels_creative", "inventory", {}))
        return vocab, media.compact_inventory(inv)

    def examples(pack_: dict[str, Any]) -> list[dict[str, Any]]:
        cands = example_candidates(pack_)[:6]
        drafts = [{"ref": "x" + c["cite"][:23], "platform": primary, "caption": c["text"]} for c in cands]
        try:
            return pick_examples(cands, evaluate(tools, drafts) if drafts else [])
        except Exception:  # noqa: BLE001 - no examples is better than no run
            return []

    def menu() -> tuple[list[dict[str, str]], list[str]]:
        """(price rows a slide may show, names of menu items that are NOT confirmed available). No facts: no prices and
        nothing known to avoid; the brand check still blocks an unavailable item in every text."""
        try:
            facts = tools.call("dk_intel_writer", "get_facts", {"keys": ["menu"]})
        except Exception:  # noqa: BLE001
            return [], []
        items = facts.get("items", []) if isinstance(facts, dict) else []
        return media.price_items(items), media.unavailable_items(items)

    def hashtags() -> dict[str, dict[str, Any]]:
        """Per platform: the playbook's hashtag range and the caption limits dk_intel checks (first line, emojis)."""
        out: dict[str, dict[str, Any]] = {}
        for p in cfg.plan.platforms:
            try:
                rules: dict[str, Any] = tools.call("dk_intel_planner", "brand_rules",
                                                   {"platform": p, "section": "platform"})  # fmt: skip
                plat = rules.get("platform") or {}
                first = next(iter((plat.get("formats") or {}).values()), None) or {}
                rng = first.get("hashtags")
                out[p] = {
                    "range": [int(rng[0]), int(rng[1])] if isinstance(rng, list) and len(rng) == 2 else None,
                    "first_line_max": plat.get("first_line_max"),
                    "emoji_max": plat.get("emoji_max"),
                }
            except Exception:  # noqa: BLE001 - nothing known: the checker still judges every caption
                out[p] = {}
        return out

    with ThreadPoolExecutor(max_workers=5) as pool:
        f_pack, f_probe, f_reels = pool.submit(pack), pool.submit(probe_results), pool.submit(reels)
        f_menu, f_tags = pool.submit(menu), pool.submit(hashtags)
        pack_, probe, (vocab, inventory) = f_pack.result(), f_probe.result(), f_reels.result()
        (price_rows, unavailable), tags = f_menu.result(), f_tags.result()
        shown = examples(pack_)
    performance = performance_for_prompts(cfg, performance_root)
    return {"today": today.isoformat(), "pack": pack_, "probe_results": probe, "vocab": vocab, "inventory": inventory,
            "prices": price_rows, "unavailable": unavailable, "hashtags": tags, "examples": shown,
            "performance": performance}  # fmt: skip


def performance_for_prompts(cfg: StudioConfig, root: Path | None = None) -> dict[str, Any]:
    """What the numbers support, per platform, in the few lines an agent's task can carry (S4). Read from the latest
    playbook on disk (`uv run intel performance build`); none yet is stated as such, never invented."""
    from intel.performance import playbook

    folder = root or (Path(__file__).resolve().parents[3] / "data" / "performance")
    pb = playbook.latest(folder)
    out = {p: playbook.for_prompt(pb, p) for p in cfg.plan.platforms}
    out["_basis"] = {"pull": (pb or {}).get("pull_id"), "caveats": (pb or {}).get("caveats", [])}
    return out


def platform_slices(cfg: StudioConfig, publish_spec: dict[str, Any], hashtags: dict[str, Any],
                    idea_format: str, platforms: list[str],
                    performance: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:  # fmt: skip
    """What the adapter is told about each platform, all of it from code: the publisher's own caption rules for the
    publishing format this idea takes there, the brand playbook's hashtag range, the options it may write."""
    route = cfg.routes.formats[idea_format]
    out: dict[str, dict[str, Any]] = {}
    for p in platforms:
        fmt = route.targets[p]
        info = (publish_spec.get("platforms") or {}).get(p) or {}
        formats = info.get("formats") or {}
        rules: dict[str, Any] = formats.get(fmt) or formats.get("feed") or next(iter(formats.values()), {})
        caption = rules.get("caption")
        options = rules.get("options") or {}
        allowed = cfg.routes.adapter_options.get(p, [])
        known = hashtags.get(p)
        limits: dict[str, Any] = known if isinstance(known, dict) else {"range": known}  # a list: an older frozen file
        out[p] = {
            "format": fmt,
            "caption_max": (caption or {}).get("max_length"),
            "caption_required": bool((caption or {}).get("required")),
            "notes": (caption or {}).get("note", ""),
            "hashtags": limits.get("range"),
            "first_line_max": limits.get("first_line_max"),
            "emoji_max": limits.get("emoji_max"),
            "option_fields": {k: {"max_length": v.get("max_length")} for k, v in options.items() if k in allowed},
            "feel": cfg.routes.feel.get(p, ""),
            "what_worked": (performance or {}).get(p),
        }
    return out


def example_candidates(pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Our own posts in the pack, best first (by how they did against our usual post)."""
    posts: dict[str, dict[str, Any]] = {}
    for p in pack.get("our_best_posts", []):
        posts[p["cite"]] = {"cite": p["cite"], "text": p["text"], "x": (p.get("perf") or {}).get("vs_our_median", 0)}
    for o in pack.get("opportunities", []):
        for p in o.get("our_posts", []):
            posts.setdefault(p["cite"], {"cite": p["cite"], "text": p["text"], "x": p.get("vs_our_median", 0)})
    return sorted(posts.values(), key=lambda p: -float(p["x"] or 0))


def pick_examples(candidates: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Our best past posts, screened by dk_intel's own checker: a blocked one is never shown, a post that breaks
    today's format is 'voice only' (format_ok false) and sorts last."""
    got = {r.get("ref"): r for r in results}
    out = []
    for c in candidates:
        r = got.get("x" + c["cite"][:23], {})
        if r.get("verdict") in ("blocked", None):
            continue
        out.append({**c, "format_ok": not r.get("errors") and not r.get("warnings")})
    out.sort(key=lambda c: not c["format_ok"])
    return out[:EXAMPLES]
