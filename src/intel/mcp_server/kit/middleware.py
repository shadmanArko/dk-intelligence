"""The shared middleware every dk_intel server runs, outermost first (architecture §3.3).

P4.1: context (request id, principal), audit (hash chain, denials included), profile gate (lists filtered, calls,
reads and gets refused), copy guard (blocked words in our own copy fields). Error masking and rate limiting are
FastMCP's own middleware, added by the gateway. Metrics, cache, egress and budget arrive in P4.4-P4.6.
"""

from __future__ import annotations

import getpass
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from intel.adapters.intel_api import REQUEST_ID
from intel.capabilities.checks import blocked_spans
from intel.mcp_server.kit.audit import AuditLog, args_hash


def stdio_principal() -> str:
    try:
        return f"stdio:{getpass.getuser()}"
    except Exception:  # noqa: BLE001 - no user in some containers
        return "stdio:unknown"


class ContextMiddleware(Middleware):
    """A request id per call (forwarded to intel-api as X-Request-ID)."""

    async def on_request(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        token = REQUEST_ID.set(uuid.uuid4().hex[:16])
        try:
            return await call_next(context)
        finally:
            REQUEST_ID.reset(token)


class AuditMiddleware(Middleware):
    """One chain entry per tool call, resource read and prompt get, with status and duration. Arguments: hash only."""

    def __init__(self, log: AuditLog, principal: str, profile: str) -> None:
        self.log, self.principal, self.profile = log, principal, profile

    async def _record(
        self, kind: str, name: str, arguments: Any, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        t0 = time.perf_counter()
        status = "ok"
        try:
            result = await call_next(context)
        except ToolError as e:
            status = "denied" if "NOT_ALLOWED_FOR_PROFILE" in str(e) else "error"
            raise
        except Exception:
            status = "error"
            raise
        finally:
            self.log.append(
                request_id=REQUEST_ID.get(),
                principal=self.principal,
                profile=self.profile,
                kind=kind,
                name=name,
                args=args_hash(arguments),
                status=status,
                ms=round((time.perf_counter() - t0) * 1000, 1),
            )
        return result

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        m = context.message
        return await self._record("tool", m.name, m.arguments, context, call_next)

    async def on_read_resource(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        return await self._record("resource", str(context.message.uri), None, context, call_next)

    async def on_get_prompt(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        m = context.message
        return await self._record("prompt", m.name, m.arguments, context, call_next)


class ProfileGate(Middleware):
    """Least privilege per profile: hidden in every list AND refused on call, read and get (a list alone blocks
    nothing). Also the per-tool kill switch (INTEL_MCP_DISABLED_TOOLS)."""

    def __init__(
        self, tools: frozenset[str], resources: frozenset[str], prompts: frozenset[str], disabled: frozenset[str]
    ) -> None:
        self.tools, self.resources, self.prompts, self.disabled = tools, resources, prompts, disabled

    def _prompt_ok(self, name: str) -> bool:
        return "*" in self.prompts or name in self.prompts

    def _resource_ok(self, uri: str) -> bool:
        return any(uri == r or (r.endswith("*") and uri.startswith(r[:-1])) for r in self.resources)

    async def on_list_tools(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        tools = await call_next(context)
        return sorted((t for t in tools if t.name in self.tools and t.name not in self.disabled), key=lambda t: t.name)

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        name = context.message.name
        if name in self.disabled:
            raise ToolError(f"[FEATURE_OFF] {name} is switched off on this server")
        if name not in self.tools:
            raise ToolError(f"[NOT_ALLOWED_FOR_PROFILE] {name} is not available to this client")
        return await call_next(context)

    async def on_list_resources(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        return [r for r in await call_next(context) if self._resource_ok(str(r.uri))]

    async def on_list_resource_templates(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        return [t for t in await call_next(context) if self._resource_ok(str(t.uri_template))]

    async def on_read_resource(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        if not self._resource_ok(str(context.message.uri)):
            raise ToolError("[NOT_ALLOWED_FOR_PROFILE] this resource is not available to this client")
        return await call_next(context)

    async def on_list_prompts(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        return [p for p in await call_next(context) if self._prompt_ok(p.name)]

    async def on_get_prompt(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        if not self._prompt_ok(context.message.name):
            raise ToolError("[NOT_ALLOWED_FOR_PROFILE] this prompt is not available to this client")
        return await call_next(context)


COPY_FIELDS = frozenset({"label", "names", "greeting", "text", "hashtags", "advice", "event"})


class CopyGuard(Middleware):
    """Blocked words in the copy fields our tools pass on (occasion labels, greetings, hashtags). It should never fire:
    the planner's build gate scrubs them. On a hit the field is withheld and a warning names the gate, loudly."""

    def __init__(self, blocked_words: Callable[[], list[str] | None], log: AuditLog) -> None:
        self.blocked_words, self.log = blocked_words, log

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        result = await call_next(context)
        data = getattr(result, "structured_content", None)
        if not isinstance(data, dict) or context.message.name in {"check_drafts", "brand_rules", "get_facts"}:
            return result
        words = self.blocked_words()
        if not words:
            return result
        hits: list[str] = []
        cleaned = _scrub(data, words, "", hits)
        if not hits:
            return result
        self.log.append(request_id=REQUEST_ID.get(), kind="copy_guard_hit", name=context.message.name, fields=hits)
        cleaned["warnings"] = [
            *cleaned.get("warnings", []),
            f"copy_guard withheld {len(hits)} field(s): {', '.join(hits)}",
        ]
        return ToolResult(content=json.dumps(cleaned, default=str), structured_content=cleaned)


def _scrub(value: Any, words: list[str], path: str, hits: list[str]) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            p = f"{path}.{k}" if path else k
            if k in COPY_FIELDS and _has_blocked(v, words) and not _is_untrusted(value):
                hits.append(p)
                out[k] = None
            else:
                out[k] = _scrub(v, words, p, hits)
        return out
    if isinstance(value, list):
        return [_scrub(v, words, f"{path}[{i}]", hits) for i, v in enumerate(value)]
    return value


def _is_untrusted(container: dict[str, Any]) -> bool:
    """Quotes from third-party text are evidence, not our copy: never censored (clients are told they are untrusted)."""
    return container.get("trust") == "untrusted"


def _has_blocked(value: Any, words: list[str]) -> bool:
    if isinstance(value, str):
        return bool(blocked_spans(value, words))
    if isinstance(value, list):
        return any(_has_blocked(v, words) for v in value)
    if isinstance(value, dict):
        return any(_has_blocked(v, words) for v in value.values())
    return False
