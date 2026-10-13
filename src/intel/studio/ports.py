"""The studio's seams (ADR 0034). The orchestrator talks to these four protocols and nothing else, so the way an agent
is run, a render is made, a tool is called or a post is handed over can change without touching the plan.

    ToolPort       code calling MCP servers directly (dk_intel, dk_reels): 0 tokens
    RunnerPort     one AI judgment: today a headless `claude -p`; later the Agent SDK or a local model (N9, N14)
    RendererPort   a spec in, a manifest out: today the reels CLI on this Mac; later a worker
    PublisherPort  the publisher's HTTP API (handover.PublishClient is the real one)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ToolPort(Protocol):
    def call(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        """The tool's JSON answer, or {"error": "..."} when the tool refused. Raises when the server is unreachable."""

    def offered(self, server: str) -> list[str]:
        """Every tool the server offers (an agent's hidden list is this minus what it may use)."""


@dataclass
class AgentResult:
    answer: dict[str, Any] | None  # None: the agent failed or answered without JSON
    tokens_in: int = 0
    tokens_out: int = 0
    turns: int = 0
    seconds: float = 0.0
    error: str = ""
    tool_calls: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


class RunnerPort(Protocol):
    def run(self, node: str, agent: str, task: str) -> AgentResult: ...


class RendererPort(Protocol):
    def dry_run(self, spec_path: Path) -> tuple[int, dict[str, Any]]:
        """Validate and plan without rendering. (exit code, summary): 0 ok · 2 invalid spec · 3 error."""

    def render(self, spec_path: Path) -> tuple[int, dict[str, Any]]:
        """Render. (exit code, summary): 0 ok · 2 invalid · 3 render error · 4 quality checks failed."""

    def thumbnail(self, manifest: dict[str, Any], folder: Path, out: Path) -> bool: ...


class PublisherPort(Protocol):
    def spec(self, cache: Path | None = None, fresh: bool = False) -> dict[str, Any]: ...
    def upload(self, path: Path) -> str: ...
    def batch(self, posts: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]: ...
    def post(self, post_key: str) -> dict[str, Any] | None: ...
