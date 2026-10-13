"""dk_performance: what our own posts actually did (S4). Read-only views of the playbook and our agent posts.

The numbers come from `uv run intel performance build` (growth-normalised lift against the previous 20 posts, bootstrap
intervals). Nothing here is a model or a score: a tool says what was measured, how many posts it rests on, and when
there is not enough to say anything.
"""

from __future__ import annotations

from typing import Any, Protocol

from intel.capabilities.types import ToolProblem, prov

LINES = 8


class PerformanceSource(Protocol):
    def playbook(self) -> dict[str, Any] | None: ...

    def agent_posts(self) -> list[dict[str, Any]]: ...


def _playbook(src: PerformanceSource) -> dict[str, Any]:
    pb = src.playbook()
    if pb is None:
        raise ToolProblem("UPSTREAM_UNAVAILABLE", "no performance playbook has been built yet",
                          "run `uv run intel performance build` on the Mac")  # fmt: skip
    return pb


def platform_playbook(src: PerformanceSource, platform: str) -> dict[str, Any]:
    pb = _playbook(src)
    entry = (pb.get("platforms") or {}).get(platform)
    if entry is None:
        raise ToolProblem("INVALID_ARGUMENT", f"no playbook for {platform!r}", ", ".join(pb.get("platforms", {})))
    out: dict[str, Any] = {"platform": platform, "posts": entry["posts"], "matured": entry["matured"],
                           "enough": entry["enough"], "method": pb["method"], "caveats": pb["caveats"]}  # fmt: skip
    if not entry["enough"]:
        out["status"] = entry.get("note", "not enough data")
    else:
        lines = entry["lines"]
        out.update(status=f"{entry['tests']} comparisons; about {entry['expected_false_alarms']} may be chance",
                   baseline=entry["baseline"], works=lines["works"][:LINES], hurts=lines["hurts"][:LINES],
                   for_the_owner=lines["info"][:LINES], no_usable_data_for=entry.get("no_outcome", []))  # fmt: skip
    return {**out, "prov": prov("performance playbook", pb["pull_id"], pb["built_at"])}


def performance_by_feature(src: PerformanceSource, platform: str, feature: str) -> dict[str, Any]:
    pb = _playbook(src)
    entry = (pb.get("platforms") or {}).get(platform)
    if entry is None or not entry.get("enough"):
        return {"platform": platform, "feature": feature, "rows": [],
                "status": (entry or {}).get("note", f"no playbook for {platform!r}"),
                "prov": prov("performance playbook", pb["pull_id"], pb["built_at"])}  # fmt: skip
    rows = [r for r in entry.get("table", []) if r["feature"] == feature]
    if not rows:
        known = sorted({r["feature"] for r in entry.get("table", [])})
        raise ToolProblem("INVALID_ARGUMENT", f"no feature {feature!r}", ", ".join(known))
    return {"platform": platform, "feature": feature, "rows": rows,
            "reading": "median_lift 1.0 = a usual post; diff and its interval compare the value with the other posts; "
                       "verdict works/hurts only when the whole interval is on one side of zero",
            "prov": prov("performance playbook", pb["pull_id"], pb["built_at"])}  # fmt: skip


def post_performance(src: PerformanceSource, post_key: str) -> dict[str, Any]:
    rows = [r for r in src.agent_posts() if r.get("post_key") == post_key]
    if not rows:
        raise ToolProblem("INVALID_ARGUMENT", f"no published post {post_key!r} in the joined results",
                          "run `uv run intel performance join` after the post is published")  # fmt: skip
    keep = ("platform", "live_url", "collected", "matured", "age_days", "engagement", "audience", "lift_engagement",
            "lift_audience", "pillar", "angle", "idea_format", "hook_length", "shots")  # fmt: skip
    out = [{k: r.get(k) for k in keep if r.get(k) is not None} for r in rows]
    note = "no result yet: the warehouse has not collected this post" if not any(r["collected"] for r in out) else (
        "a post is judged after 7 days; younger numbers are still growing")  # fmt: skip
    return {"post_key": post_key, "variants": out, "note": note, "prov": prov("performance join")}
