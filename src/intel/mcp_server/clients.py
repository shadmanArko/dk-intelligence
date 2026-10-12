"""Ready-to-paste client configs (`intel mcp config`). They name .env keys and paths, never secret values."""

from __future__ import annotations

import json

from intel.settings import McpSettings, repo_root

CLIENTS = ("claude-code", "claude-desktop", "langchain", "openai-agents")


def _stdio_args(profile: str) -> list[str]:
    return ["--directory", str(repo_root()), "run", "--quiet", "intel", "mcp", "serve", "--profile", profile]


def client_config(client: str, profile: str, transport: str) -> str:
    from intel.mcp_server.gateway import profiles

    if client not in CLIENTS:
        raise ValueError(f"client must be one of: {', '.join(CLIENTS)}")
    if profile not in profiles():
        raise ValueError(f"unknown profile {profile!r}")
    if transport not in ("stdio", "http"):
        raise ValueError("transport must be stdio or http")
    s = McpSettings()
    url = f"http://127.0.0.1:{s.intel_mcp_http_port}/mcp/{profile}/"
    key = f"INTEL_MCP_TOKEN_{client.upper().replace('-', '_')}"
    if client == "claude-desktop":
        if transport == "http":
            raise ValueError("Claude Desktop is configured with stdio entries only")
        return json.dumps({"mcpServers": {"dk_intel": {"command": "uv", "args": _stdio_args(profile)}}}, indent=2)
    if client == "claude-code":
        if transport == "stdio":
            return json.dumps({"mcpServers": {"dk_intel": {"command": "uv", "args": _stdio_args(profile)}}}, indent=2)
        helper = f"uv --directory '{repo_root()}' run --quiet intel mcp token header --profile {profile} --ttl 300"
        entry = {"type": "http", "url": url, "headersHelper": helper}
        return json.dumps({"mcpServers": {"dk_intel_http": entry}}, indent=2)
    issue = f"uv run intel mcp token issue --client {client} --profile {profile} --ttl 30d --write-env {key}"
    if client == "langchain":
        return (
            f"# 1) {issue}\n# 2) uv run intel mcp serve --transport http\n"
            f"# 3) see examples/clients/langchain_agent.py\n"
            f'from fastmcp import Client\nclient = Client("{url}", auth=os.environ["{key}"])\n'
            "async with MCPAdapter(client) as mcp:\n    tools = await mcp.list_tools()\n"
        )
    return (
        f"# 1) {issue}\n# 2) uv run intel mcp serve --transport http\n"
        f"# 3) see examples/clients/openai_agents_ollama.py\n"
        "from agents.mcp import MCPServerStreamableHttp\n"
        f'server = MCPServerStreamableHttp(params={{"url": "{url}", '
        f'"headers": {{"Authorization": f"Bearer {{os.environ[\'{key}\']}}"}}}})\n'
    )
