"""Code calling MCP servers directly (no model): one long-lived connection per server for the whole run.

The lab started a fresh server process for every call (about a second each); a 150-post run makes hundreds of calls,
so the studio keeps one connection per server and any thread can call through it. Each connection lives in its own
task on a background event loop (opened and closed by that one task, as the MCP client requires) and serves its
requests concurrently.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from intel.studio.config import REPO

SERVER_TIMEOUT_S = 180.0
Request = tuple[str, dict[str, Any], "asyncio.Future[Any]"]


def server_specs(reels: Path) -> dict[str, dict[str, Any]]:
    """The MCP servers a run may use, by the names agent files and tool ids use (`mcp__<name>__<tool>`)."""
    intel = ["run", "--quiet", "--directory", str(REPO), "intel", "mcp", "serve", "--profile"]
    return {
        "dk_intel_planner": {"command": "uv", "args": [*intel, "planner"]},
        "dk_intel_writer": {"command": "uv", "args": [*intel, "writer"]},
        "dk_reels_creative": {"command": "npm", "args": ["--prefix", str(reels), "run", "-s", "mcp", "--",
                                                         "--profile", "creative", "--root", str(reels)]},
    }  # fmt: skip


class McpHub:
    """A ToolPort over stdio MCP servers. Safe to call from many threads."""

    def __init__(self, servers: dict[str, dict[str, Any]]) -> None:
        self._servers = servers
        self._offered: dict[str, list[str]] = {}
        self._queues: dict[str, asyncio.Queue[Request | None]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-hub", daemon=True)
        self._thread.start()
        self._lock: asyncio.Lock | None = None

    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=SERVER_TIMEOUT_S + 60)

    async def _serve(self, server: str, queue: asyncio.Queue[Request | None], ready: asyncio.Future[None]) -> None:
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport

        spec = self._servers[server]
        inflight: set[asyncio.Task[None]] = set()
        try:
            async with Client(StdioTransport(spec["command"], spec["args"])) as client:
                self._offered[server] = [t.name for t in await client.list_tools()]
                ready.set_result(None)
                while (req := await queue.get()) is not None:
                    task = asyncio.create_task(self._handle(client, *req))
                    inflight.add(task)
                    task.add_done_callback(inflight.discard)
                await asyncio.gather(*inflight, return_exceptions=True)
        except Exception as ex:  # noqa: BLE001 - the connection is gone: whoever waits is told, the next call reconnects
            if not ready.done():
                ready.set_exception(ex)
            while not queue.empty():
                req = queue.get_nowait()
                if req is not None and not req[2].done():
                    req[2].set_exception(ex)

    @staticmethod
    async def _handle(client: Any, tool: str, args: dict[str, Any], fut: asyncio.Future[Any]) -> None:
        try:
            r = await asyncio.wait_for(client.call_tool(tool, args, raise_on_error=False), SERVER_TIMEOUT_S)
            text = "".join(getattr(b, "text", "") for b in r.content)
            if r.is_error:
                fut.set_result({"error": text[:300]})
                return
            try:
                fut.set_result(json.loads(text))
            except json.JSONDecodeError:
                fut.set_result({"text": text})
        except Exception as ex:  # noqa: BLE001 - a timeout or a dead server: the caller decides
            if not fut.done():
                fut.set_exception(ex)

    async def _queue(self, server: str) -> asyncio.Queue[Request | None]:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            task = self._tasks.get(server)
            if task is None or task.done():  # first use, or the server died: (re)connect
                queue: asyncio.Queue[Request | None] = asyncio.Queue()
                ready: asyncio.Future[None] = self._loop.create_future()
                self._queues[server] = queue
                self._tasks[server] = asyncio.create_task(self._serve(server, queue, ready))
                await ready
            return self._queues[server]

    async def _call(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        queue = await self._queue(server)
        fut: asyncio.Future[Any] = self._loop.create_future()
        await queue.put((tool, args, fut))
        return await fut

    def call(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        return self._run(self._call(server, tool, args))

    def offered(self, server: str) -> list[str]:
        if server not in self._offered:
            self._run(self._queue(server))
        return self._offered[server]

    def close(self) -> None:
        async def shut() -> None:
            for queue in self._queues.values():
                await queue.put(None)
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        try:
            self._run(shut())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
