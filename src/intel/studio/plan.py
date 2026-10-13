"""The strategist level: angles for the ideas, in chunks, with code keeping the plan honest (no repeats, spread
pillars, only citations that exist). Claude Code subagents cannot nest, so the levels above the writers are code
(playbook section 1): this module is the 'lead' that fans out to the strategist and gathers its answers."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from intel.studio import slots
from intel.studio.ai import AI
from intel.studio.config import StudioConfig
from intel.studio.inputs import chunks
from intel.studio.runlog import RunLog
from intel.studio.state import RunState

SIMILAR = 0.75  # two angles whose word sets overlap this much are the same angle


def cites(pack: Any) -> dict[str, str]:
    """Every citation id in the grounding pack → its text (review quotes, our posts)."""
    out: dict[str, str] = {}

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if isinstance(x.get("cite"), str) and isinstance(x.get("text"), str):
                out[x["cite"]] = x["text"]
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(pack)
    return out


def compact_performance(performance: dict[str, Any] | None) -> dict[str, Any]:
    """The strategist's view of the playbook: per platform at most 2 lines that work and 1 that hurts."""
    if not performance:
        return {"status": "no playbook yet"}
    out: dict[str, Any] = {"basis": performance.get("_basis", {}).get("caveats", [])}
    for platform, entry in performance.items():
        if platform.startswith("_"):
            continue
        out[platform] = {"status": entry.get("status"), "works": entry.get("works", [])[:2],
                         "hurts": entry.get("hurts", [])[:1]}  # fmt: skip
    return out


def words(text: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-zäöüß0-9]+", text.lower()) if len(w) > 2)


def same_angle(a: str, b: str) -> bool:
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / len(wa | wb) >= SIMILAR


def normalise(
    answer: dict[str, Any] | None, known: set[str], pillars: set[str], pack_cites: set[str]
) -> dict[str, dict[str, Any]]:
    """An AI answer is data (A13): `id` for `slot_id`, an unknown pillar, a made-up citation are all repaired or
    dropped here. Returns slot_id → {angle, pillar, sources}."""
    out: dict[str, dict[str, Any]] = {}
    for s in (answer or {}).get("slots") or []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("slot_id") or s.get("id") or "")
        angle = str(s.get("angle") or "").strip()
        if sid not in known or not angle or sid in out:
            continue
        pillar = str(s.get("pillar") or "").strip().lower()
        sources = [c for c in s.get("sources") or [] if isinstance(c, str) and c in pack_cites][:3]
        out[sid] = {
            "angle": angle,
            "pillar": pillar if pillar in pillars else "",
            "sources": sources,
            "playbook_note": str(s.get("playbook_note") or "").strip()[:300],
        }
    return out


def plan_angles(ai: AI, cfg: StudioConfig, state: RunState, log: RunLog, ideas: list[slots.Idea],
                pack: dict[str, Any], unavailable: list[str],
                performance: dict[str, Any] | None = None) -> None:  # fmt: skip
    """Give every idea an angle. Chunks of `chunk_ideas`; each chunk sees the angles already chosen. Duplicates and
    missing answers are asked for once more; an idea that still has none is held with that reason."""
    pillars = {p.name for p in cfg.plan.pillars}
    pack_cites = set(cites(pack))
    todo = [i for i in ideas if not state.reached(i.idea_id, "angled") and not state.held_whole(i.idea_id)]
    for k, chunk in enumerate(chunks(todo, cfg.plan.chunk_ideas), start=1):
        node = f"strategist-{k}"
        known = {i.idea_id for i in chunk}
        chosen = {i.idea_id: state.idea(i.idea_id) for i in ideas if "angle" in state.idea(i.idea_id)}
        previous = [r["angle"] for r in chosen.values()]
        counts = Counter(r.get("pillar") or "?" for r in chosen.values())
        pending = list(chunk)
        for attempt in (1, 2):
            if not pending:
                break
            open_slots = [
                {"slot_id": i.idea_id, "date": i.date.isoformat(), "weekday": i.weekday, "format": i.format,
                 "time": i.slots[i.lead(cfg.plan.primary_platform)][11:16], "slot_score": i.score}
                for i in pending
            ]  # fmt: skip
            task = {"slots": open_slots, "grounding_pack": pack, "pillars": [p.model_dump() for p in cfg.plan.pillars],
                    "pillar_counts": dict(counts), "max_pillar_share": cfg.plan.max_pillar_share,
                    "previous": previous, "not_on_the_menu": unavailable,
                    "what_worked": compact_performance(performance)}  # fmt: skip
            res = ai.call(node if attempt == 1 else f"{node}-redo", "strategist", task)
            got = normalise(res.answer, {i.idea_id for i in pending}, pillars, pack_cites)
            retry: list[slots.Idea] = []
            for i in pending:
                row = got.get(i.idea_id)
                if row is None:
                    retry.append(i)
                    continue
                if any(same_angle(row["angle"], p) for p in previous):
                    retry.append(i)  # a repeat of an earlier angle: ask again, once
                    continue
                previous.append(row["angle"])
                counts[row["pillar"] or "?"] += 1
                state.update(i.idea_id, stage="angled", **row)
            if res.error.startswith("budget:"):
                for i in retry:
                    state.hold(i.idea_id, res.error, code="budget")
                break
            pending = retry
        for i in pending:
            if not state.held_whole(i.idea_id) and not state.reached(i.idea_id, "angled"):
                state.hold(i.idea_id, "the strategist gave no usable, different angle for this slot", code="no_angle")
        log.emit("orchestrator", "chunk", chunk=node, ideas=sorted(known), angled=len(known) - len(pending))
