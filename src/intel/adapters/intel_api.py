"""intel-api over the tailnet, through an explicit (method, path) allowlist (ADR 0025, architecture §2.3).

Anything not allowlisted raises before any network I/O, so the MCP side can never reach /v1/predict (writes the
ledger), approvals, rollback or the HTML pages. The client's credentials are never forwarded: this adapter builds
its own requests. Scoring calls share two slots and a per-principal draft budget **across processes** (file locks
under data/mcp/limits/), because every Claude Code session starts its own stdio server.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import time
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import IO, Any, Final

import httpx

from intel.capabilities.types import ToolProblem

ALLOWED: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("GET", "/ready"),
        ("GET", "/v1/monitoring/latest"),  # last persisted kv('monitoring'); never computes or writes
        ("GET", "/v1/ledger/summary"),
        ("GET", "/api/training-state"),
        ("GET", "/v1/audit"),  # package list + decisions; `actor` is never passed on
        ("GET", "/v1/insights/{name}"),  # read-only, field allowlist per section (intel.exposure)
        ("POST", "/v1/score/batch"),  # never /v1/predict (it writes the ledger)
    }
)
INSIGHTS_NAME = re.compile(r"^[a-z_]{1,40}$")
REQUEST_ID: ContextVar[str | None] = ContextVar("intel_mcp_request_id", default=None)

SLOTS = 2  # scoring calls in flight to intel-api, host-wide
SLOT_WAIT_S = 3.0
DRAFTS_PER_MINUTE = 600  # per principal, host-wide
ATTEMPT_TIMEOUT_S = 3.0
RETRY_AFTER_S = 0.5


def route_template(method: str, path: str) -> tuple[str, str]:
    """The allowlist key of a concrete request; raises for anything not allowlisted."""
    key = (method, path)
    if path.startswith("/v1/insights/"):
        name = path.removeprefix("/v1/insights/")
        if not INSIGHTS_NAME.match(name):
            raise PermissionError(f"refused: {method} {path}")
        key = (method, "/v1/insights/{name}")
    if key not in ALLOWED:
        raise PermissionError(f"refused: {method} {path} is not on the MCP allowlist")
    return key


class ScoringLimits:
    """Two host-wide scoring slots (non-blocking flock, polled up to SLOT_WAIT_S) and a per-principal minute budget."""

    def __init__(self, directory: Path, slots: int = SLOTS, wait_s: float = SLOT_WAIT_S) -> None:
        self.dir, self.slots, self.wait_s = directory, slots, wait_s

    @contextlib.contextmanager
    def slot(self) -> Iterator[int]:
        self.dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.wait_s
        while True:
            for i in range(self.slots):
                fh = (self.dir / f"slot-{i}.lock").open("a")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    fh.close()
                    continue
                try:
                    yield i
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                    fh.close()
                return
            if time.monotonic() >= deadline:
                raise ToolProblem("RATE_LIMITED", "both scoring slots are busy", "retry in a few seconds")
            time.sleep(0.05)

    def spend(self, principal: str, drafts: int, limit: int = DRAFTS_PER_MINUTE) -> None:
        """Count drafts against the principal's budget for the current minute; refuse above it."""
        self.dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", principal)[:60] or "anonymous"
        path = self.dir / f"budget-{safe}.json"
        with path.open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                state = _read_json(fh)
                minute = int(time.time() // 60)
                used = state.get("used", 0) if state.get("minute") == minute else 0
                if used + drafts > limit:
                    raise ToolProblem(
                        "RATE_LIMITED", f"{used} of {limit} drafts scored this minute", "wait a minute or send fewer"
                    )
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps({"minute": minute, "used": used + drafts}))
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _read_json(fh: IO[str]) -> dict[str, Any]:
    fh.seek(0)
    raw = fh.read()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class HttpIntelApi:
    def __init__(
        self,
        base_url: str,
        limits: ScoringLimits,
        transport: httpx.BaseTransport | None = None,
        attempt_timeout_s: float = ATTEMPT_TIMEOUT_S,
        retry_after_s: float = RETRY_AFTER_S,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.limits = limits
        self.retry_after_s = retry_after_s
        self.client = httpx.Client(timeout=attempt_timeout_s, transport=transport, follow_redirects=False)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def score_batch(self, drafts: list[dict[str, Any]], principal: str) -> dict[str, Any]:
        self.limits.spend(principal, len(drafts))
        with self.limits.slot():
            out = self._request("POST", "/v1/score/batch", json_body={"drafts": drafts})
        if not isinstance(out, dict):
            raise ToolProblem("UPSTREAM_UNAVAILABLE", "intel-api returned an unexpected scoring answer")
        return out

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, json_body: Any = None) -> Any:
        route_template(method, path)  # raises before any I/O
        headers = {"Accept": "application/json"}
        if rid := REQUEST_ID.get():
            headers["X-Request-ID"] = rid
        last: str = ""
        for attempt in (1, 2):
            try:
                r = self.client.request(method, self.base + path, params=params, json=json_body, headers=headers)
            except httpx.HTTPError as e:
                last = type(e).__name__
            else:
                if r.status_code == 404:
                    detail = _detail(r)
                    if detail == "not_active":
                        raise ToolProblem(
                            "MODEL_NOT_ACTIVE", f"{path.rsplit('/', 1)[-1]} isn't approved and active yet"
                        )
                    raise ToolProblem(
                        "UPSTREAM_UNAVAILABLE", f"{path} is not deployed on intel-api yet ({detail})", "retry later"
                    )
                if r.status_code < 500:
                    if r.status_code >= 400:
                        raise ToolProblem("INVALID_ARGUMENT", f"intel-api refused the request: {_detail(r)}")
                    return r.json()
                last = f"HTTP {r.status_code}"
            if attempt == 1:
                time.sleep(self.retry_after_s)
        raise ToolProblem("UPSTREAM_UNAVAILABLE", f"intel-api unreachable ({last})", "retry in a minute")


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:120]
    return str(body.get("detail", body))[:200] if isinstance(body, dict) else str(body)[:200]
