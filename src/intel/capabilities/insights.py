"""The five insights tools, read from intel-api's /v1/insights/{name} (active, approved versions only).

Restricted fields are removed at the source (intel.exposure in the route); `_section` checks again, so a mistake
in either place can't leak (architecture §2.7).
"""

from __future__ import annotations

import datetime as dt
import difflib
from typing import Any, Literal
from zoneinfo import ZoneInfo

from intel.capabilities.ports import IntelApi
from intel.capabilities.types import ResponseFormat, ToolProblem, prov
from intel.exposure import leaked_keys

BERLIN = ZoneInfo("Europe/Berlin")
QUOTE_CHARS = 140
TIMING_PLATFORMS = frozenset({"facebook", "instagram"})
METHOD_NOTE = (
    "score and gap are area-level, built from public statistics and delivery reach: use them to choose AREAS "
    "(delivery zones, area-targeted ads, flyers, pop-ups), never to target or exclude people."
)


def _section(api: IntelApi, name: str, section: str, **params: Any) -> dict[str, Any]:
    body = api.get(f"/v1/insights/{name}", {"section": section, **params})
    if not isinstance(body, dict):
        raise ToolProblem("UPSTREAM_UNAVAILABLE", f"unexpected answer for {name}.{section}")
    if leaked := leaked_keys(body):
        raise ToolProblem(
            "UPSTREAM_UNAVAILABLE", f"refused: {name}.{section} carried restricted fields {sorted(leaked)}"
        )
    return body


def _all_items(api: IntelApi, name: str, section: str, **params: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every item of a list section (paged by 200)."""
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        body = _section(api, name, section, limit=200, offset=offset, **params)
        page = body.get("items") or []
        items.extend(page)
        offset += len(page)
        if not page or offset >= int(body.get("total", 0)):
            return items, body


def _quote(text: Any) -> dict[str, str]:
    s = str(text)
    return {"text": s if len(s) <= QUOTE_CHARS else s[: QUOTE_CHARS - 1] + "…", "trust": "untrusted"}


def _head(body: dict[str, Any], name: str) -> dict[str, Any]:
    return prov(f"intel-api /v1/insights/{name}", body.get("version"))


# ---------- occasion_planner ----------


def upcoming_occasions(
    api: IntelApi,
    days: int = 14,
    platform: str | None = None,
    include_times: bool = True,
    today: dt.date | None = None,
) -> dict[str, Any]:
    if not 1 <= days <= 30:
        raise ToolProblem("INVALID_ARGUMENT", "days must be 1 to 30")
    start = today or dt.datetime.now(BERLIN).date()
    end = start + dt.timedelta(days=days)
    picks, body = _all_items(api, "occasion_planner", "picks")

    def overlaps(first: str, last: str) -> bool:
        return dt.date.fromisoformat(first) <= end and dt.date.fromisoformat(last) >= start

    def soon(p: dict[str, Any]) -> bool:
        posting = p.get("start_posting")
        return overlaps(p["first_day"], p["last_day"]) or (
            posting is not None and start <= dt.date.fromisoformat(posting) <= end
        )

    occasions = [
        {
            "id": p.get("public_id"),
            "label": p.get("label"),
            "names": p.get("names") or [],
            "greeting": {
                "text": p.get("greeting"),
                "translit": p.get("greeting_translit"),
                "needs_native_review": bool(p.get("greeting_needs_review")),
            },
            "first_day": p["first_day"],
            "last_day": p["last_day"],
            "start_posting": p.get("start_posting"),
            "food_role": p.get("food_role"),
            "tone": p.get("tone"),
            "sales_ok": p.get("tone") != "solemn",
            "bridge_dishes": (p.get("bridge_dishes") or [])[:3],
            "hashtags": (p.get("hashtags") or [])[:3],
            "estimated_date": bool(p.get("estimated_date")),
        }
        for p in picks
        if soon(p)
    ]
    notes, _ = _all_items(api, "occasion_planner", "respectful_notes")
    events, _ = _all_items(api, "occasion_planner", "berlin_events")
    out: dict[str, Any] = {
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "occasions": occasions,
        "respectful_notes": [n for n in notes if start <= dt.date.fromisoformat(n["day"]) <= end],
        "berlin_events": [
            {"event": e["event"], "days": [d for d in e.get("days", []) if start.isoformat() <= d <= end.isoformat()]}
            for e in events
            if any(start.isoformat() <= d <= end.isoformat() for d in e.get("days", []))
        ],
    }
    if include_times:
        if platform is not None and platform not in TIMING_PLATFORMS:
            out["timing"] = {"reason": "no_timing_for_platform", "best_weekdays": [], "best_bands": []}
        else:
            timing = _section(api, "occasion_planner", "timing").get("data") or {}

            def best(col: str) -> list[dict[str, Any]]:
                rows = [r for r in timing.get(col) or [] if platform is None or r["platform"] == platform]
                rows.sort(key=lambda r: -r["above_recent"])
                return [
                    {"platform": r["platform"], col: r[col], "above_recent": r["above_recent"], "posts": r["posts"]}
                    for r in rows[:3]
                ]

            out["timing"] = {
                "best_weekdays": best("weekday"),
                "best_bands": best("band"),
                "note": "two separate tables (weekday, Berlin time band); small samples, differences under ~15 "
                "points are noise",
            }
    return {**out, "prov": _head(body, "occasion_planner")}


# ---------- weakness_miner ----------


def competitor_complaints(
    api: IntelApi,
    district: str | None = None,
    competitor: str | None = None,
    aspect: str | None = None,
    limit: int = 10,
    response_format: ResponseFormat = "concise",
) -> dict[str, Any]:
    if not 1 <= limit <= 20:
        raise ToolProblem("INVALID_ARGUMENT", "limit must be 1 to 20")
    summary = _section(api, "weakness_miner", "summary")
    meta = summary.get("data") or {}
    overall, _ = _all_items(api, "weakness_miner", "overall")
    known = sorted(a["aspect"] for a in overall)
    if aspect is not None and aspect not in known:
        raise ToolProblem("INVALID_ARGUMENT", f"unknown aspect {aspect!r}", f"one of: {', '.join(known)}")
    n_examples = 2 if response_format == "concise" else 4
    aspects = [
        {
            "aspect": a["aspect"],
            "label": a["label"],
            "mentions": a["mentions"],
            "negative_rate": a["negative_rate"],
            "examples": [_quote(x) for x in (a.get("complaints") or [])[:n_examples]],
            "our_angle": a.get("our_angle"),
            "angle_status": "suggestion, verify",
        }
        for a in sorted(overall, key=lambda a: -a["negative"])
        if aspect is None or a["aspect"] == aspect
    ]
    out: dict[str, Any] = {
        "period": meta.get("period"),
        "reviews_analysed": meta.get("reviews"),
        "quality": {
            k: (meta.get("check") or {}).get(k) for k in ("aspect_precision", "aspect_recall", "polarity_accuracy")
        },
        "aspects": aspects[:limit],
    }
    if district is not None or competitor is not None:
        params = {"district": district} if district else {}
        comps, _ = _all_items(api, "weakness_miner", "competitors", **params)
        if district is not None and not comps:
            dists, _ = _all_items(api, "weakness_miner", "districts")
            names = sorted(d["district"] for d in dists)
            raise ToolProblem(
                "INVALID_ARGUMENT", f"no competitors in district {district!r}", f"one of: {', '.join(names)}"
            )
        if competitor is not None:
            by_name = {str(c.get("name")): c for c in comps}
            match = difflib.get_close_matches(competitor, list(by_name), n=3, cutoff=0.5)
            if not match:
                raise ToolProblem("INVALID_ARGUMENT", f"no competitor like {competitor!r}", "check the spelling")
            comps = [by_name[m] for m in match]
        out["competitors"] = [
            {
                "name": c.get("name"),
                "district": c.get("district"),
                "reviews": c.get("reviews"),
                "rating": c.get("rating"),
                "weak": [w["label"] for w in (c.get("weak") or [])[:3]],
                "strong": [s["label"] for s in (c.get("strong") or [])[:3]],
            }
            for c in sorted(comps, key=lambda c: -int(c.get("reviews") or 0))[:limit]
        ]
    out["next"] = "for cited quotes use search_reviews(aspect=…) (available from P4.2)"
    return {**out, "prov": _head(summary, "weakness_miner")}


# ---------- community_map ----------


def community_areas(
    api: IntelApi,
    district: str | None = None,
    top_n: int = 10,
    sort: Literal["score", "distance"] = "score",
    gaps_only: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    if not 1 <= top_n <= 20:
        raise ToolProblem("INVALID_ARGUMENT", "top_n must be 1 to 20")
    params = {"district": district} if district else {}
    areas, body = _all_items(api, "community_map", "areas", **params)
    if district is not None and not areas:
        dists, _ = _all_items(api, "community_map", "districts")
        names = sorted(d["district"] for d in dists)
        raise ToolProblem("INVALID_ARGUMENT", f"unknown district {district!r}", f"one of: {', '.join(names)}")
    if gaps_only:
        areas = [a for a in areas if a.get("gap")]
    if sort == "score":
        areas.sort(key=lambda a: -(a.get("score") or 0))
    else:
        areas.sort(key=lambda a: a.get("distance_km") or 1e9)
    page = areas[offset : offset + top_n]
    out: dict[str, Any] = {
        "areas": [{k: a.get(k) for k in a if k != "area_id"} for a in page],
        "total": len(areas),
        "next_offset": offset + top_n if offset + top_n < len(areas) else None,
        "method_note": METHOD_NOTE,
    }
    if district is None:
        dists, _ = _all_items(api, "community_map", "districts")
        out["districts"] = sorted(dists, key=lambda d: -(d.get("score") or 0))
    return {**out, "prov": _head(body, "community_map")}


# ---------- funnel ----------


def funnel_status(
    api: IntelApi, section: Literal["overview", "channels", "posts", "friction"] = "overview"
) -> dict[str, Any]:
    if section == "overview":
        body = _section(api, "funnel", "summary")
        data = body.get("data") or {}
        out: dict[str, Any] = {
            k: data.get(k) for k in ("period", "sessions", "unknown_share", "tracked_links", "totals")
        }
        out["advice"] = (data.get("advice") or [])[:3]
    elif section == "channels":
        items, body = _all_items(api, "funnel", "channels")
        out = {"top_channels": sorted(items, key=lambda c: -int(c.get("visit") or 0))[:5]}
    elif section == "posts":
        items, body = _all_items(api, "funnel", "posts")
        out = {
            "top_posts": [
                {
                    **{k: p.get(k) for k in p if k != "caption"},
                    "caption_excerpt": _quote(p.get("caption") or "")["text"],
                }
                for p in sorted(items, key=lambda p: -int(p.get("sessions") or 0))[:10]
            ],
            "note": "visits within 48 h after a post; without tracked links this is timing only",
        }
    else:
        body = _section(api, "funnel", "friction")
        out = {"friction": body.get("data") or {}}
    out["small_counts"] = 'order counts from 1 to 4 are shown as "<5"'
    return {**out, "prov": _head(body, "funnel")}


# ---------- creator_matcher ----------


def find_creators(
    api: IntelApi,
    size: str | None = None,
    topic: str | None = None,
    cuisine_specialists_only: bool = False,
    include_examples: bool = False,
    limit: int = 8,
) -> dict[str, Any]:
    if not 1 <= limit <= 15:
        raise ToolProblem("INVALID_ARGUMENT", "limit must be 1 to 15")
    summary = _section(api, "creator_matcher", "summary")
    section = "specialists" if cuisine_specialists_only else "creators"
    creators, _ = _all_items(api, "creator_matcher", section)
    if size is not None:
        creators = [c for c in creators if size.casefold() in str(c.get("size", "")).casefold()]
    if topic is not None:
        creators = [c for c in creators if any(topic.casefold() in str(t).casefold() for t in c.get("topics") or [])]
    out = []
    for c in creators[:limit]:
        row = {k: c.get(k) for k in c if k != "examples"}
        row["topics"] = (c.get("topics") or [])[:3]
        if include_examples and c.get("examples"):
            ex = c["examples"][0]
            row["example"] = {
                "url": ex.get("url"),
                "caption": _quote(ex.get("caption") or ""),
                "views": ex.get("views"),
            }
        out.append(row)
    meta = summary.get("data") or {}
    return {
        "eligible": meta.get("eligible"),
        "creators": out,
        "note": "public accounts; follower counts unknown. Watch their videos before contacting anyone; paid or "
        "gifted posts must be labelled 'Werbung'.",
        "prov": _head(summary, "creator_matcher"),
    }
