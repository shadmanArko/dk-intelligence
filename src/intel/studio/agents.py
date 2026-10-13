"""One AI judgment = one headless Claude Code process (the RunnerPort the studio ships with).

Everything the lab measured is kept (playbook A2-A5): the agent's own prompt only (`--system-prompt`), an empty
working folder (no CLAUDE.md, no memory), no built-in tools (`--tools ""`), MCP tools loaded up front
(`ENABLE_TOOL_SEARCH=false`), exactly its own tools allowed and the rest of its servers' tools hidden. New here:
`--max-turns` from budgets.yaml, and an AgentResult that carries the token counts for the budget.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from intel.studio.ports import AgentResult, ToolPort
from intel.studio.runlog import RunLog, short

AGENTS_DIR = Path(__file__).resolve().parent / "agents"


def agent_spec(name: str, directory: Path = AGENTS_DIR) -> dict[str, Any]:
    """Front matter (tools, model) and body (the system prompt) of `agents/<name>.md`."""
    text = (directory / f"{name}.md").read_text()
    _, front, body = text.split("---\n", 2)
    meta: dict[str, str] = {}
    for line in front.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
    return {"name": name, "tools": tools, "model": meta.get("model", "sonnet"), "prompt": body.strip()}


def servers_for(tools: list[str]) -> list[str]:
    """`mcp__<server>__<tool>` → the servers this agent needs (and nothing else)."""
    return sorted({t.split("__")[1] for t in tools if t.startswith("mcp__")})


def json_from(text: str) -> dict[str, Any]:
    """The agent's answer: the first {...} block in its final message."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    data: dict[str, Any] = json.loads(m.group(0))
    return data


class ClaudeRunner:
    def __init__(self, log: RunLog, tools: ToolPort, servers: dict[str, dict[str, Any]], max_turns: dict[str, int],
                 timeout_s: int, claude_bin: str | None = None, directory: Path = AGENTS_DIR) -> None:  # fmt: skip
        self.log, self.tools, self.servers = log, tools, servers
        self.max_turns, self.timeout_s, self.directory = max_turns, timeout_s, directory
        self.claude = claude_bin or os.environ.get("CLAUDE_BIN", "claude")

    def _hidden(self, allowed: list[str]) -> list[str]:
        hidden: list[str] = []
        for s in servers_for(allowed):
            hidden += [f"mcp__{s}__{t}" for t in self.tools.offered(s) if f"mcp__{s}__{t}" not in allowed]
        return hidden

    def run(self, node: str, agent: str, task: str) -> AgentResult:
        spec = agent_spec(agent, self.directory)
        cfg = self.log.dir / f"{node.replace(':', '_')}.mcp.json"
        cfg.write_text(
            json.dumps({"mcpServers": {s: {"type": "stdio", **self.servers[s]} for s in servers_for(spec["tools"])}})
        )
        hidden = self._hidden(spec["tools"])
        cmd = [
            self.claude, "-p", task, "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(cfg), "--strict-mcp-config", "--tools", "",
            "--system-prompt", spec["prompt"], "--no-session-persistence", "--model", spec["model"],
            "--max-turns", str(self.max_turns.get(agent, 4)),
        ]  # fmt: skip
        if spec["tools"]:
            cmd += ["--allowedTools", ",".join(spec["tools"])]
        if hidden:
            cmd += ["--disallowedTools", ",".join(hidden)]
        self.log.emit(node, "start", agent=agent, model=spec["model"], tools=spec["tools"], hidden=len(hidden),
                      task=short(task, 600))  # fmt: skip
        work = Path(tempfile.mkdtemp(prefix="dk-agent-"))  # outside the repo: no CLAUDE.md, no memory, nothing to read
        started = time.monotonic()
        calls: dict[str, tuple[str, float]] = {}
        names: list[str] = []
        final: dict[str, Any] = {}
        last_text = ""
        try:
            env = {**os.environ, "ENABLE_TOOL_SEARCH": "false"}  # else MCP tools hide behind the removed ToolSearch
            proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") == "assistant":
                    for c in e["message"].get("content", []):
                        if c.get("type") == "tool_use":
                            calls[c["id"]] = (c["name"], time.monotonic())
                            names.append(c["name"].split("__")[-1])
                            server = c["name"].split("__")[1] if c["name"].startswith("mcp__") else "built-in"
                            self.log.emit(node, "tool_call", tool=c["name"].split("__")[-1], server=server,
                                          input=short(c.get("input")))  # fmt: skip
                        elif c.get("type") == "text" and c.get("text", "").strip():
                            last_text = c["text"]
                            self.log.emit(node, "thought", text=short(c["text"], 400))
                elif e.get("type") == "user":
                    for c in e["message"].get("content", []):
                        if c.get("type") == "tool_result":
                            name, t_call = calls.get(c.get("tool_use_id", ""), ("?", time.monotonic()))
                            self.log.emit(node, "tool_result", tool=name.split("__")[-1],
                                          is_error=bool(c.get("is_error")),
                                          took_ms=round((time.monotonic() - t_call) * 1000),
                                          output=short(c.get("content")))  # fmt: skip
                elif e.get("type") == "result":
                    final = e
            proc.wait(timeout=self.timeout_s)
            if proc.returncode != 0 or final.get("is_error"):
                stderr = proc.stderr.read() if proc.stderr else ""
                raise RuntimeError(final.get("result") or stderr[-300:] or f"exit {proc.returncode}")
            answer = json_from(final.get("result") or last_text)
        except Exception as ex:  # noqa: BLE001 - one failed agent is reported; the run decides what to do
            seconds = time.monotonic() - started
            usage = final.get("usage") or {}  # a failed call may still have spent tokens
            tin, tout = _tokens(usage)
            self.log.emit(node, "end", status="failed", error=short(str(ex), 300), ms_total=round(seconds * 1000),
                          tokens_in=tin, tokens_out=tout)  # fmt: skip
            return AgentResult(None, tin, tout, int(final.get("num_turns") or 0), seconds, short(str(ex), 300), names)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        seconds = time.monotonic() - started
        tin, tout = _tokens(final.get("usage") or {})
        self.log.emit(node, "end", status="done", ms_total=round(seconds * 1000), tokens_in=tin, tokens_out=tout,
                      tokens_cached=int((final.get("usage") or {}).get("cache_read_input_tokens") or 0),
                      turns=final.get("num_turns"), output=answer)  # fmt: skip
        return AgentResult(answer, tin, tout, int(final.get("num_turns") or 0), seconds, "", names)


def _tokens(usage: dict[str, Any]) -> tuple[int, int]:
    tin = sum(
        int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    return tin, int(usage.get("output_tokens") or 0)
