"""The FastMCP behaviours the Phase 4 design relies on (ADR 0025, P4.0 spikes).

Each test pins one assumption from docs/PHASE4_MCP_ARCHITECTURE.md §9.4. If a FastMCP upgrade changes one of them,
this file fails before any server code silently changes meaning. Skipped when the `mcp` group isn't installed.
"""

import asyncio
from typing import Any

import pytest

fastmcp = pytest.importorskip("fastmcp")

from fastmcp import Client, FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from fastmcp.server.middleware import Middleware, MiddlewareContext  # noqa: E402
from fastmcp.server.transforms import ToolTransform  # noqa: E402
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False, openWorldHint=False)
CLIENT_NAME_LIMIT = 64  # Anthropic API tool names: ^[a-zA-Z0-9_-]{1,64}$


def _child(name: str, tools: list[str]) -> FastMCP:
    mcp = FastMCP(name)
    for tool in tools:

        def make(tool: str):  # type: ignore[no-untyped-def]
            def fn(query: str = "", include_text: bool = True) -> dict[str, Any]:
                """Test tool."""
                return {"tool": tool, "query": query, "include_text": include_text}

            fn.__name__ = tool
            return fn

        mcp.tool(make(tool), annotations=READ_ONLY)
    mcp.resource(f"dk://{name}/about")(lambda: f"{name} about")
    mcp.prompt(name=f"{name}_prompt")(lambda: f"{name} prompt")
    return mcp


class _Gate(Middleware):
    """The profile-gate pattern: filter every list and refuse every call, read and get outside the profile."""

    def __init__(self, tools: set[str], prompts: set[str], resources: set[str]) -> None:
        self.tools, self.prompts, self.resources = tools, prompts, resources

    async def on_list_tools(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def,override]
        return [t for t in await call_next(context) if t.name in self.tools]

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def,override]
        if context.message.name not in self.tools:
            raise ToolError(f"[NOT_ALLOWED_FOR_PROFILE] {context.message.name}")
        return await call_next(context)

    async def on_list_prompts(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def,override]
        return [p for p in await call_next(context) if p.name in self.prompts]

    async def on_get_prompt(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def,override]
        if context.message.name not in self.prompts:
            raise ToolError("[NOT_ALLOWED_FOR_PROFILE]")
        return await call_next(context)

    async def on_list_resources(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def,override]
        return [r for r in await call_next(context) if str(r.uri) in self.resources]

    async def on_read_resource(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def,override]
        if str(context.message.uri) not in self.resources:
            raise ToolError("[NOT_ALLOWED_FOR_PROFILE]")
        return await call_next(context)


SERVERS = {
    "dk_models": ["score_drafts", "model_status", "competitor_complaints"],
    "dk_evidence": ["search_reviews", "get_sources"],
    "dk_brand": ["check_drafts"],
    "dk_studio": ["grounding_pack"],
}


def _gateway(gate: _Gate | None = None) -> FastMCP:
    gw = FastMCP("dk_intel")
    for name, tools in SERVERS.items():
        gw.mount(_child(name, tools))  # no namespace
    if gate is not None:
        hide_text = ArgTransformConfig(hide=True, default=False)
        gw.add_transform(ToolTransform({"get_sources": ToolTransformConfig(arguments={"include_text": hide_text})}))
        gw.add_middleware(gate)
    return gw


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_mount_without_namespace_keeps_plain_names_and_annotations() -> None:
    async def go() -> None:
        async with Client(_gateway()) as c:
            tools = await c.list_tools()
            assert sorted(t.name for t in tools) == sorted(n for ns in SERVERS.values() for n in ns)
            assert all(t.annotations and t.annotations.readOnlyHint for t in tools)
            assert len(await c.list_prompts()) == 4
            assert len(await c.list_resources()) == 4

    _run(go())


def test_duplicate_names_across_mounted_servers_collapse_silently() -> None:
    """Why the gateway must test that names are unique: FastMCP keeps one and raises nothing."""

    async def go() -> None:
        gw = FastMCP("dup")
        gw.mount(_child("a", ["same"]))
        gw.mount(_child("b", ["same"]))
        async with Client(gw) as c:
            assert [t.name for t in await c.list_tools()] == ["same"]

    _run(go())


def test_gate_filters_lists_and_refuses_hidden_calls_reads_and_gets() -> None:
    gate = _Gate({"check_drafts", "get_sources"}, {"dk_brand_prompt"}, {"dk://dk_brand/about"})

    async def go() -> None:
        async with Client(_gateway(gate)) as c:
            assert sorted(t.name for t in await c.list_tools()) == ["check_drafts", "get_sources"]
            with pytest.raises(ToolError, match="NOT_ALLOWED_FOR_PROFILE"):
                await c.call_tool("score_drafts", {"query": "x"})
            assert [p.name for p in await c.list_prompts()] == ["dk_brand_prompt"]
            with pytest.raises(Exception):  # noqa: B017 - the client wraps it as an MCP error
                await c.get_prompt("dk_models_prompt")
            assert [str(r.uri) for r in await c.list_resources()] == ["dk://dk_brand/about"]
            with pytest.raises(Exception):  # noqa: B017
                await c.read_resource("dk://dk_models/about")

    _run(go())


def test_forced_argument_is_hidden_fixed_and_cannot_be_overridden() -> None:
    gate = _Gate({"get_sources"}, set(), set())

    async def go() -> None:
        async with Client(_gateway(gate)) as c:
            (tool,) = await c.list_tools()
            assert "include_text" not in tool.inputSchema.get("properties", {})
            result = await c.call_tool("get_sources", {"query": "q"})
            assert result.structured_content == {"tool": "get_sources", "query": "q", "include_text": False}
            with pytest.raises(ToolError, match="unexpected keyword"):
                await c.call_tool("get_sources", {"query": "q", "include_text": True})

    _run(go())


def test_longest_planned_client_tool_name_fits_the_limit() -> None:
    planned = ["competitor_complaints", "upcoming_occasions", "evaluate_drafts", "grounding_pack"]
    longest_key = "dk_intel_http"  # the longest config key we generate (§5.2)
    assert max(len(f"mcp__{longest_key}__{t}") for t in planned) <= CLIENT_NAME_LIMIT
