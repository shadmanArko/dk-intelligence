"""Ports: what the capabilities need from the outside world. Adapters implement them; tests use fakes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class IntelApi(Protocol):
    """intel-api, read-only plus the non-logging batch scorer. Implementations refuse any route not allowlisted."""

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...

    def score_batch(self, drafts: list[dict[str, Any]], principal: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BrandPack:
    """The brand part of the active knowledge pack, already parsed and checksum-verified."""

    pack_id: str
    playbook: dict[str, Any]
    content_rules: dict[str, Any]
    facts: dict[str, Any]
    documents: dict[str, str] = field(default_factory=dict)  # file name -> text (brand book, voice and rules)
    sources: list[dict[str, str]] = field(default_factory=list)  # [{file, sha256}]


class BrandSource(Protocol):
    def load(self) -> BrandPack:
        """The active brand pack; raises ToolProblem(BRAND_UNAVAILABLE) when missing or tampered with."""
        ...
