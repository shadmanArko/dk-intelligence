"""dk_studio: one-call grounding, check-and-score, and the workflow and role prompts (architecture §2.6).

Prompts are versioned text in `mcp_server/prompts/*.md` (front matter: name, version, args, tools_used,
max_tool_calls), reviewed like code and linted by tests. Generation happens in the client (ADR 0028): the server
never calls a language model.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import yaml
from fastmcp import FastMCP
from pydantic import Field

from intel.capabilities import brand, studio
from intel.capabilities.types import Draft, ToolProblem
from intel.mcp_server.deps import Deps
from intel.mcp_server.servers.common import READ_ONLY, labels, run

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
S1 = labels("S1", "trusted", "dk_studio")
Days = Annotated[int, Field(ge=1, le=30, description="how many days ahead, 1 to 30")]
StudioPlatform = Literal["facebook", "instagram", "threads", "youtube", "tiktok", "linkedin"]


@cache
def prompt_specs() -> dict[str, tuple[dict[str, Any], str]]:
    """name -> (front matter, body) for every prompt file."""
    out = {}
    for f in sorted(PROMPTS.glob("[!_]*.md")):
        _, front, body = f.read_text().split("---\n", 2)
        meta: dict[str, Any] = yaml.safe_load(front)
        out[str(meta["name"])] = (meta, body.strip())
    return out


def render(name: str, deps: Deps | None = None, **args: Any) -> str:
    meta, body = prompt_specs()[name]
    values = {"rules": (PROMPTS / "_rules.md").read_text().strip(), **args}
    if "{brand_card}" in body:
        values["brand_card"] = brand_card(deps)
    return body.format(**values) + f"\n\n(prompt {meta['name']} v{meta['version']})"


def brand_card(deps: Deps | None) -> str:
    """The voice part of the brand rules, rendered into role prompts (stable text, so clients can cache it)."""
    if deps is None:
        return "(brand card unavailable: call brand_rules)"
    try:
        rules = brand.brand_rules(deps.brand, section="voice")
    except ToolProblem:
        return "(brand card unavailable: call brand_rules)"
    lines = [f"- voice: {rules.get('voice')}", f"- always say: {', '.join(rules.get('always_say') or [])}"]
    lines.append(f"- signature: {rules.get('signature')}")
    lines.append(f"- languages: {rules['languages']['rules']}")
    return "\n".join(lines)


def build_studio_server(deps: Deps) -> FastMCP:
    mcp = FastMCP("dk_studio", mask_error_details=True)

    @mcp.tool(annotations=READ_ONLY, meta=S1, timeout=30.0)
    def grounding_pack(
        platform: StudioPlatform,
        days: Days = 14,
        topic: Annotated[str | None, Field(max_length=100, description="optional focus, e.g. Borhani")] = None,
        size: Literal["small", "medium"] = "small",
    ) -> dict[str, Any]:
        """Use when: planning posts, a content calendar, captions or ideas for one platform: START HERE. One call
        replaces upcoming_occasions + competitor_complaints + search_posts + trend_signals + brand_rules: the next
        days' occasions (with tone), posting times, competitor gaps with a cited quote, our best posts, what lifts
        engagement, the hidden patterns (opportunities) and brand reminders. Parts whose source is down are listed
        in unavailable. Not for: checking drafts (use evaluate_drafts)."""
        return run(
            lambda: studio.grounding_pack(
                deps.api, deps.brand, deps.evidence, deps.trends, platform, days, topic, size, deps.min_evidence
            )
        )

    @mcp.tool(annotations=READ_ONLY, meta=S1, timeout=30.0)
    def evaluate_drafts(
        drafts: Annotated[list[Draft], Field(min_length=1, max_length=20, description="the drafts to judge")],
    ) -> dict[str, Any]:
        """Use when: judging, checking or scoring drafts or captions: one call instead of check_drafts + score_drafts.
        Runs the brand checks and, for Facebook and Instagram drafts with planned_posted_at, the content scorer, and
        gives one verdict per draft: keep, rewrite (with fixes) or blocked. Rewrite at most twice."""
        return run(lambda: studio.evaluate_drafts(deps.api, deps.brand, drafts, deps.principal))

    @mcp.prompt(name="content_plan", description=prompt_specs()["content_plan"][0]["description"])
    def content_plan(platform: str = "instagram", days: str = "14") -> str:
        return render("content_plan", platform=_platform(platform), days=_days(days, 10))

    @mcp.prompt(name="caption_ideas", description=prompt_specs()["caption_ideas"][0]["description"])
    def caption_ideas(platform: str = "instagram", count: str = "5") -> str:
        return render("caption_ideas", platform=_platform(platform), count=_count(count))

    @mcp.prompt(name="image_ideas", description=prompt_specs()["image_ideas"][0]["description"])
    def image_ideas(platform: str = "instagram") -> str:
        return render("image_ideas", platform=_platform(platform))

    @mcp.prompt(name="video_ideas", description=prompt_specs()["video_ideas"][0]["description"])
    def video_ideas(platform: str = "tiktok") -> str:
        return render("video_ideas", platform=_platform(platform))

    for role in ("role_planner", "role_writer", "role_producer", "role_checker"):
        mcp.prompt(name=role, description=prompt_specs()[role][0]["description"])(_role(role, deps))

    return mcp


def _role(name: str, deps: Deps) -> Any:
    def role_prompt() -> str:
        return render(name, deps)

    role_prompt.__name__ = name
    return role_prompt


def _platform(value: str) -> str:
    allowed = get_args(StudioPlatform)
    if value not in allowed:
        raise ValueError(f"platform must be one of: {', '.join(allowed)}")
    return value


def _days(value: str, low: int) -> int:
    """Prompt arguments arrive as strings (Claude Code splits them on spaces)."""
    try:
        days = int(value)
    except ValueError as e:
        raise ValueError(f"days must be a number from {low} to 30") from e
    if not low <= days <= 30:
        raise ValueError(f"days must be {low} to 30")
    return days


def _count(value: str) -> int:
    try:
        n = int(value)
    except ValueError as e:
        raise ValueError("count must be a number from 1 to 10") from e
    if not 1 <= n <= 10:
        raise ValueError("count must be 1 to 10")
    return n
