"""The shared middleware every dk_intel server runs, outermost first (architecture §3.3).

P4.1: context (request id, principal), audit (hash chain, denials included), profile gate (lists filtered, calls,
reads and gets refused), copy guard (blocked words in our own copy fields). P4.4: budget (a token cap per tool).
Error masking, rate limiting and the response-size backstop are FastMCP's own middleware, added by the gateway.
Metrics, cache and egress arrive in P4.5-P4.6.
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


def token_claims() -> dict[str, Any] | None:
    """The verified JWT's claims on HTTP; None on stdio and in memory."""
    from fastmcp.server.dependencies import get_access_token

    try:
        token = get_access_token()
    except RuntimeError:
        return None
    return None if token is None else dict(token.claims)


def token_principal() -> str | None:
    claims = token_claims()
    return None if claims is None else f"jwt:{claims.get('sub')}:{str(claims.get('jti', ''))[:8]}"


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
        tokens: int | None = None
        try:
            result = await call_next(context)
            data = getattr(result, "structured_content", None)
            tokens = est_tokens(data) if data is not None else None
        except ToolError as e:
            status = "denied" if "NOT_ALLOWED_FOR_PROFILE" in str(e) else "error"
            raise
        except Exception:
            status = "error"
            raise
        finally:
            ms = round((time.perf_counter() - t0) * 1000, 1)
            self.log.append(
                request_id=REQUEST_ID.get(),
                principal=token_principal() or self.principal,
                profile=self.profile,
                kind=kind,
                name=name,
                args=args_hash(arguments),
                status=status,
                ms=ms,
                tokens=tokens,
            )
            if kind == "tool":
                from intel.mcp_server.kit import metrics

                metrics.observe(name, self.profile, status, ms / 1000, tokens)
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


def est_tokens(data: Any) -> int:
    """The budget estimate: UTF-8 bytes of the compact JSON / 3."""
    return len(json.dumps(data, default=str, ensure_ascii=False, separators=(",", ":")).encode()) // 3


def _longest_list(value: Any, path: tuple[Any, ...] = ()) -> tuple[tuple[Any, ...], int] | None:
    best: tuple[tuple[Any, ...], int] | None = None
    if isinstance(value, dict):
        items: Any = value.items()
    elif isinstance(value, list):
        if len(value) > 1:
            best = (path, len(value))
        items = enumerate(value)
    else:
        return None
    for k, v in items:
        sub = _longest_list(v, (*path, k))
        if sub is not None and (best is None or sub[1] > best[1]):
            best = sub
    return best


class BudgetMiddleware(Middleware):
    """A result over its tool's token budget loses items from its longest list until it fits, and says so: the client
    gets a complete, valid answer with a hint to narrow the request, never JSON cut in the middle."""

    def __init__(self, budgets: dict[str, int], log: AuditLog) -> None:
        self.budgets, self.log = budgets, log

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        result = await call_next(context)
        data = getattr(result, "structured_content", None)
        budget = self.budgets.get(context.message.name)
        if budget is None or not isinstance(data, dict) or est_tokens(data) <= budget:
            return result
        data = json.loads(json.dumps(data, default=str))
        before = est_tokens(data)
        cut = 0
        while est_tokens(data) > budget and (found := _longest_list(data)) is not None:
            node = data
            for k in found[0]:
                node = node[k]
            node.pop()
            cut += 1
        data["budget_note"] = (
            f"result trimmed to the {budget}-token budget ({cut} list items removed): narrow the request "
            "(a filter, a smaller limit or response_format='concise')"
        )
        self.log.append(
            request_id=REQUEST_ID.get(), kind="budget_cut", name=context.message.name, before=before, cut=cut
        )
        return ToolResult(content=json.dumps(data, default=str), structured_content=data)


ENUM_ARGS = frozenset(
    {"platform", "source", "sort", "direction", "section", "response_format", "content_type", "lang", "collection",
     "size", "tag_type", "format", "occasion_tone"}
)  # fmt: skip


def _lower_enums(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: v.strip().lower() if k in ENUM_ARGS and isinstance(v, str) else _lower_enums(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_lower_enums(v) for v in value]
    return value


class NormaliseArgs(Middleware):
    """Models often send "TikTok" or "Instagram " for an enum value; the tool-use eval saw 11 such refusals and a
    retry each (tokens). Enum-like arguments are lower-cased and trimmed before validation; nothing else changes."""

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        args = context.message.arguments
        if isinstance(args, dict):
            context.message.arguments = _lower_enums(args)
        return await call_next(context)


class SensitivityCeiling(Middleware):
    """A token may carry `dk_max_sensitivity` (e.g. S1 keeps review and creator text, S2, away from a client whose
    model provider should not see it). Tools labelled above the ceiling are hidden and refused, like the profile gate.
    No token (stdio, in memory) means the default ceiling."""

    async def on_list_tools(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        from intel.mcp_server.auth import SENSITIVITY, ceiling_of

        top = SENSITIVITY.index(ceiling_of(token_claims()))
        tools = await call_next(context)
        return [t for t in tools if SENSITIVITY.index(_sensitivity(t)) <= top]

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        from intel.mcp_server.auth import SENSITIVITY, ceiling_of

        ceiling = ceiling_of(token_claims())
        server = context.fastmcp_context.fastmcp if context.fastmcp_context else None
        tool = await server.get_tool(context.message.name) if server is not None else None
        if tool is None or SENSITIVITY.index(_sensitivity(tool)) > SENSITIVITY.index(ceiling):
            raise ToolError(f"[NOT_ALLOWED_FOR_PROFILE] {context.message.name} is above this token's {ceiling} ceiling")
        return await call_next(context)


def _sensitivity(tool: Any) -> str:
    value = (getattr(tool, "meta", None) or {}).get("dk/sensitivity", "S3")  # unlabelled fails closed
    return str(value) if value in ("S0", "S1", "S2", "S3") else "S3"


CACHEABLE = frozenset(
    {"upcoming_occasions", "competitor_complaints", "community_areas", "funnel_status", "find_creators",
     "brand_rules", "get_facts", "trend_signals", "grounding_pack"}
)  # fmt: skip


class ResultCache(Middleware):
    """Slow, weekly-changing answers (insights over the tailnet, brand facts, trends, the grounding pack) are kept for
    `ttl_s`. Key: tool + arguments + the token's ceiling + the active knowledge pack, so a new pack or a narrower
    token never gets a stale or wider answer. Errors, searches and model status are never cached. It sits inside
    the profile gate: a cached answer is still only served to a profile that may call the tool."""

    def __init__(self, pack_id: Callable[[], str | None], ttl_s: float = 300.0, max_entries: int = 256) -> None:
        self.pack_id, self.ttl_s, self.max_entries = pack_id, ttl_s, max_entries
        self._store: dict[str, tuple[float, Any]] = {}

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        from intel.mcp_server.auth import ceiling_of

        name = context.message.name
        if name not in CACHEABLE:
            return await call_next(context)
        key = json.dumps(
            [name, context.message.arguments or {}, ceiling_of(token_claims()), self.pack_id()],
            sort_keys=True,
            default=str,
        )
        now = time.monotonic()
        hit = self._store.get(key)
        if hit is not None and now - hit[0] < self.ttl_s:
            from intel.mcp_server.kit import metrics

            metrics.CACHE_HITS.labels(name).inc()
            return hit[1]
        result = await call_next(context)
        if not getattr(result, "is_error", False):
            if len(self._store) >= self.max_entries:
                self._store.pop(next(iter(self._store)))
            self._store[key] = (now, result)
        return result
