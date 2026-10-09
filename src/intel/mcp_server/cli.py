"""`intel mcp …` and `intel pack …` (architecture §3.6). FastMCP is imported inside the commands that need it."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

mcp_app = typer.Typer(no_args_is_help=True, help="The dk_intel MCP server (read-only; ADR 0025)")
pack_app = typer.Typer(no_args_is_help=True, help="Knowledge packs the MCP servers read")

Profile = Annotated[str, typer.Option(help="owner, analyst, planner, writer, producer or checker")]


def _gateway(profile: str, server: str | None = None) -> Any:
    from intel.mcp_server.deps import Deps
    from intel.mcp_server.gateway import build_gateway
    from intel.mcp_server.kit.middleware import stdio_principal
    from intel.settings import McpSettings

    return build_gateway(profile, Deps.from_settings(McpSettings(), stdio_principal()), only=server)


@mcp_app.command("serve")
def serve(
    profile: Profile = "owner",
    server: Annotated[str | None, typer.Option(help="run one server alone: models or brand")] = None,
) -> None:
    """Serve over stdio (what Claude Code and Claude Desktop start). Prints nothing to stdout itself."""
    _gateway(profile, server).run(transport="stdio", show_banner=False)


@mcp_app.command("tools")
def tools(profile: Profile = "owner") -> None:
    """List the tools a profile sees, with an estimate of their size in tokens (bytes / 3)."""
    from fastmcp import Client

    async def go() -> None:
        async with Client(_gateway(profile)) as c:
            listed = await c.list_tools()
            total = 0
            for t in listed:
                size = len(json.dumps(t.model_dump(mode="json", exclude_none=True)))
                total += size
                typer.echo(f"{t.name:<24} ~{size // 3:>5} tokens")
            typer.echo(f"{len(listed)} tools, ~{total // 3} tokens for the whole list")

    asyncio.run(go())


@mcp_app.command("call")
def call(
    tool: str,
    arguments: Annotated[str, typer.Argument(help="JSON object")] = "{}",
    profile: Profile = "owner",
) -> None:
    """Call one tool in memory, as a client would (debugging)."""
    from fastmcp import Client

    async def go() -> None:
        async with Client(_gateway(profile)) as c:
            r = await c.call_tool(tool, json.loads(arguments), raise_on_error=False)
            if r.is_error:
                typer.echo(" ".join(getattr(b, "text", "") for b in r.content), err=True)
                raise typer.Exit(1)
            typer.echo(json.dumps(r.structured_content, indent=2, ensure_ascii=False, default=str))

    asyncio.run(go())


@mcp_app.command("audit")
def audit(action: Annotated[str, typer.Argument(help="verify | tail")] = "verify") -> None:
    """Check the audit hash chain, or show its last entries."""
    from intel.mcp_server.kit.audit import AuditLog
    from intel.settings import McpSettings

    log = AuditLog(McpSettings().audit_dir)
    if action == "verify":
        result = log.verify()
        typer.echo(json.dumps(result))
        raise typer.Exit(0 if result["ok"] else 1)
    for e in list(log.entries())[-20:]:
        typer.echo(
            f"{e['at']}  {e.get('status', ''):<7} {e.get('kind', ''):<9} {e.get('name', '')}  {e.get('ms', '')} ms"
        )


@pack_app.command("build")
def pack_build(
    brand_only: Annotated[bool, typer.Option("--brand-only", help="P4.1: the brand part only")] = True,
    activate: Annotated[bool, typer.Option(help="make it the active pack")] = True,
) -> None:
    """Build a new, immutable knowledge pack from the data platform's brand files and the latest market snapshot."""
    from intel.rag import pack as kp
    from intel.settings import McpSettings

    s = McpSettings()
    pack_id = kp.build_brand_pack(s.path(s.mip_dir), s.data_dir / "market", s.pack_dir)
    if activate:
        kp.activate(s.pack_dir, pack_id)
    typer.echo(f"{pack_id} built{' and active' if activate else ''}")


@pack_app.command("status")
def pack_status() -> None:
    from intel.rag import pack as kp
    from intel.settings import McpSettings

    typer.echo(json.dumps(kp.status(McpSettings().pack_dir), indent=2))


@pack_app.command("activate")
def pack_activate(pack_id: str) -> None:
    from intel.rag import pack as kp
    from intel.settings import McpSettings

    kp.activate(McpSettings().pack_dir, pack_id)
    typer.echo(f"{pack_id} is active")
