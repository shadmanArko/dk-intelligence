"""dk_intel: one endpoint that mounts the servers, applies one profile and the shared kit (ADR 0025, §3.4).

Servers are mounted without a namespace (clients already prefix tool names), so names must be unique across
servers: FastMCP silently keeps one of two duplicates (P4.0), hence `check_unique_names`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig

from intel.capabilities.types import ToolProblem
from intel.mcp_server.deps import Deps
from intel.mcp_server.kit.middleware import (
    AuditMiddleware,
    BudgetMiddleware,
    ContextMiddleware,
    CopyGuard,
    NormaliseArgs,
    ProfileGate,
    ResultCache,
    SensitivityCeiling,
    token_principal,
)
from intel.mcp_server.servers.brand import build_brand_server
from intel.mcp_server.servers.evidence import build_evidence_server
from intel.mcp_server.servers.models import build_models_server
from intel.mcp_server.servers.performance import build_performance_server
from intel.mcp_server.servers.studio import build_studio_server

HERE = Path(__file__).parent
SERVERS: dict[str, Callable[[Deps], FastMCP]] = {
    "models": build_models_server,
    "brand": build_brand_server,
    "evidence": build_evidence_server,
    "studio": build_studio_server,
    "performance": build_performance_server,
}
TOOLS_API = "1.0"


@cache
def profiles() -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = yaml.safe_load((HERE / "profiles.yaml").read_text())
    return data


@cache
def budgets() -> dict[str, int]:
    data: dict[str, int] = yaml.safe_load((HERE / "budgets.yaml").read_text())
    return data


def instructions() -> str:
    return (HERE / "instructions.md").read_text().strip()


async def tool_names(server: FastMCP) -> list[str]:
    return [t.name for t in await server.list_tools()]


async def check_unique_names(servers: list[FastMCP]) -> None:
    seen: dict[str, str] = {}
    for s in servers:
        for name in await tool_names(s):
            if name in seen:
                raise RuntimeError(f"tool {name!r} is defined by both {seen[name]} and {s.name}")
            seen[name] = str(s.name)


def build_gateway(profile: str, deps: Deps, only: str | None = None, auth: AuthProvider | None = None) -> FastMCP:
    """The gateway for one profile; `only` runs a single server alone (same kit). `auth` (HTTP only) is the
    profile's token verifier: without a valid token for this profile nothing reaches the gateway."""
    if profile not in profiles():
        raise ValueError(f"unknown profile {profile!r}; one of: {', '.join(profiles())}")
    spec = profiles()[profile]
    chosen = {only: SERVERS[only]} if only else SERVERS
    from intel.mcp_server.kit.log_redact import install

    install()
    gw = FastMCP("dk_intel", instructions=instructions(), version=TOOLS_API, mask_error_details=True, auth=auth)
    for build in chosen.values():
        gw.mount(build(deps))
    forced = spec.get("forced") or {}
    if forced:
        gw.add_transform(
            ToolTransform(
                {
                    tool: ToolTransformConfig(
                        arguments={arg: ArgTransformConfig(hide=True, default=value) for arg, value in args.items()}
                    )
                    for tool, args in forced.items()
                }
            )
        )
    resources = frozenset(spec.get("resources") or [])
    prompts = spec.get("prompts") or []
    # Errors: expected failures are ToolErrors (`[CODE] message. Try: …`, returned as tool results the model can
    # read); anything else is masked by mask_error_details. FastMCP's ErrorHandlingMiddleware is not used: it turns
    # ToolErrors into opaque protocol errors.
    gw.add_middleware(ContextMiddleware())
    gw.add_middleware(AuditMiddleware(deps.audit, deps.principal, profile))
    gw.add_middleware(
        ProfileGate(
            tools=frozenset(spec.get("tools") or []),
            resources=resources,
            prompts=frozenset(["*"] if "*" in prompts else prompts),
            disabled=deps.disabled_tools,
        )
    )
    gw.add_middleware(SensitivityCeiling())
    gw.add_middleware(NormaliseArgs())
    # per principal (the token's subject + jti on HTTP, the process on stdio): the P4.6 load test found the default
    # global bucket let 10 agents' handshakes starve each other
    gw.add_middleware(
        RateLimitingMiddleware(
            max_requests_per_second=10.0, burst_capacity=20, get_client_id=lambda _: token_principal() or "stdio"
        )
    )
    gw.add_middleware(ResultCache(lambda: _pack_id(deps)))
    gw.add_middleware(CopyGuard(lambda: _blocked_words(deps), deps.audit))
    gw.add_middleware(BudgetMiddleware(budgets(), deps.audit))
    gw.add_middleware(ResponseLimitingMiddleware(max_size=32_768))  # the last backstop (~11k tokens)
    return gw


def _pack_id(deps: Deps) -> str | None:
    try:
        return deps.brand.load().pack_id
    except ToolProblem:
        return None


def _blocked_words(deps: Deps) -> list[str] | None:
    try:
        return [str(w) for w in deps.brand.load().content_rules.get("blocked_words") or []]
    except ToolProblem:
        return None


def describe(gw: FastMCP) -> dict[str, Any]:
    """Name, version and instructions as clients see them (for the CLI and tests)."""
    return {"name": gw.name, "version": TOOLS_API, "instructions": gw.instructions}
