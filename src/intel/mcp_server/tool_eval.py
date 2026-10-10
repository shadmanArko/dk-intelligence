"""The tool-use eval (architecture §7.6): can a free local model pick and call our tools from their descriptions?

Mac only, €0: a local Ollama model (default gpt-oss:20b) drives the real `dk_intel` gateway in memory through
Ollama's native tool calling. No LLM ever runs inside the server; this is a client-side measurement of how usable
our tool contract is. Per task: success (every expected tool, or an accepted alternative, was called with valid
arguments and the expected argument values, and a final answer came back), tool calls, tokens (prompt + output,
as Ollama counts them) and tool errors. `python -m intel.mcp_server.tool_eval [--split dev] [--model gpt-oss:20b]`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[3]
TASKS = ROOT / "evals" / "tools" / "tasks.jsonl"
RESULTS = ROOT / "data" / "evals" / "tools"
OLLAMA = "http://127.0.0.1:11434/api/chat"
MAX_CALLS = 8
RESULT_CHARS = 6000  # what one tool result may add to the model's context


def tasks(split: str | None) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in TASKS.read_text().splitlines() if line.strip()]
    return [t for t in rows if split is None or t["split"] == split]


def _same(got: Any, want: Any) -> bool:
    """Case-insensitive, as the server's NormaliseArgs accepts "TikTok" for "tiktok"."""
    return str(got).strip().casefold() == str(want).strip().casefold()


def judge(task: dict[str, Any], calls: list[dict[str, Any]], answer: str) -> bool:
    """Every expected group has a called tool; expected argument values were used at least once; an answer came."""
    called = {c["name"] for c in calls if not c["error"]}
    if not all(any(name in called for name in group) for group in task["expect"]):
        return False
    for tool, want in (task.get("args") or {}).items():
        if tool in called and not any(
            c["name"] == tool and all(_same(c["args"].get(k), v) for k, v in want.items()) for c in calls
        ):
            return False
    return bool(answer.strip())


async def run_task(
    client: Any, tools: list[dict[str, Any]], system: str, task: dict[str, Any], model: str
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": task["task"]}]
    calls: list[dict[str, Any]] = []
    tokens = 0
    answer = ""
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=600) as http:
        for _ in range(MAX_CALLS + 1):
            body = {"model": model, "messages": messages, "tools": tools, "stream": False,
                    "options": {"num_ctx": 32768, "temperature": 0}}  # fmt: skip
            r = (await http.post(OLLAMA, json=body)).json()
            tokens += int(r.get("prompt_eval_count") or 0) + int(r.get("eval_count") or 0)
            msg = r.get("message") or {}
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            todo = msg.get("tool_calls") or []
            if not todo or len(calls) >= MAX_CALLS:
                answer = str(msg.get("content") or "")
                break
            for tc in todo:
                fn = tc.get("function") or {}
                name, args = str(fn.get("name")), fn.get("arguments") or {}
                res = await client.call_tool(name, args, raise_on_error=False)
                text = "".join(getattr(c, "text", "") for c in res.content)
                calls.append({"name": name, "args": args, "error": bool(res.is_error)})
                messages.append({"role": "tool", "tool_name": name, "content": text[:RESULT_CHARS]})
    return {
        "id": task["id"],
        "split": task["split"],
        "success": judge(task, calls, answer),
        "calls": len(calls),
        "tool_errors": sum(c["error"] for c in calls),
        "tokens": tokens,
        "seconds": round(time.perf_counter() - t0, 1),
        "tools_called": [c["name"] for c in calls],
        "args": [c["args"] for c in calls],
        "call_errors": [c["error"] for c in calls],
        "answer": answer[:400],
    }


async def main(split: str | None, model: str, profile: str) -> dict[str, Any]:
    from fastmcp import Client

    from intel.mcp_server.deps import Deps
    from intel.mcp_server.gateway import build_gateway, instructions
    from intel.mcp_server.kit.middleware import stdio_principal
    from intel.settings import McpSettings

    gw = build_gateway(profile, Deps.from_settings(McpSettings(), f"{stdio_principal()}:tool-eval"))
    results = []
    async with Client(gw) as client:
        listed = await client.list_tools()
        tools = [
            {"type": "function",
             "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema}}
            for t in listed
        ]  # fmt: skip
        for task in tasks(split):
            r = await run_task(client, tools, instructions(), task, model)
            print(f"{r['id']} {'OK ' if r['success'] else 'MISS'} calls={r['calls']} tokens={r['tokens']} "
                  f"{r['seconds']}s {r['tools_called']}", flush=True)  # fmt: skip
            results.append(r)
    summary: dict[str, Any] = {"model": model, "profile": profile, "tools": len(listed)}
    for s in sorted({r["split"] for r in results}):
        rs = [r for r in results if r["split"] == s]
        summary[s] = {
            "tasks": len(rs),
            "success": round(sum(r["success"] for r in rs) / len(rs), 3),
            "calls_per_task": round(sum(r["calls"] for r in rs) / len(rs), 2),
            "tokens_per_task": round(sum(r["tokens"] for r in rs) / len(rs)),
            "tool_errors": sum(r["tool_errors"] for r in rs),
        }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"tool-eval-{model.replace(':', '_')}-{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default=None)
    ap.add_argument("--model", default="gpt-oss:20b")
    ap.add_argument("--profile", default="owner")
    a = ap.parse_args()
    asyncio.run(main(a.split, a.model, a.profile))
