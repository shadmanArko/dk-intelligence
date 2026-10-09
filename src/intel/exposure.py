"""Which insights fields may leave intel-api: an allowlist per model and section (ADR 0025, architecture §2.7).

Used by the read-only route `GET /v1/insights/{name}` and again by the MCP capability layer. A field that isn't
listed here never leaves. Never add these, whatever a model's bundle contains (rule 10, owner-reviewed exceptions):
- community_map: origin counts (`south_asian`, `meat_rice_cultures`, `internationals`), `audience`, `weights`,
  `top_audience`, `orders`, `orders_located`, `lat`/`lon` of areas;
- occasion_planner: `community`, `community_id`, `people`, `districts`, the origin-weighted `score`; the community
  and names of respectful notes;
- creator_matcher: personal `name`, `followers`.
`score` and `gap` of community areas are shown: area-level, origin-derived (D2, "choose areas, never people").

Import-light on purpose (stdlib only): intel-api imports it, so the VPS image stays unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

# A field allowlist: a name maps to True (keep as is) or to a nested allowlist (for dicts, and for lists of dicts).
Allow = Mapping[str, "bool | Allow"]

RESTRICTED: frozenset[str] = frozenset(
    {
        "south_asian",
        "meat_rice_cultures",
        "internationals",
        "audience",
        "audience_rank",
        "weights",
        "top_audience",
        "orders_located",
        "community",
        "community_id",
        "people",
        "followers",
    }
)
"""Keys that must never appear in any exposed output (tested over every section of every model)."""

SMALL_COUNT_KEYS: frozenset[str] = frozenset({"ordered", "orders"})
"""Funnel order counts from 1 to 4 are shown as "<5" (only aggregates leave the funnel module)."""


@dataclass(frozen=True)
class Section:
    path: str  # key in bundle["insights"]
    kind: Literal["list", "object"]
    allow: Allow
    note: str = ""


_COMPLAINT: Allow = {"aspect": True, "label": True, "mentions": True, "negative_rate": True}
_ASPECT: Allow = {
    "aspect": True,
    "label": True,
    "mentions": True,
    "negative": True,
    "negative_rate": True,
    "positive_rate": True,
    "share_of_reviews": True,
    "complaints": True,  # short quotes from public reviews: untrusted third-party text
}
_CREATOR: Allow = {
    "handle": True,
    "url": True,
    "size": True,
    "score": True,
    "berlin_share": True,
    "median_views": True,
    "engagement_rate": True,
    "posts_90d": True,
    "last_post": True,
    "topics": True,
    "examples": {"url": True, "caption": True, "views": True},
}

SECTIONS: dict[str, dict[str, Section]] = {
    "occasion_planner": {
        "summary": Section("", "object", {"from": True, "to": True, "days_covered": True, "calendar_until": True}),
        "picks": Section(
            "picks",
            "list",
            {
                "public_id": True,
                "label": True,
                "names": True,
                "greeting": True,
                "greeting_translit": True,
                "greeting_needs_review": True,
                "first_day": True,
                "last_day": True,
                "start_posting": True,
                "food_role": True,
                "tone": True,
                "bridge_dishes": True,
                "hashtags": True,
                "estimated_date": True,
                "market_lift": True,
            },
        ),
        "respectful_notes": Section("respectful_notes", "list", {"day": True, "label": True, "advice": True}),
        "berlin_events": Section("berlin_events", "list", {"event": True, "days": True}),
        "timing": Section(
            "timing",
            "object",
            {
                "posts": True,
                "weekday": {"platform": True, "weekday": True, "above_recent": True, "posts": True},
                "band": {"platform": True, "band": True, "above_recent": True, "posts": True},
            },
            note="two separate marginal tables (weekday, time band), facebook and instagram only",
        ),
    },
    "community_map": {
        "summary": Section("", "object", {"n_areas": True, "reach": {"label": True, "max_km": True, "factor": True}}),
        "areas": Section(
            "areas",
            "list",
            {
                "area_id": True,
                "name": True,
                "district": True,
                "score": True,
                "reach_band": True,
                "distance_km": True,
                "gap": True,
                "delivery_listings": True,
                "biryani_listings": True,
            },
            note="score and gap are area-level and derived from public statistics: choose areas, never people",
        ),
        "districts": Section(
            "districts",
            "list",
            {
                "district": True,
                "areas": True,
                "score": True,
                "biryani_listings": True,
                "gaps": True,
                "distance_km": True,
            },
        ),
    },
    "funnel": {
        "summary": Section(
            "",
            "object",
            {
                "period": True,
                "sessions": True,
                "unknown_share": True,
                "tracked_links": True,
                "totals": True,
                "advice": True,
            },
        ),
        "channels": Section(
            "channels",
            "list",
            {
                "channel": True,
                "visit": True,
                "menu": True,
                "cart": True,
                "checkout": True,
                "submitted": True,
                "ordered": True,
            },
        ),
        "posts": Section(
            "posts",
            "list",
            {
                "platform": True,
                "posted_at": True,
                "format": True,
                "caption": True,
                "sessions": True,
                "carts": True,
                "orders": True,
            },
        ),
        "friction": Section(
            "checkout_friction",
            "object",
            {"reached_checkout": True, "submitted": True, "stuck_sessions": True, "with": True},
        ),
    },
    "creator_matcher": {
        "summary": Section(
            "", "object", {"candidates": True, "eligible": True, "business_like": True, "by_size": True}
        ),
        "creators": Section("top", "list", _CREATOR, note="public accounts; followers unknown"),
        "specialists": Section(
            "south_asian_specialists", "list", _CREATOR, note="creators whose topics are South Asian food"
        ),
        "businesses": Section(
            "businesses", "list", {"handle": True, "url": True, "median_views": True, "posts_90d": True, "topics": True}
        ),
    },
    "weakness_miner": {
        "summary": Section(
            "", "object", {"period": True, "reviews": True, "listings": True, "clauses": True, "check": True}
        ),
        "overall": Section("overall", "list", {**_ASPECT, "praise": True, "our_angle": True}),
        "biryani_mentions": Section("biryani_mentions", "list", _ASPECT),
        "districts": Section(
            "districts",
            "list",
            {"district": True, "listings": True, "reviews": True, "top_complaints": _COMPLAINT},
        ),
        "competitors": Section(
            "competitors",
            "list",
            {
                "listing_id": True,
                "name": True,
                "district": True,
                "reviews": True,
                "rating": True,
                "biryani_items": True,
                "weak": _COMPLAINT,
                "strong": _COMPLAINT,
            },
        ),
    },
}


def project(value: Any, allow: Allow) -> Any:
    """Keep only allowlisted keys, recursively (lists are projected item by item)."""
    if isinstance(value, list):
        return [project(v, allow) for v in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, rule in allow.items():
        if key not in value:
            continue
        v = value[key]
        out[key] = v if rule is True else project(v, rule) if isinstance(rule, Mapping) else None
    return out


def suppress_small_counts(value: Any) -> Any:
    """Order counts 1-4 become "<5", anywhere in the value."""
    if isinstance(value, list):
        return [suppress_small_counts(v) for v in value]
    if isinstance(value, dict):
        return {
            k: "<5" if k in SMALL_COUNT_KEYS and isinstance(v, int) and 0 < v < 5 else suppress_small_counts(v)
            for k, v in value.items()
        }
    return value


def section_data(name: str, section: str, insights: Mapping[str, Any]) -> Any:
    """The exposed form of one section of an insights bundle: projected, then small counts suppressed."""
    spec = SECTIONS[name][section]
    raw = insights if spec.path == "" else insights.get(spec.path)
    if raw is None:
        return [] if spec.kind == "list" else {}
    data = project(raw, spec.allow)
    return suppress_small_counts(data) if name == "funnel" else data


def leaked_keys(value: Any) -> set[str]:
    """Restricted keys found anywhere in a value (a test helper and a last-line check)."""
    found: set[str] = set()
    if isinstance(value, list):
        for v in value:
            found |= leaked_keys(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in RESTRICTED:
                found.add(k)
            found |= leaked_keys(v)
    return found
