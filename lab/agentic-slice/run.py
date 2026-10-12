"""The engine: the orchestrator as plain code (lab).

    planner  ──►  writer helper 1 ┐
                  writer helper 2 ├─ in parallel ──►  writer-lead  ──►  report
                  writer helper 3 ┘

Each agent is one headless Claude Code process (`claude -p … --output-format stream-json`) that gets ONLY its own
tools: its MCP servers come from a per-agent config, `--tools ""` removes every built-in tool (no shell, no web, no
file writes), and `--allowedTools` lists exactly the MCP tools in its agent file. The agent files in
`.claude/agents/` are the single source for prompts, tools and model, shared with the interactive `/plan-week`.

Every event (agent start and end, each tool call and result, tokens) is appended to `runs/<run_id>.jsonl`; the
console (`console.py`) draws the run from that file while it happens. One run at a time (`runs/.running`).

    uv run python lab/agentic-slice/run.py --platform instagram --trigger manual
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

LAB = Path(__file__).resolve().parent
REPO = LAB.parents[1]
RUNS = LAB / "runs"
AGENTS = LAB / ".claude" / "agents"
LOCK = RUNS / ".running"
CLAUDE = os.environ.get("CLAUDE_BIN", "claude")
BERLIN = ZoneInfo("Europe/Berlin")
HELPERS = 3
AGENT_TIMEOUT_S = 600

# the MCP servers an agent may be given (the same three as .mcp.json, for interactive use)
SERVERS: dict[str, dict[str, Any]] = {
    "dk_intel_planner": {"command": "uv", "args": ["run", "--quiet", "--directory", str(REPO), "intel", "mcp", "serve",
                                                   "--profile", "planner"]},
    "dk_intel_writer": {"command": "uv", "args": ["run", "--quiet", "--directory", str(REPO), "intel", "mcp", "serve",
                                                  "--profile", "writer"]},
    "demo_publisher": {"command": "uv", "args": ["run", "--quiet", "--directory", str(REPO), "python",
                                                 str(LAB / "demo_publisher" / "server.py")]},
}  # fmt: skip


# ---------- agent files ----------


def agent_spec(name: str) -> dict[str, Any]:
    """Front matter (tools, model) and body (the system prompt) of `.claude/agents/<name>.md`."""
    text = (AGENTS / f"{name}.md").read_text()
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


# ---------- the run log ----------


class Run:
    def __init__(self, platform: str, trigger: str) -> None:
        self.id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        self.platform, self.trigger = platform, trigger
        self.dir = RUNS / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = RUNS / f"{self.id}.jsonl"
        self._lock = threading.Lock()
        self.t0 = time.monotonic()

    def emit(self, node: str, kind: str, **data: Any) -> None:
        event = {"t": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
                 "ms": round((time.monotonic() - self.t0) * 1000), "node": node, "kind": kind, **data}  # fmt: skip
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _short(value: Any, n: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= n else text[: n - 1] + "…"


def _json_from(text: str) -> dict[str, Any]:
    """The agent's answer: the first {...} block in its final message."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    data: dict[str, Any] = json.loads(m.group(0))
    return data


# ---------- one agent = one headless Claude Code process ----------


def call_agent(run: Run, node: str, agent: str, task: str) -> dict[str, Any] | None:
    spec = agent_spec(agent)
    cfg = run.dir / f"{node}.mcp.json"
    servers = {s: {"type": "stdio", **SERVERS[s]} for s in servers_for(spec["tools"])}
    cfg.write_text(json.dumps({"mcpServers": servers}))
    cmd = [
        CLAUDE, "-p", task, "--output-format", "stream-json", "--verbose",
        "--mcp-config", str(cfg), "--strict-mcp-config",
        "--allowedTools", ",".join(spec["tools"]), "--tools", "",
        "--append-system-prompt", spec["prompt"], "--no-session-persistence", "--model", spec["model"],
    ]  # fmt: skip
    run.emit(node, "start", agent=agent, model=spec["model"], tools=spec["tools"], task=_short(task, 600))
    started = time.monotonic()
    calls: dict[str, tuple[str, float]] = {}
    final: dict[str, Any] = {}
    last_text = ""
    try:
        proc = subprocess.Popen(cmd, cwd=LAB, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
                        server = c["name"].split("__")[1] if c["name"].startswith("mcp__") else "built-in"
                        run.emit(node, "tool_call", tool=c["name"].split("__")[-1], server=server,
                                 input=_short(c.get("input")))  # fmt: skip
                    elif c.get("type") == "text" and c.get("text", "").strip():
                        last_text = c["text"]
                        run.emit(node, "thought", text=_short(c["text"], 400))
            elif e.get("type") == "user":
                for c in e["message"].get("content", []):
                    if c.get("type") == "tool_result":
                        name, t_call = calls.get(c.get("tool_use_id", ""), ("?", time.monotonic()))
                        run.emit(
                            node, "tool_result", tool=name.split("__")[-1], is_error=bool(c.get("is_error")),
                            ms=round((time.monotonic() - t_call) * 1000), output=_short(c.get("content")),
                        )  # fmt: skip
            elif e.get("type") == "result":
                final = e
        proc.wait(timeout=AGENT_TIMEOUT_S)
        if proc.returncode != 0 or final.get("is_error"):
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(final.get("result") or stderr[-300:] or f"exit {proc.returncode}")
        answer = _json_from(final.get("result") or last_text)
    except Exception as ex:  # noqa: BLE001 - one failed agent is reported, the run decides what to do
        run.emit(node, "end", status="failed", error=_short(str(ex), 300),
                 ms_total=round((time.monotonic() - started) * 1000))  # fmt: skip
        return None
    usage = final.get("usage") or {}
    run.emit(
        node, "end", status="done", ms_total=round((time.monotonic() - started) * 1000),
        tokens_in=sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                       "cache_creation_input_tokens")),
        tokens_out=int(usage.get("output_tokens") or 0), turns=final.get("num_turns"), output=answer,
    )  # fmt: skip
    return answer


# ---------- the workflow ----------


def _dates_next_week() -> dict[str, str]:
    """Mon..Sun → the next occurrence (from tomorrow), as YYYY-MM-DD (Berlin)."""
    today = dt.datetime.now(BERLIN).date()
    out = {}
    for i in range(1, 8):
        d = today + dt.timedelta(days=i)
        out[d.strftime("%a")] = d.isoformat()
    return out


def orchestrate(platform: str, trigger: str) -> int:
    RUNS.mkdir(exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print("a run is already in progress", file=sys.stderr)
        return 2
    run = Run(platform, trigger)
    os.write(fd, run.id.encode())
    os.close(fd)
    try:
        run.emit("orchestrator", "start", platform=platform, trigger=trigger,
                 plan="planner → 3 writer helpers in parallel → writer-lead")  # fmt: skip
        plan = call_agent(run, "planner", "planner", f"Plan 3 posts for {platform}.")
        if not plan or not plan.get("slots"):
            run.emit("orchestrator", "end", status="failed", error="the planner returned no plan")
            return 1
        dates = _dates_next_week()
        slots = plan["slots"][:HELPERS]
        run.emit("orchestrator", "delegate", to=[f"helper-{i + 1}" for i in range(len(slots))],
                 note=f"{len(slots)} writer helpers start at the same time")  # fmt: skip

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=HELPERS) as pool:
            futures = {
                pool.submit(
                    call_agent, run, f"helper-{i + 1}", "writer",
                    json.dumps({"platform": platform, "date": dates.get(str(s.get("day")), ""), **s},
                               ensure_ascii=False),
                ): i
                for i, s in enumerate(slots)
            }  # fmt: skip
            for f in as_completed(futures):
                if (r := f.result()) is not None:
                    results.append(r)
        run.emit("orchestrator", "gathered", ok=len(results), failed=len(slots) - len(results))

        lead = call_agent(run, "writer-lead", "writer-lead",
                          json.dumps({"platform": platform, "results": results}, ensure_ascii=False))  # fmt: skip
        proposed = (lead or {}).get("proposed", [])
        skipped = (lead or {}).get("skipped", [])
        status = "done" if lead is not None else "failed"
        run.emit("orchestrator", "end", status=status, proposed=len(proposed), skipped=len(skipped),
                 helpers_failed=len(slots) - len(results))  # fmt: skip
        report = [f"# Run {run.id} ({trigger}, {platform})", "", f"Proposed: {len(proposed)} · skipped: {len(skipped)}"]
        report += [f"- {p.get('slot_id')}: proposal {p.get('proposal_id')}" for p in proposed]
        report += [f"- {s.get('slot_id')}: skipped ({s.get('why')})" for s in skipped]
        (RUNS / f"{run.id}.md").write_text("\n".join(report) + "\n")
        return 0 if status == "done" else 1
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="instagram", choices=["instagram", "facebook"])
    ap.add_argument("--trigger", default="manual", choices=["manual", "button", "schedule"])
    a = ap.parse_args()
    sys.exit(orchestrate(a.platform, a.trigger))
