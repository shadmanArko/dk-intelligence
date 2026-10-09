"""Shared types of the capability core: drafts, problems, provenance."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Platform = Literal["facebook", "instagram", "threads", "youtube", "tiktok", "linkedin", "x", "reddit"]
ResponseFormat = Literal["concise", "detailed"]
ProblemCode = Literal[
    "INVALID_ARGUMENT",
    "UPSTREAM_UNAVAILABLE",
    "MODEL_NOT_ACTIVE",
    "BRAND_UNAVAILABLE",
    "NOT_ALLOWED_FOR_PROFILE",
    "RATE_LIMITED",
    "FEATURE_OFF",
]
SCORABLE: frozenset[str] = frozenset({"facebook", "instagram"})  # the production scorer's platforms


class ToolProblem(Exception):
    """An expected failure the client can act on. The MCP edge turns it into `[CODE] message. Try: hint`."""

    def __init__(self, code: ProblemCode, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (f". Try: {self.hint}" if self.hint else "")


class Draft(BaseModel):
    """A social post draft, as every scoring and checking tool takes it."""

    model_config = ConfigDict(extra="forbid")

    ref: str | None = Field(default=None, max_length=24, description="your own handle for this draft, echoed back")
    platform: Platform
    format: str | None = Field(default=None, description="playbook format, e.g. reel, carousel, short_video")
    content_type: Literal["image", "video", "reel", "carousel", "short"] | None = None
    caption: str = Field(min_length=1, max_length=5000)
    planned_posted_at: dt.datetime | None = Field(
        default=None, description="with a timezone, e.g. 2026-10-14T16:30:00+02:00; needed for scoring"
    )
    paid: bool = Field(default=False, description="a paid or gifted partnership post")
    occasion_tone: Literal["festive", "solemn", "none"] = "none"

    @field_validator("planned_posted_at")
    @classmethod
    def _needs_timezone(cls, v: dt.datetime | None) -> dt.datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("planned_posted_at needs a timezone, e.g. 2026-10-14T16:30:00+02:00")
        return v


def refs(drafts: list[Draft]) -> list[str]:
    """Each draft's ref, or d1, d2, ... when the client gave none."""
    return [d.ref or f"d{i + 1}" for i, d in enumerate(drafts)]


def prov(source: str, version: str | None = None, as_of: str | None = None) -> dict[str, Any]:
    """Where an answer came from (every tool result carries it)."""
    return {"source": source, "version": version, "as_of": as_of}
