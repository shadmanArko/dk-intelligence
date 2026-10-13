"""dk_performance: what our own posts did, per platform (S4). Read-only, trusted (our own numbers)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from intel.capabilities import performance
from intel.capabilities.types import ToolProblem
from intel.mcp_server.deps import Deps
from intel.mcp_server.servers.common import READ_ONLY, labels, run

S1 = labels("S1", "trusted", "dk_performance")
PerfPlatform = Literal["instagram", "facebook", "threads", "youtube", "tiktok"]
Feature = Literal["format", "weekday", "window", "caption_length", "first_line", "hashtags", "emojis", "asks_question",
                  "calls_to_action", "german_first_line", "bilingual", "mentions_price"]  # fmt: skip


def build_performance_server(deps: Deps) -> FastMCP:
    mcp = FastMCP("dk_performance", mask_error_details=True)

    def source() -> performance.PerformanceSource:
        if deps.performance is None:
            raise ToolProblem("UPSTREAM_UNAVAILABLE", "no performance data on this machine",
                              "run `uv run intel performance build` on the Mac")  # fmt: skip
        return deps.performance

    @mcp.tool(annotations=READ_ONLY, meta=S1, timeout=10.0)
    def platform_playbook(platform: PerfPlatform) -> dict[str, Any]:
        """Use when: planning or writing for one platform and you want to know what our own past posts there support:
        what works, what hurts (each with its numbers, interval and post count), what a brand rule decides, and when
        there is not enough data to say anything. Reach and engagement, never orders. Not for: checking a draft (use
        evaluate_drafts) or competitor or market trends (use trend_signals)."""
        return run(lambda: performance.platform_playbook(source(), platform))

    @mcp.tool(annotations=READ_ONLY, meta=S1, timeout=10.0)
    def performance_by_feature(
        platform: PerfPlatform, feature: Annotated[Feature, Field(description="the post feature to break down")]
    ) -> dict[str, Any]:
        """Use when: you want every value of one feature on one platform, e.g. format or caption length: the median
        lift of a usual post, its interval, how many posts, and whether the difference from the other posts is clear.
        Not for: the short summary (use platform_playbook)."""
        return run(lambda: performance.performance_by_feature(source(), platform, feature))

    @mcp.tool(annotations=READ_ONLY, meta=S1, timeout=10.0)
    def post_performance(
        post_key: Annotated[str, Field(max_length=80, description="e.g. 2026w42-tue13-i1")],
    ) -> dict[str, Any]:
        """Use when: you want to know how one of our studio's published posts did, per platform, with what we knew about
        its idea (pillar, angle, hook). Posts are judged after 7 days. Not for: posts made by hand (they are in the
        playbook's numbers, not listed by key)."""
        return run(lambda: performance.post_performance(source(), post_key))

    return mcp
