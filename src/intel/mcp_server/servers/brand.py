"""dk_brand: brand rules, confirmed facts and deterministic caption checks, from the knowledge pack (§2.5)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from intel.capabilities import brand, checks
from intel.capabilities.types import Draft, Platform, ToolProblem
from intel.mcp_server.deps import Deps
from intel.mcp_server.servers.common import READ_ONLY, labels, run

S0 = labels("S0", "trusted", "dk_brand")
S1 = labels("S1", "trusted", "dk_brand")
FactGroup = Literal["menu", "ordering", "fulfilment", "claims_status"]


def build_brand_server(deps: Deps) -> FastMCP:
    mcp = FastMCP("dk_brand", mask_error_details=True)

    @mcp.tool(annotations=READ_ONLY, meta=S1, timeout=5.0)
    def check_drafts(
        drafts: Annotated[list[Draft], Field(min_length=1, max_length=20)],
    ) -> dict[str, Any]:
        """Use when: before showing or scoring any caption: checks blocked words, Kacchi and Berlin, length and
        first line, hashtags, emojis, German first, 'Werbung' on paid posts, prices against facts, halal, health,
        allergen, superlative and 'only in Berlin' claims, and sales wording on solemn days. Not for: engagement
        (use score_drafts)."""
        return run(lambda: checks.check_drafts(deps.brand, drafts))

    @mcp.tool(annotations=READ_ONLY, meta=S1, timeout=5.0)
    def brand_rules(
        platform: Platform | None = None,
        format: str | None = None,
        section: Literal["voice", "platform", "legal", "all"] = "all",
    ) -> dict[str, Any]:
        """Use when: writing for Dhaka Kacchi: voice, words to always use, what we never say, languages, and for a
        platform its cadence, posting windows, formats, caption limits and audio rules. Not for: prices or hours
        (use get_facts) or checking a draft (use check_drafts)."""
        return run(lambda: brand.brand_rules(deps.brand, platform, format, section))

    @mcp.tool(annotations=READ_ONLY, meta=S0, timeout=5.0)
    def get_facts(keys: list[FactGroup] | None = None) -> dict[str, Any]:
        """Use when: you need a price, order deadline, delivery fee or area, pickup place or what we may claim.
        Every fact has a status: only 'confirmed' may be stated; 'unknown' means don't state it. Never answer
        allergen questions: use the allergen_reply."""
        return run(lambda: brand.get_facts(deps.brand, list(keys) if keys else None))

    def _doc(name: str) -> str:
        try:
            return deps.brand.load().documents[name]
        except ToolProblem as e:
            return str(e)

    @mcp.resource("dk://brand/book", mime_type="text/markdown", meta=S0)
    def brand_book() -> str:
        """The brand book (public-safe)."""
        return _doc("brand-book.md")

    @mcp.resource("dk://brand/voice", mime_type="text/markdown", meta=S1)
    def brand_voice() -> str:
        """Voice, audiences and rules (internal)."""
        return _doc("voice-and-rules.md")

    @mcp.resource("dk://brand/facts", mime_type="text/yaml", meta=S0)
    def brand_facts() -> str:
        """facts.yaml as written, including its [CONFIRM] notes."""
        return _doc("facts.yaml")

    return mcp
