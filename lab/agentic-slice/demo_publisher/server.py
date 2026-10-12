"""demo_publisher: a toy MCP server standing in for "another product" (lab only).

It shows how any product joins the agent system: a small FastMCP server with typed tools and "Use when" descriptions.
Two tools: one that reads (the posting calendar) and one that writes (a *proposal*, appended to a local file). It
never touches a real system: the "write" lands in ./proposals.json next to this folder, for you to look at.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

HERE = Path(__file__).parent
CALENDAR = HERE / "calendar.json"
PROPOSALS = HERE.parent / "proposals.json"

mcp = FastMCP("demo_publisher", instructions="A toy posting calendar. Read slots, then propose posts for them.")


@mcp.tool(annotations={"readOnlyHint": True})
def list_slots(platform: Literal["instagram", "facebook"]) -> dict[str, Any]:
    """Use when: you need the open posting slots for a platform this week (day, time, format)."""
    slots = json.loads(CALENDAR.read_text())["slots"]
    return {"platform": platform, "slots": [s for s in slots if s["platform"] == platform]}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
def propose_post(
    slot_id: Annotated[str, Field(pattern=r"^s\d+$")],
    caption: Annotated[str, Field(min_length=10, max_length=2200)],
    verdict: Literal["keep"],
    sources: Annotated[list[str], Field(max_length=10)] = [],  # noqa: B006 - pydantic copies defaults
    proposed_by: str = "agent",
) -> dict[str, Any]:
    """Use when: a caption passed evaluate_drafts with verdict keep and should be proposed for a slot. It only records
    a proposal for the owner to approve; it never posts anything. Only verdict 'keep' is accepted."""
    slots = {s["id"] for s in json.loads(CALENDAR.read_text())["slots"]}
    if slot_id not in slots:
        raise ValueError(f"unknown slot {slot_id}")
    data = json.loads(PROPOSALS.read_text()) if PROPOSALS.exists() else []
    item = {
        "id": uuid.uuid4().hex[:8],
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "slot_id": slot_id,
        "caption": caption,
        "sources": sources,
        "proposed_by": proposed_by,
        "status": "waiting_for_owner",
    }
    PROPOSALS.write_text(json.dumps([*data, item], indent=2, ensure_ascii=False))
    return {"proposal_id": item["id"], "status": item["status"]}


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
