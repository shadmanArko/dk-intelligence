"""A LangChain agent on a free local model (Ollama) using dk_intel as a *writer* over HTTP (€0, ADR 0029).

Setup once (tokens go into .env, never onto the screen):
    uv sync --group clients
    uv run intel mcp init --write-env
    uv run intel mcp token issue --client langchain --profile writer --ttl 30d --write-env INTEL_MCP_TOKEN_LANGCHAIN
Run:
    uv run intel mcp serve --transport http          # in another terminal
    uv run python examples/clients/langchain_agent.py "Write two Instagram captions about Borhani"
The writer profile sees 7 tools; the server refuses everything else whatever the model tries.
"""

from __future__ import annotations

import asyncio
import os
import sys
import warnings

from fastmcp import Client
from langchain.agents import create_agent
from langchain_core._api import LangChainBetaWarning
from langchain_ollama import ChatOllama

warnings.filterwarnings("ignore", category=LangChainBetaWarning)
from langchain.mcp import MCPAdapter  # noqa: E402 - beta namespace: import after silencing its warning

URL = os.environ.get("DK_INTEL_URL", "http://127.0.0.1:8766/mcp/writer/")
MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:20b")


def token(key: str = "INTEL_MCP_TOKEN_LANGCHAIN") -> str:
    """From the environment or the repo's .env; never printed."""
    from dotenv import dotenv_values

    from intel.settings import repo_root

    value = os.environ.get(key) or dotenv_values(repo_root() / ".env").get(key)
    if not value:
        sys.exit("INTEL_MCP_TOKEN_LANGCHAIN is not set: see the setup steps at the top of this file")
    return str(value)


async def main(task: str) -> None:
    client = Client(URL, auth=token())
    async with client:  # MCPAdapter wraps tools only: fetch the role prompt directly
        role = await client.get_prompt("role_writer")
    system = role.messages[0].content.text  # type: ignore[union-attr]
    async with MCPAdapter(client) as mcp:
        tools = await mcp.list_tools()
        agent = create_agent(ChatOllama(model=MODEL, temperature=0, num_ctx=32768), tools, system_prompt=system)
        result = await agent.ainvoke({"messages": [{"role": "user", "content": task}]})
    calls = [m.name for m in result["messages"] if getattr(m, "type", "") == "tool"]
    print(f"tools called: {calls}\n")
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "Write two Instagram captions about Borhani for this Saturday."))
