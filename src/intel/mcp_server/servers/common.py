"""What every server's tools share: annotations, the problem-to-ToolError edge, the Draft list type."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from intel.capabilities.types import Draft, ToolProblem

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, destructive_hint=False, open_world_hint=False)

Drafts = Annotated[list[Draft], Field(min_length=1, max_length=50, description="the drafts to judge")]


def labels(sensitivity: str, trust: str, server: str) -> dict[str, Any]:
    """`_meta` on every tool: data class, trust of its text, owning server, contract version (architecture §2.1)."""
    return {"dk/sensitivity": sensitivity, "dk/trust": trust, "dk/server": server, "dk/contract": "1.0"}


def run(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Expected failures become `[CODE] message. Try: …` for the client; anything else stays masked."""
    try:
        return fn()
    except ToolProblem as e:
        raise ToolError(str(e)) from e
