"""Composition root: settings -> adapters -> what the servers call. The only place that wires I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intel.adapters.intel_api import HttpIntelApi, ScoringLimits
from intel.adapters.pack import PackBrandSource
from intel.capabilities.evidence import EvidenceIndex
from intel.capabilities.ports import BrandSource, IntelApi
from intel.capabilities.types import ToolProblem
from intel.mcp_server.kit.audit import AuditLog
from intel.settings import McpSettings


class _NoIntelApi:
    """INTEL_API_URL isn't set: the model tools say so; the brand tools keep working."""

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        raise ToolProblem("UPSTREAM_UNAVAILABLE", "INTEL_API_URL is not set on this machine", "set it in .env")

    def score_batch(self, drafts: list[dict[str, Any]], principal: str) -> dict[str, Any]:
        raise ToolProblem("UPSTREAM_UNAVAILABLE", "INTEL_API_URL is not set on this machine", "set it in .env")


def model_kinds() -> dict[str, str]:
    """name -> scorer | insights, from our own plug-ins (intel-api's /ready only lists name -> version)."""
    from intel.plugins import discover

    return {name: str(p.kind) for p in discover() for name in p.models}


@dataclass
class Deps:
    api: IntelApi
    brand: BrandSource
    audit: AuditLog
    principal: str
    disabled_tools: frozenset[str] = frozenset()
    kinds: dict[str, str] = field(default_factory=dict)
    evidence: EvidenceIndex | None = None
    min_evidence: float | None = None  # reranker score below which search says "not found" (calibrated, ADR 0027)
    min_support: float | None = None  # reranker score a claim needs from its cited source to count as supported

    @classmethod
    def from_settings(cls, settings: McpSettings, principal: str) -> Deps:
        api: IntelApi = (
            HttpIntelApi(settings.intel_api_url, ScoringLimits(settings.limits_dir))
            if settings.intel_api_url
            else _NoIntelApi()
        )
        try:
            kinds = model_kinds()
        except Exception:  # noqa: BLE001 - status still works with kind "unknown"
            kinds = {}
        return cls(
            api=api,
            brand=PackBrandSource(settings.pack_dir),
            audit=AuditLog(settings.audit_dir),
            principal=principal,
            disabled_tools=settings.disabled_tools,
            kinds=kinds,
            evidence=_evidence(settings),
            min_evidence=settings.intel_rag_min_evidence,
            min_support=settings.intel_rag_min_support,
        )


def _evidence(settings: McpSettings) -> EvidenceIndex | None:
    if not settings.intel_rag_dsn:
        return None
    from intel.adapters.pg_index import PgEvidenceIndex
    from intel.rag.embed import load
    from intel.rag.rerank import load_reranker

    embedder_key, reranker_key = settings.intel_rag_embedder, settings.intel_rag_reranker
    return PgEvidenceIndex(
        settings.intel_rag_dsn,
        (lambda: load(embedder_key)) if embedder_key else None,
        (lambda: load_reranker(reranker_key)) if reranker_key else None,
    )
