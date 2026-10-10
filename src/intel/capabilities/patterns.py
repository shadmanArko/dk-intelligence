"""Hidden patterns: what competitors' customers miss that we do well (architecture §1.4, P4.3).

No new model: a join of three things we already have.
1. Aspects where competitors' reviews are most negative (weakness miner, via competitor_complaints).
2. Our own posts about the same aspect (aspect tags from the same miner's dictionary, set at pack build) and how they
   did against our typical post (`vs_our_median`).
3. Optionally, community-map gap areas (a high area score, no biryani listing) for area-targeted ads (decision D2).

An aspect is `proven` when at least one of our posts about it beat our median, `untested` when we have never posted
about it, and `weak` when we did and it underperformed. Patterns are hints to test, never claims: each says so.
"""

from __future__ import annotations

from typing import Any

MIN_MENTIONS = 30  # an aspect mentioned fewer times is noise
QUOTE = 140
NOTE = "a pattern to test, not a fact: verify before claiming anything about competitors or us"


def opportunities(
    complaints: list[dict[str, Any]],
    own_posts: list[dict[str, Any]],
    gap_areas: list[dict[str, Any]] | None = None,
    top: int = 3,
) -> list[dict[str, Any]]:
    """complaints: [{aspect, label, mentions, negative_rate}]; own_posts: [{cite, text, aspects, perf{vs_our_median}}];
    gap_areas: [{name, district, score}] (only when allowed). Ranked: proven first, then untested, by how negative
    competitors' reviews are."""
    out = []
    for c in complaints:
        if int(c.get("mentions") or 0) < MIN_MENTIONS:
            continue
        ours = [p for p in own_posts if c["aspect"] in (p.get("aspects") or [])]
        rated = [p for p in ours if (p.get("perf") or {}).get("vs_our_median") is not None]
        winners = sorted(
            (p for p in rated if p["perf"]["vs_our_median"] >= 1.0), key=lambda p: -p["perf"]["vs_our_median"]
        )
        status = "proven" if winners else "untested" if not ours else "weak"
        item: dict[str, Any] = {
            "aspect": c["aspect"],
            "label": c.get("label", c["aspect"]),
            "competitors_negative_rate": round(float(c["negative_rate"]), 3),
            "status": status,
            "our_posts": [
                {
                    "cite": p["cite"],
                    "vs_our_median": p["perf"]["vs_our_median"],
                    "text": p["text"] if len(p["text"]) <= QUOTE else p["text"][: QUOTE - 1] + "…",
                }
                for p in winners[:2]
            ],
            "our_posts_on_aspect": len(ours),
            "note": NOTE,
        }
        if gap_areas:
            item["gap_areas"] = [{k: a.get(k) for k in ("name", "district")} for a in gap_areas[:3]]
        out.append(item)
    rank = {"proven": 0, "untested": 1, "weak": 2}
    out.sort(key=lambda o: (rank[o["status"]], -o["competitors_negative_rate"]))
    return out[:top]
