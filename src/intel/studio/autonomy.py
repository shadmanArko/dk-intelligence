"""Earned autonomy, measured (S6). Which KINDS of post does the owner approve, over a recent window?

This only measures. It reads what the owner decided in the publisher (a post that went on to be scheduled or published
was approved; a cancelled one was rejected) and what the studio knew about each idea (pillar, format, template). Per
kind it reports how many were decided, the approval rate, and whether the kind would clear the owner's bar
(autonomy.yaml). The auto-approve path is NOT built: acting on this needs a dedicated publisher key with the approve
right, enforced there in code, and the owner's decision (ADR 0037). `enabled: false` is the shipped state.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from intel.studio.config import AutonomyCfg

APPROVED = frozenset({"scheduled", "publishing", "checking", "published"})
REJECTED = frozenset({"cancelled"})


def idea_kinds(states: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """job_id → {pillar, format, template} from studio run states (later runs win)."""
    out: dict[str, dict[str, str]] = {}
    for state in states:
        for row in state.get("ideas", {}).values():
            idea = row.get("idea") or {}
            if idea.get("job_id"):
                out[str(idea["job_id"])] = {"pillar": str(row.get("pillar") or "?"), "format": str(idea.get("format")),
                                            "template": str((row.get("treatment") or {}).get("template") or "?"),
                                            }  # fmt: skip
    return out


def table(
    rows: list[dict[str, Any]], kinds: dict[str, dict[str, str]], cfg: AutonomyCfg, now: dt.datetime
) -> list[dict[str, Any]]:
    """One row per (kind, value): decided posts, approved, rate, and whether it clears the bar."""
    since = now - dt.timedelta(days=cfg.window_days)
    counts: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        if r.get("source") != "api" or r.get("state") not in APPROVED | REJECTED:
            continue
        slot = dt.datetime.fromisoformat(str(r["slot"])) if r.get("slot") else None
        if slot is None or slot < since:
            continue
        key = kinds.get(str(r.get("post_key")))
        if key is None:
            continue
        for kind in cfg.kinds:
            c = counts.setdefault((kind, key[kind]), [0, 0])
            c[0] += 1
            c[1] += r["state"] in APPROVED
    out = []
    for (axis, value), (decided, approved) in sorted(counts.items()):
        rate = approved / decided
        out.append({"kind": axis, "value": value, "decided": decided, "approved": approved, "rate": round(rate, 2),
                    "clears_bar": decided >= cfg.min_posts and rate >= cfg.approval_rate})  # fmt: skip
    return out
