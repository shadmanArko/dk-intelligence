"""The engine: the orchestrator as plain code (lab).

    code: fetch ──► planner ──► writer helper 1 ┐
    (slots, pack,   (AI, 1 turn)  writer helper 2 ├─ in parallel ──► code: re-check + propose ──► report
     examples)                  writer helper 3 ┘

AI only where judgment is needed (choosing the angles, writing captions); plain code for everything fixed: fetching
the slots and the grounding pack, re-checking the captions and filing the proposals. Code calls the same MCP servers
an agent would (dk_intel's audit records both), so the boundaries don't change, but those steps cost no tokens, take
no turns and can't be skipped or misreported.

Each agent is one headless Claude Code process (`claude -p … --output-format stream-json`) that gets ONLY its own
tools: its MCP servers come from a per-agent config, `--tools ""` removes every built-in tool (no shell, no web, no
file writes), `--allowedTools` lists exactly the MCP tools in its agent file and `--disallowedTools` hides the rest of
those servers' tools. The agent files in `.claude/agents/` hold the prompts, tools and model.

Token budget (measured 2026-10-12; every turn re-sends the whole context, so the fixed part matters most):
- `--system-prompt` = the agent's own prompt only, instead of Claude Code's coding prompt (~9k tokens per turn);
- the process starts in an empty folder outside the repo, so no CLAUDE.md or memory is loaded (~6k);
- a tool the agent may not call isn't described to it at all (~3k);
- the planner gets its data in the task (one turn, no tools); each writer gets only the evidence behind its own angle
  and two of our best checked posts as examples, so the context is small but has what the job needs.

Every event is appended to `runs/<run_id>.jsonl`; the console (`console.py`) draws the run from that file while it
happens. One run at a time (`runs/.running`). `--inputs FILE` replays frozen inputs (for `eval.py`), and
`--save-inputs FILE` freezes this run's.

    uv run python lab/agentic-slice/run.py --platform instagram --trigger manual
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


_OFFERED: dict[str, list[str]] = {}  # server → every tool it offers (from tools/list), filled by mcp_session


def mcp_session(server: str, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    """Code calling an MCP server directly (no model): list its tools, then make `calls` in order on one connection.
    Each result is the tool's JSON, or {"error": "..."} if the tool refused."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    async def go() -> list[Any]:
        async with Client(StdioTransport(SERVERS[server]["command"], SERVERS[server]["args"])) as c:
            _OFFERED[server] = [t.name for t in await c.list_tools()]
            out: list[Any] = []
            for tool, args in calls:
                r = await c.call_tool(tool, args, raise_on_error=False)
                text = "".join(getattr(b, "text", "") for b in r.content)
                if r.is_error:
                    out.append({"error": text[:300]})
                    continue
                try:
                    out.append(json.loads(text))
                except json.JSONDecodeError:
                    out.append({"text": text})
            return out

    return asyncio.run(go())


def code_calls(run: Run, node: str, server: str, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    """`mcp_session`, with each call in the run log like an agent's (the console shows code and AI the same way)."""
    for tool, args in calls:
        run.emit(node, "tool_call", tool=tool, server=server, input=_short(args), by="code")
    t0 = time.monotonic()
    results = mcp_session(server, calls)
    took = round((time.monotonic() - t0) * 1000)
    for (tool, _), r in zip(calls, results, strict=True):
        run.emit(node, "tool_result", tool=tool, is_error="error" in r, took_ms=took, output=_short(r))
    return results


def hidden_tools(allowed: list[str]) -> list[str]:
    """The tools of this agent's servers that it may not call: hidden, so their descriptions cost no tokens."""
    hidden = []
    for s in servers_for(allowed):
        if s not in _OFFERED:
            mcp_session(s, [])
        hidden += [f"mcp__{s}__{t}" for t in _OFFERED[s] if f"mcp__{s}__{t}" not in allowed]
    return hidden


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
        if {"t", "ms", "node", "kind"} & data.keys():
            raise ValueError("event data must not overwrite the timeline fields")
        with self._lock, self.path.open("a") as f:  # stamped under the lock: the file is in time order
            event = {"t": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
                     "ms": round((time.monotonic() - self.t0) * 1000), "node": node, "kind": kind, **data}  # fmt: skip
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
    hidden = hidden_tools(spec["tools"])
    cmd = [
        CLAUDE, "-p", task, "--output-format", "stream-json", "--verbose",
        "--mcp-config", str(cfg), "--strict-mcp-config", "--tools", "",
        "--system-prompt", spec["prompt"], "--no-session-persistence", "--model", spec["model"],
    ]  # fmt: skip
    if spec["tools"]:
        cmd += ["--allowedTools", ",".join(spec["tools"])]
    if hidden:
        cmd += ["--disallowedTools", ",".join(hidden)]
    run.emit(node, "start", agent=agent, model=spec["model"], tools=spec["tools"], hidden=len(hidden),
             task=_short(task, 600))  # fmt: skip
    work = Path(tempfile.mkdtemp(prefix="dk-agent-"))  # outside the repo: no CLAUDE.md, no memory, nothing to read
    started = time.monotonic()
    calls: dict[str, tuple[str, float]] = {}
    final: dict[str, Any] = {}
    last_text = ""
    try:
        # MCP tools load up front: with tool search on they are deferred behind ToolSearch, a built-in that
        # `--tools ""` removes, and the agent would see no tools at all (found on the first real run)
        env = {**os.environ, "ENABLE_TOOL_SEARCH": "false"}
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
                            took_ms=round((time.monotonic() - t_call) * 1000), output=_short(c.get("content")),
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
    finally:
        shutil.rmtree(work, ignore_errors=True)
    usage = final.get("usage") or {}
    run.emit(
        node, "end", status="done", ms_total=round((time.monotonic() - started) * 1000),
        tokens_in=sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                       "cache_creation_input_tokens")),
        tokens_cached=int(usage.get("cache_read_input_tokens") or 0),
        tokens_out=int(usage.get("output_tokens") or 0), turns=final.get("num_turns"), output=answer,
    )  # fmt: skip
    return answer


# ---------- the workflow ----------

CONTENT_TYPES = ("image", "video", "reel", "carousel", "short")
EXAMPLES = 2


def _dates_next_week() -> dict[str, str]:
    """Mon..Sun → the next occurrence (from tomorrow), as YYYY-MM-DD (Berlin)."""
    today = dt.datetime.now(BERLIN).date()
    out = {}
    for i in range(1, 8):
        d = today + dt.timedelta(days=i)
        out[d.strftime("%a")] = d.isoformat()
    return out


def _cites(pack: Any) -> dict[str, str]:
    """Every citation id in the grounding pack → its text (review quotes, our posts)."""
    out: dict[str, str] = {}

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if isinstance(x.get("cite"), str) and isinstance(x.get("text"), str):
                out[x["cite"]] = x["text"]
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(pack)
    return out


def _example_candidates(pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Our own posts in the pack, best first (by how they did against our usual post)."""
    posts: dict[str, dict[str, Any]] = {}
    for p in pack.get("our_best_posts", []):
        posts[p["cite"]] = {"cite": p["cite"], "text": p["text"], "x": (p.get("perf") or {}).get("vs_our_median", 0)}
    for o in pack.get("opportunities", []):
        for p in o.get("our_posts", []):
            posts.setdefault(p["cite"], {"cite": p["cite"], "text": p["text"], "x": p.get("vs_our_median", 0)})
    return sorted(posts.values(), key=lambda p: -float(p["x"] or 0))


def _brief(platform: str, slot: dict[str, Any], dates: dict[str, str], cites: dict[str, str],
           examples: list[dict[str, Any]], reminders: list[str]) -> dict[str, Any]:  # fmt: skip
    """One writer's task, with everything code can work out already filled in (no turns spent on it)."""
    date, posted_at = _when(slot.get("day"), slot.get("time"), dates)
    evidence = [{"cite": c, "text": cites[c]} for c in slot.get("sources", []) if c in cites]
    return {"platform": platform, "date": date, "planned_posted_at": posted_at,
            "content_type": _ctype(slot.get("format")), **slot, "evidence": evidence,
            "examples": [{"cite": e["cite"], "text": e["text"], "vs_our_median": e["x"],
                          "format_ok": e.get("format_ok", True)} for e in examples],
            "brand": reminders}  # fmt: skip


def fetch_inputs(run: Run, platform: str) -> dict[str, Any]:
    """Code, not AI: the open slots (product #2) and the grounding pack (dk_intel), at the same time."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        slots_f = pool.submit(
            code_calls, run, "orchestrator", "demo_publisher", [("list_slots", {"platform": platform})]
        )
        pack_f = pool.submit(code_calls, run, "orchestrator", "dk_intel_planner",
                             [("grounding_pack", {"platform": platform, "days": 7})])  # fmt: skip
        (slots,), (pack,) = slots_f.result(), pack_f.result()
    for name, value in (("list_slots", slots), ("grounding_pack", pack)):
        if "error" in value:
            raise RuntimeError(f"{name}: {value['error']}")
    pack.pop("prov", None)
    return {"slots": slots.get("slots", []), "pack": pack}


# A caption of the usual shape (3 lines, 3 hashtags). The content scorer reads a caption's shape, not its words
# (measured 2026-10-12: different captions for one slot scored the same), so a slot that fails with this probe
# fails with any caption: no writer is spent on it.
PROBE = "Kacchi aus Berlin, frisch aus dem Topf.\nSlow-cooked kacchi biryani from Berlin.\n" \
        "#kacchibiryani #berlinfood #biryaniberlin"  # fmt: skip


def _when(day: Any, time_: Any, dates: dict[str, str]) -> tuple[str, str]:
    """(date, planned_posted_at with Berlin's offset) for a weekday and HH:MM next week."""
    date = dates.get(str(day), "")
    if not date:
        return "", ""
    hh, mm = (str(time_ or "17:00") + ":00").split(":")[:2]
    return date, dt.datetime.fromisoformat(date).replace(hour=int(hh), minute=int(mm), tzinfo=BERLIN).isoformat()


def _ctype(fmt: Any) -> str:
    return str(fmt) if str(fmt) in CONTENT_TYPES else "image"


Rows = list[dict[str, Any]]


def screen(run: Run, platform: str, pack: dict[str, Any], slots: Rows,
           dates: dict[str, str]) -> tuple[Rows, Rows, Rows]:  # fmt: skip
    """Code, one dk_intel call, before any AI: (checked examples, open slots that can pass, slots that can't)."""
    cands = _example_candidates(pack)[:6]
    drafts = [{"ref": "x" + c["cite"][:23], "platform": platform, "caption": c["text"]} for c in cands]
    for s in slots:
        drafts.append({"ref": "s" + str(s["id"])[:23], "platform": platform, "caption": PROBE,
                       "content_type": _ctype(s.get("format")),
                       "planned_posted_at": _when(s.get("day"), s.get("time"), dates)[1] or None})  # fmt: skip
    (res,) = code_calls(run, "orchestrator", "dk_intel_writer", [("evaluate_drafts", {"drafts": drafts[:20]})])
    if "error" in res:
        raise RuntimeError(res["error"])
    got = {r.get("ref"): r for r in res.get("results", [])}
    examples = []
    for c in cands:
        r = got.get("x" + c["cite"][:23], {})
        if r.get("verdict") in ("blocked", None):
            continue  # never shown, whatever its engagement
        examples.append({**c, "format_ok": not r.get("errors") and not r.get("warnings")})
    examples.sort(key=lambda c: not c["format_ok"])  # rule-following examples first, then by engagement
    open_, dropped = [], []
    for s in slots:
        r = got.get("s" + str(s["id"])[:23], {})
        prob = ((r.get("scores") or {}).get("content_scorer") or {}).get("probability")
        if r.get("verdict") == "rewrite" and not r.get("errors") and not r.get("warnings"):
            dropped.append({"slot_id": s["id"], "day": s.get("day"), "time": s.get("time"), "format": s.get("format"),
                            "score": prob, "why": f"this slot scores {round((prob or 0) * 100)}% with any caption, "
                            "below the bar: move it to another day, time or format"})  # fmt: skip
        else:
            open_.append({**s, "slot_score": prob})
    return examples[:EXAMPLES], open_, dropped


def check_and_propose(run: Run, platform: str, results: list[dict[str, Any]],
                      briefs: dict[str, dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    """Code, not AI: re-check every "keep" caption with dk_intel (trust, but verify), propose those still "keep"."""
    node = "checker"
    run.emit(node, "start", agent="code", model="code", tools=["dk_intel_writer.evaluate_drafts",
             "demo_publisher.propose_post"], task=f"{len(results)} captions to re-check")  # fmt: skip
    t0 = time.monotonic()
    skipped = [{"slot_id": r.get("slot_id"), "why": f"the writer's best was '{r.get('verdict')}' "
                f"(score {r.get('score')})"} for r in results if r.get("verdict") != "keep"]  # fmt: skip
    keep = [r for r in results if r.get("verdict") == "keep"]
    proposed: list[dict[str, Any]] = []
    if keep:
        drafts = [{"ref": str(r["slot_id"]), "platform": platform, "caption": r["caption"],
                   "content_type": briefs[r["slot_id"]]["content_type"],
                   "planned_posted_at": briefs[r["slot_id"]]["planned_posted_at"]} for r in keep]  # fmt: skip
        (res,) = code_calls(run, node, "dk_intel_writer", [("evaluate_drafts", {"drafts": drafts})])
        verdicts = {r.get("ref"): r for r in res.get("results", [])}
        still = [r for r in keep if verdicts.get(str(r["slot_id"]), {}).get("verdict") == "keep"]
        skipped += [
            {"slot_id": r["slot_id"], "why": "the re-check did not confirm 'keep'"} for r in keep if r not in still
        ]
        if still:
            calls = [("propose_post", {"slot_id": r["slot_id"], "caption": r["caption"], "verdict": "keep",
                                       "sources": r.get("sources", [])[:10], "proposed_by": "checker"})
                     for r in still]  # fmt: skip
            for r, out in zip(still, code_calls(run, node, "demo_publisher", calls), strict=True):
                if "error" in out:
                    skipped.append({"slot_id": r["slot_id"], "why": f"the calendar refused it: {out['error'][:120]}"})
                else:
                    proposed.append({"slot_id": r["slot_id"], "proposal_id": out.get("proposal_id")})
    answer = {"proposed": proposed, "skipped": skipped}
    run.emit(node, "end", status="done", ms_total=round((time.monotonic() - t0) * 1000), tokens_in=0, tokens_out=0,
             turns=0, output=answer)  # fmt: skip
    return answer


def orchestrate(platform: str, trigger: str, inputs: Path | None = None, save_inputs: Path | None = None) -> int:
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
        run.emit("orchestrator", "start", platform=platform, trigger=trigger, inputs=str(inputs or "live"),
                 plan="fetch (code) → planner → 3 writers in parallel → re-check + propose (code)")  # fmt: skip
        try:
            data = json.loads(inputs.read_text()) if inputs else fetch_inputs(run, platform)
        except Exception as ex:  # noqa: BLE001 - no inputs, no run: end it with the reason
            run.emit("orchestrator", "end", status="failed", error=f"could not fetch the inputs: {_short(str(ex))}")
            return 1
        if save_inputs:
            save_inputs.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        pack = data["pack"]

        dates = _dates_next_week()
        try:
            examples, open_slots, dropped = screen(run, platform, pack, data["slots"], dates)
        except Exception as ex:  # noqa: BLE001 - without the screen, plan every slot and use no examples
            run.emit("orchestrator", "note", text=f"slot screen unavailable: {_short(str(ex))}")
            examples, open_slots, dropped = [], data["slots"], []
        run.emit("orchestrator", "screened", open=[s["id"] for s in open_slots], dropped=dropped,
                 examples=[e["cite"] for e in examples])  # fmt: skip
        if not open_slots:
            run.emit("orchestrator", "end", status="done", proposed=0, skipped=len(dropped), helpers_failed=0,
                     note="no open slot can pass the check: nothing to write")  # fmt: skip
            return 0

        given = [{"slot_id": s["id"], **{k: v for k, v in s.items() if k != "id"}} for s in open_slots]
        task = json.dumps({"platform": platform, "slots": given, "grounding_pack": pack}, ensure_ascii=False)
        plan = call_agent(run, "planner", "planner", task)
        if not plan or not plan.get("slots"):
            run.emit("orchestrator", "end", status="failed", error="the planner returned no plan")
            return 1
        for sl in plan["slots"]:  # an AI answer is data: normalise it in code (it once wrote `id` for `slot_id`)
            sl["slot_id"] = str(sl.get("slot_id") or sl.get("id") or "")
        known = {str(s["id"]) for s in open_slots}
        plan["slots"] = [sl for sl in plan["slots"] if sl["slot_id"] in known]
        cites = _cites(pack)
        reminders = [str(x) for x in pack.get("brand_reminders", [])]
        briefs = [_brief(platform, s, dates, cites, examples, reminders) for s in plan["slots"][:HELPERS]]
        lean = [{k: v for k, v in b.items() if k not in ("examples", "brand")} for b in briefs]
        run.emit("orchestrator", "delegate", to=[f"helper-{i + 1}" for i in range(len(briefs))],
                 note=f"{len(briefs)} writer helpers start at the same time; each gets its evidence and "
                      f"{len(examples)} checked examples",
                 slots=[{"node": f"helper-{i + 1}", **b} for i, b in enumerate(lean)])  # fmt: skip

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=HELPERS) as pool:
            futures = {
                pool.submit(call_agent, run, f"helper-{i + 1}", "writer", json.dumps(b, ensure_ascii=False)): i
                for i, b in enumerate(briefs)
            }
            for f in as_completed(futures):
                if (r := f.result()) is not None:
                    results.append(r)
        run.emit("orchestrator", "gathered", ok=len(results), failed=len(briefs) - len(results))

        by_slot = {str(b.get("slot_id")): b for b in briefs}
        for r in results:
            r["slot_id"] = str(r.get("slot_id") or "")
        unmatched = [r for r in results if r["slot_id"] not in by_slot]
        results = [r for r in results if r["slot_id"] in by_slot]
        try:
            lead = check_and_propose(run, platform, results, by_slot)
        except Exception as ex:  # noqa: BLE001 - the check failing must never propose anything
            run.emit("checker", "end", status="failed", error=_short(str(ex)))
            lead = None
        proposed = (lead or {}).get("proposed", [])
        skipped = (lead or {}).get("skipped", []) + [{"slot_id": d["slot_id"], "why": d["why"]} for d in dropped]
        skipped += [{"slot_id": r["slot_id"] or "?", "why": "a writer answered for a slot it wasn't given"}
                    for r in unmatched]  # never dropped silently  # fmt: skip
        status = "done" if lead is not None else "failed"
        run.emit("orchestrator", "end", status=status, proposed=len(proposed), skipped=len(skipped),
                 helpers_failed=len(briefs) - len(results))  # fmt: skip
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
    ap.add_argument("--trigger", default="manual", choices=["manual", "button", "schedule", "eval"])
    ap.add_argument("--inputs", type=Path, help="replay frozen inputs (slots + grounding pack) instead of fetching")
    ap.add_argument("--save-inputs", type=Path, help="freeze this run's inputs to a file")
    a = ap.parse_args()
    sys.exit(orchestrate(a.platform, a.trigger, a.inputs, a.save_inputs))
