"""dk_intel over Streamable HTTP (ADR 0029, architecture §6.1): one app, one path per profile, a JWT per call.

- `/mcp/{profile}`: that profile's gateway (stateless, JSON responses). A token must be signed by this host, be for
  this audience, not be expired or revoked, and carry `scope=<profile>`: a writer token can't open `/mcp/owner`.
- `/healthz`: `{"status": "ok"}` only (custom routes are never authenticated, so they never say more).
- Two listeners, both bound to 127.0.0.1:
  - `local` (8766): for clients on this Mac. A `Tailscale-User-Login` header is stripped, so nobody can forge it.
  - `tailnet` (8767): the only target of `tailscale serve`, which sets that header from the caller's tailnet
    identity. Here the owner profile also needs the login to be in `INTEL_MCP_OWNER_LOGINS`.
- Host and Origin are checked against an allowlist (DNS-rebinding protection).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any, Literal

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from intel.mcp_server.auth import ProfileVerifier, Revocations
from intel.mcp_server.deps import Deps
from intel.mcp_server.gateway import build_gateway, profiles
from intel.settings import McpSettings

Listener = Literal["local", "tailnet"]
LOGIN_HEADER = b"tailscale-user-login"


class TailnetIdentity:
    """Strips the Tailscale login header on the local listener; on the tailnet listener, refuses the owner path
    unless the login is one of the owner's."""

    def __init__(self, app: ASGIApp, listener: Listener, owner_logins: frozenset[str]) -> None:
        self.app, self.listener, self.owner_logins = app, listener, owner_logins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != LOGIN_HEADER]
            login = next((v.decode().strip().lower() for k, v in scope["headers"] if k.lower() == LOGIN_HEADER), None)
            if self.listener == "local":
                scope = {**scope, "headers": headers}
            elif scope["path"].startswith("/mcp/owner") and login not in self.owner_logins:
                await JSONResponse({"error": "owner profile needs an owner tailnet login"}, status_code=403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def build_http_app(
    settings: McpSettings, deps: Deps, listener: Listener = "local", only: list[str] | None = None
) -> ASGIApp:
    if not settings.intel_mcp_secret:
        raise RuntimeError("INTEL_MCP_SECRET is not set: run `uv run intel mcp init --write-env` first")
    revoked = Revocations(settings.revoked_file)
    names = only or list(profiles())
    apps: dict[str, Any] = {}
    for name in names:
        verifier = ProfileVerifier(settings.intel_mcp_secret, name, revoked, settings.intel_mcp_host)
        gateway = build_gateway(name, deps, auth=verifier)
        apps[name] = gateway.http_app(
            path="/",
            stateless_http=True,
            json_response=True,
            host_origin_protection=True,
            allowed_hosts=settings.allowed_hosts,
        )

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as stack:
            for app in apps.values():
                await stack.enter_async_context(app.lifespan(app))
            yield

    root = Starlette(
        routes=[Route("/healthz", healthz), *(Mount(f"/mcp/{p}", app=a) for p, a in apps.items())],
        lifespan=lifespan,
    )
    return TailnetIdentity(root, listener, settings.owner_logins)


def serve(settings: McpSettings, deps: Deps, listeners: list[Listener]) -> None:
    """Run the listeners in one process (one event loop), both bound to loopback."""
    import asyncio

    import uvicorn

    ports = {"local": settings.intel_mcp_http_port, "tailnet": settings.intel_mcp_tailnet_port}
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                build_http_app(settings, deps, listener), host="127.0.0.1", port=ports[listener], log_level="warning"
            )
        )
        for listener in listeners
    ]

    async def run() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(run())
