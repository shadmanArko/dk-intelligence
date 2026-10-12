"""P4.5: dk_intel over Streamable HTTP with JWTs (ADR 0029): a real server on a free loopback port.

Threat T10 in the architecture: 401 without a valid token, a token for one profile can't open another, hidden tools
stay hidden and refused, revocation, Host protection, the Tailscale login header (stripped on the local listener,
required for the owner on the tailnet listener), the sensitivity ceiling, and one contract over every transport.
"""

import asyncio
import socket
import threading
import time
import warnings

import httpx
import pytest
import uvicorn
from fastmcp import Client

from intel.mcp_server import auth
from intel.mcp_server.gateway import build_gateway, profiles
from intel.mcp_server.http import build_http_app
from intel.settings import McpSettings
from tests.test_mcp_gateway import deps  # noqa: F401 - deps is a fixture

SECRET = "s" * 48
OWNER = "owner@example.com"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start(app):  # type: ignore[no-untyped-def]
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


@pytest.fixture
def settings(tmp_path) -> McpSettings:
    return McpSettings(
        intel_mcp_secret=SECRET, intel_mcp_revoked_file=tmp_path / "revoked.txt", intel_mcp_owner_logins=OWNER
    )


@pytest.fixture
def servers(settings, deps):  # noqa: F811
    local, local_url = _start(build_http_app(settings, deps, "local", only=["owner", "writer", "analyst"]))
    tailnet, tailnet_url = _start(build_http_app(settings, deps, "tailnet", only=["owner", "writer"]))
    yield {"local": local_url, "tailnet": tailnet_url}
    local.should_exit = tailnet.should_exit = True


def _tok(profile: str, **kw) -> str:  # type: ignore[no-untyped-def]
    return auth.issue(SECRET, "test", profile, kw.pop("ttl", 300), **kw)[0]


async def _names(url: str, token: str | None, headers: dict[str, str] | None = None) -> list[str]:
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(url, auth=token, headers=headers or {})
    async with Client(transport) as c:
        return [t.name for t in await c.list_tools()]


def _refused(coro) -> bool:  # type: ignore[no-untyped-def]
    try:
        asyncio.run(coro)
    except Exception:  # noqa: BLE001 - any refusal (401, 403, protocol error) is the expected outcome
        return True
    return False


def test_a_profile_token_opens_only_its_own_path(servers):
    writer = asyncio.run(_names(f"{servers['local']}/mcp/writer/", _tok("writer")))
    assert writer == sorted(profiles()["writer"]["tools"]) and "find_creators" not in writer
    assert _refused(_names(f"{servers['local']}/mcp/owner/", _tok("writer")))  # wrong scope
    assert _refused(_names(f"{servers['local']}/mcp/writer/", None))  # no token

    async def call_hidden() -> None:
        async with Client(f"{servers['local']}/mcp/writer/", auth=_tok("writer")) as c:
            await c.call_tool("find_creators", {})

    assert _refused(call_hidden())  # hidden AND refused


def test_bad_tokens_get_401(servers, settings):
    url = f"{servers['local']}/mcp/writer/"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

    def status(token: str | None) -> int:
        h = {**hdr, **({"Authorization": f"Bearer {token}"} if token else {})}
        return httpx.post(url, json=body, headers=h, timeout=10).status_code

    assert status(None) == 401
    assert status(_tok("writer", now=time.time() - 3600)) == 401  # expired
    assert status(_tok("writer", host="vps")) == 401  # another host's audience
    assert status(auth.issue("o" * 48, "x", "writer", 300)[0]) == 401  # signed with another secret
    revoked, jti = auth.issue(SECRET, "test", "writer", 300)
    auth.Revocations(settings.revoked_file).add(jti)
    assert status(revoked) == 401
    assert httpx.get(f"{servers['local']}/healthz", timeout=10).json() == {"status": "ok"}


def test_host_protection(servers):
    r = httpx.post(
        f"{servers['local']}/mcp/writer/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Host": "evil.example",
            "Authorization": f"Bearer {_tok('writer')}",
            "Accept": "application/json, text/event-stream",
        },  # fmt: skip
        timeout=10,
    )
    assert r.status_code == 421


def test_the_tailscale_login_header(servers):
    owner = _tok("owner")
    forged = {"Tailscale-User-Login": OWNER}
    # tailnet listener: the owner profile needs an owner login on top of the token
    assert _refused(_names(f"{servers['tailnet']}/mcp/owner/", owner))
    assert _refused(_names(f"{servers['tailnet']}/mcp/owner/", owner, {"Tailscale-User-Login": "else@example.com"}))
    assert "score_drafts" in asyncio.run(_names(f"{servers['tailnet']}/mcp/owner/", owner, forged))
    assert asyncio.run(_names(f"{servers['tailnet']}/mcp/writer/", _tok("writer")))  # other roles: the token decides
    # local listener: the header is stripped (nobody can forge it), the signed token alone decides
    assert "score_drafts" in asyncio.run(_names(f"{servers['local']}/mcp/owner/", owner, forged))


def test_the_sensitivity_ceiling(servers):
    full = asyncio.run(_names(f"{servers['local']}/mcp/analyst/", _tok("analyst")))
    s1 = asyncio.run(_names(f"{servers['local']}/mcp/analyst/", _tok("analyst", max_sensitivity="S1")))
    assert "search_reviews" in full and "search_reviews" not in s1 and "funnel_status" in s1

    async def call_above() -> None:
        async with Client(f"{servers['local']}/mcp/analyst/", auth=_tok("analyst", max_sensitivity="S1")) as c:
            await c.call_tool("search_reviews", {"query": "kalt"})

    assert _refused(call_above())


def test_one_contract_in_memory_over_http_and_through_langchain(servers, deps):  # noqa: F811
    async def in_memory() -> list[str]:
        async with Client(build_gateway("writer", deps)) as c:
            return [t.name for t in await c.list_tools()]

    over_http = asyncio.run(_names(f"{servers['local']}/mcp/writer/", _tok("writer")))
    assert asyncio.run(in_memory()) == over_http
    lc = pytest.importorskip("langchain.mcp", reason="the clients group is not installed")

    async def via_langchain() -> tuple[list[str], str]:
        client = Client(f"{servers['local']}/mcp/writer/", auth=_tok("writer"))
        async with lc.MCPAdapter(client) as mcp:
            tools = await mcp.list_tools()
            by_name = {t.name: t for t in tools}
            out = await by_name["get_facts"].ainvoke({"keys": ["menu"]})
        return sorted(by_name), str(out)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        names, facts = asyncio.run(via_langchain())
    assert names == over_http and "menu" in facts  # LangChain loads every tool and calls one, no LLM


def test_tokens_are_bounded_and_never_shown_on_a_terminal(monkeypatch):
    with pytest.raises(ValueError):
        auth.issue(SECRET, "x", "writer", 91 * 86400)
    with pytest.raises(ValueError):
        auth.issue("short", "x", "writer", 300)
    with pytest.raises(ValueError):
        auth.issue(SECRET, "x", "writer", 300, max_sensitivity="S9")
    assert auth.ceiling_of({"dk_max_sensitivity": "bogus"}) == "S0" and auth.ceiling_of(None) == "S2"
    from typer.testing import CliRunner

    from intel.cli import app

    monkeypatch.delenv("CLAUDE_CODE_MCP_SERVER_NAME", raising=False)
    out = CliRunner().invoke(app, ["mcp", "token", "header", "--profile", "owner"])
    assert out.exit_code == 2 and "Bearer" not in out.output  # outside Claude Code's helper: refused


def test_the_catalog_is_current(deps):  # noqa: F811
    from intel.mcp_server import catalog

    assert catalog.CATALOG.read_text() == catalog.render(deps), "run `uv run intel mcp catalog` and commit it"
