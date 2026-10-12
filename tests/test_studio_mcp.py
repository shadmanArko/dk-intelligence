"""The studio's MCP hub against a real (tiny) stdio MCP server: one connection, many threads, errors as data."""

import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from intel.studio.mcp import McpHub

SERVER = textwrap.dedent(
    """
    import time
    from fastmcp import FastMCP

    mcp = FastMCP("echo")

    @mcp.tool
    def echo(text: str, wait: float = 0.0) -> dict:
        time.sleep(wait)
        return {"echo": text}

    @mcp.tool
    def refuse() -> dict:
        raise ValueError("no")

    mcp.run()
    """
)


@pytest.fixture
def hub(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(SERVER)
    h = McpHub({"echo": {"command": sys.executable, "args": [str(script)]}})
    yield h
    h.close()


def test_one_connection_serves_many_threads_at_once_and_lists_its_tools(hub):
    assert hub.call("echo", "echo", {"text": "hi"}) == {"echo": "hi"}
    assert sorted(hub.offered("echo")) == ["echo", "refuse"]
    t0 = time.monotonic()
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(lambda i: hub.call("echo", "echo", {"text": str(i), "wait": 0.4}), range(8)))
    assert [o["echo"] for o in out] == [str(i) for i in range(8)]
    assert time.monotonic() - t0 < 2.0  # eight 0.4 s calls overlapped: not 3.2 s one after the other


def test_a_refusing_tool_is_data_not_an_exception(hub):
    assert "error" in hub.call("echo", "refuse", {})
