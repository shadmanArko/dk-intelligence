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
sys.path.insert(0, str(LAB))  # handover.py and media.py sit next to this file (tests load run.py by path)
import handover  # noqa: E402
import media  # noqa: E402

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
    "dk_reels_creative": {"command": "npm", "args": ["--prefix", str(media.REELS), "run", "-s", "mcp", "--",
                                                     "--profile", "creative", "--root", str(media.REELS)]},
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


def checked_prices(run: Run, node: str) -> list[dict[str, str]]:
    """Code, not AI: the price rows a price slide may show (confirmed facts only, from dk_intel). On any failure:
    none, so price slides turn into cards: a price never ships unchecked."""
    try:
        (facts,) = code_calls(run, node, "dk_intel_writer", [("get_facts", {"keys": ["menu"]})])
    except Exception as ex:  # noqa: BLE001 - no facts, no prices
        run.emit(node, "note", text=f"no confirmed prices: {_short(str(ex))}")
        return []
    return media.price_items(facts.get("items", []) if isinstance(facts, dict) else [])


def recheck_captions(
    run: Run, node: str, platform: str, results: Rows, briefs: dict[str, dict[str, Any]]
) -> tuple[Rows, Rows]:
    """Code, one dk_intel call: re-check every "keep" caption (trust, but verify). Returns (still keep, skipped)."""
    skipped = [{"slot_id": r.get("slot_id"), "why": f"the writer's best was '{r.get('verdict')}' "
                f"(score {r.get('score')})"} for r in results if r.get("verdict") != "keep"]  # fmt: skip
    keep = [r for r in results if r.get("verdict") == "keep"]
    if not keep:
        return [], skipped
    drafts = [{"ref": str(r["slot_id"]), "platform": platform, "caption": r["caption"],
               "content_type": briefs[r["slot_id"]]["content_type"],
               "planned_posted_at": briefs[r["slot_id"]]["planned_posted_at"]} for r in keep]  # fmt: skip
    (res,) = code_calls(run, node, "dk_intel_writer", [("evaluate_drafts", {"drafts": drafts})])
    verdicts = {r.get("ref"): r for r in res.get("results", [])}
    still = [r for r in keep if verdicts.get(str(r["slot_id"]), {}).get("verdict") == "keep"]
    skipped += [{"slot_id": r["slot_id"], "why": "the re-check did not confirm 'keep'"} for r in keep if r not in still]
    return still, skipped


def check_and_propose(run: Run, platform: str, results: list[dict[str, Any]],
                      briefs: dict[str, dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    """Code, not AI: re-check every "keep" caption with dk_intel (trust, but verify), propose those still "keep"."""
    node = "checker"
    run.emit(node, "start", agent="code", model="code", tools=["dk_intel_writer.evaluate_drafts",
             "demo_publisher.propose_post"], task=f"{len(results)} captions to re-check")  # fmt: skip
    t0 = time.monotonic()
    still, skipped = recheck_captions(run, node, platform, results, briefs)
    proposed: list[dict[str, Any]] = []
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


def _problem_codes(errors: Any) -> list[str]:
    return [str(e.get("code") or e.get("rule") or e) if isinstance(e, dict) else str(e) for e in errors or []]


def check_texts(run: Run, node: str, platform: str, texts: dict[str, list[dict[str, str]]]) -> dict[str, list[str]]:
    """Code, dk_intel: every word a viewer reads on screen goes through the same brand and claims rules as a caption.
    Only blocking errors count here (a short line isn't a caption, so caption-style warnings are noise).
    Returns job_id → the problems found (empty when clean). The renderer holds no brand rules: dk_intel does."""
    drafts: list[dict[str, str]] = []
    owner: dict[str, tuple[str, str]] = {}
    for jid, items in texts.items():
        for t in items:
            ref = f"t{len(drafts)}"
            owner[ref] = (jid, t["where"])
            drafts.append({"ref": ref, "platform": platform, "caption": t["text"]})
    problems: dict[str, list[str]] = {jid: [] for jid in texts}
    for i in range(0, len(drafts), 20):
        (res,) = code_calls(run, node, "dk_intel_writer", [("evaluate_drafts", {"drafts": drafts[i : i + 20]})])
        if "error" in res:
            raise RuntimeError(res["error"])
        for r in res.get("results", []):
            codes = _problem_codes(r.get("errors"))
            if codes or r.get("verdict") == "blocked":
                jid, where = owner[r["ref"]]
                problems[jid].append(f"{where}: {', '.join(codes) or 'blocked'}")
    return problems


def make_media(run: Run, platform: str, results: list[dict[str, Any]], designs: dict[str, dict[str, Any]],
               briefs: dict[str, dict[str, Any]], publish: str,
               client: handover.PublishClient | None) -> dict[str, Any]:  # fmt: skip
    """Code, not AI. Kept captions → specs → (check) → renders → drafts at dk-publishing. Every post that doesn't make
    it is returned with the reason (never dropped silently). publish: "render" stops after the renders; "dry-run"
    validates with the publisher and creates nothing; "drafts" creates drafts."""
    node = "checker"
    run.emit(node, "start", agent="code", model="code", tools=["dk_intel_writer.evaluate_drafts", "dk_reels job",
             "dk-publishing batch"], task=f"{len(results)} captions to re-check, then make the media")  # fmt: skip
    t0 = time.monotonic()
    still, skipped = recheck_captions(run, node, platform, results, briefs)
    prices = checked_prices(run, node)
    ledger = media.AssetLedger()
    avoid = ledger.recent()  # grows as this run's own specs are planned, so the week's posts don't share footage
    specs: dict[str, tuple[dict[str, Any], Path]] = {}  # job_id → (spec, spec file)
    texts: dict[str, list[dict[str, str]]] = {}
    slot_of: dict[str, str] = {}
    for r in still:
        sid, b = str(r["slot_id"]), briefs[str(r["slot_id"])]
        spec, why = media.finalize_spec(designs.get(sid), jid=b["job_id"], platform=platform, slot_format=b["format"],
                                        planned_posted_at=b["planned_posted_at"], caption=r["caption"], avoid=avoid,
                                        week_label=b["job_id"].split("-")[0], prices=prices)  # fmt: skip
        if spec is None:
            skipped.append({"slot_id": sid, "why": why})
            continue
        path = run.dir / f"{b['job_id']}.spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=1))
        code, out = media.reels_job(path, "--dry-run")
        if code != 0:
            problems = "; ".join(f"{i['path']}: {i['message']}" for i in out.get("issues", [])[:3]) or out.get("error")
            skipped.append({"slot_id": sid, "why": f"the designer's spec was refused: {_short(str(problems), 200)}"})
            continue
        specs[b["job_id"]] = (spec, path)
        avoid = avoid + [a for a in media.assets_used({"plan": out.get("plan")}) if a not in avoid]
        slot_of[b["job_id"]] = sid
        texts[b["job_id"]] = [t for t in out.get("texts", []) if not t["where"].startswith("captions.")]
    run.emit(node, "end", status="done", ms_total=round((time.monotonic() - t0) * 1000), tokens_in=0, tokens_out=0,
             turns=0, output={"specs": list(specs), "skipped": skipped})  # fmt: skip

    if specs:
        bad = check_texts(run, node, platform, texts)
        for jid, problems in bad.items():
            if problems:
                skipped.append({"slot_id": slot_of[jid], "why": "on-screen text failed the brand check: "
                                + "; ".join(problems[:3])})  # fmt: skip
                specs.pop(jid)

    manifests: list[tuple[dict[str, Any], Path]] = []
    thumbs: set[str] = set()
    if specs:
        run.emit("renderer", "start", agent="code", model="code", tools=["dk_reels job"], task=f"{len(specs)} renders")
        t1 = time.monotonic()
        rendered: dict[str, Any] = {}
        for jid, (_, path) in specs.items():  # one at a time: a render uses every core
            code, out = media.reels_job(path)
            if out.get("manifest") and code in (0, 4):
                manifest = json.loads(Path(out["manifest"]).read_text())
                if code == 0:
                    manifests.append((manifest, Path(out["manifest"]).parent))
                    ledger.record(jid, media.assets_used(manifest))
                    if media.thumbnail(manifest, Path(out["manifest"]).parent, run.dir / f"{jid}.jpg"):
                        thumbs.add(jid)
                else:
                    failed_checks = ", ".join(q["check"] for q in manifest["qa"] if not q["ok"])
                    skipped.append(
                        {"slot_id": slot_of[jid], "why": f"the render failed its quality checks: {failed_checks}"}
                    )
            else:
                skipped.append(
                    {"slot_id": slot_of[jid], "why": f"the render failed: {_short(str(out.get('error')), 160)}"}
                )
            rendered[jid] = {"code": code, "status": out.get("status"), "timing_ms": out.get("timing_ms")}
        run.emit("renderer", "end", status="done", ms_total=round((time.monotonic() - t1) * 1000), tokens_in=0,
                 tokens_out=0, turns=0, output=rendered)  # fmt: skip

    proposed: list[dict[str, Any]] = []
    if manifests and client is None:  # render only: the media is ready in the reels engine's out/jobs, nothing is sent
        by_job = {m["job_id"]: slot_of[m["job_id"]] for m, _ in manifests}
        proposed = [{"slot_id": by_job[m["job_id"]], "proposal_id": m["job_id"], "state": "rendered",
                     "manifest": str(folder / "manifest.json"), "thumb": m["job_id"] in thumbs}
                    for m, folder in manifests]  # fmt: skip
    elif manifests and client is not None:
        run.emit("handover", "start", agent="code", model="code", tools=["dk-publishing /v1/posts/batch"],
                 task=f"{len(manifests)} posts as {'a dry run' if publish == 'dry-run' else 'drafts'}")  # fmt: skip
        t2 = time.monotonic()
        contract = client.spec(cache=REPO / "data" / "lab" / "publish-spec.json")
        report = handover.submit(client, manifests, contract, handover.load_config(),
                                 handover.MediaLedger(REPO / "data" / "lab" / "media-ledger.json"),
                                 dry_run=publish == "dry-run")  # fmt: skip
        done = {"submitted": report.submitted, "replayed": report.replayed, "would_submit": report.would_submit}
        by_job = {m["job_id"]: slot_of[m["job_id"]] for m, _ in manifests}
        for state, keys in done.items():
            proposed += [{"slot_id": by_job[k], "proposal_id": k, "state": state, "thumb": k in thumbs} for k in keys]
        skipped += [{"slot_id": by_job.get(h.job_id, h.job_id), "why": f"held: {h.reason}"} for h in report.held]
        skipped += [
            {
                "slot_id": by_job.get(f["post_key"], f["post_key"]),
                "why": "the publisher refused it: " + _short(json.dumps(f["problems"]), 200),
            }
            for f in report.failed
        ]
        run.emit("handover", "end", status="done", ms_total=round((time.monotonic() - t2) * 1000), tokens_in=0,
                 tokens_out=0, turns=0, output=report.as_dict())  # fmt: skip
    run.emit(
        node, "outcome", proposed=proposed, skipped=skipped
    )  # what became of each post: the console's result cards
    return {"proposed": proposed, "skipped": skipped}


def orchestrate(platform: str, trigger: str, inputs: Path | None = None, save_inputs: Path | None = None,
                publish: str = "demo", suffix: str = "") -> int:  # fmt: skip
    """publish: "demo" proposes captions to the toy calendar (as before); "dry-run" and "drafts" also design and render
    the media and hand each post to dk-publishing (dry-run validates and creates nothing; drafts waits for approval);
    "render" stops after the renders, needs no key and sends nothing."""
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
        media_mode = publish != "demo"
        ending = "stop after the renders" if publish == "render" else f"hand over as {publish}"
        plan_text = "fetch (code) → planner → 3 writers in parallel → re-check + propose (code)"
        if media_mode:
            plan_text = "fetch (code) → planner → 3 × (writer → designer) in parallel → re-check, check, render, "
            plan_text += f"{ending} (code)"
        run.emit("orchestrator", "start", platform=platform, trigger=trigger, inputs=str(inputs or "live"),
                 plan=plan_text, publish=publish)  # fmt: skip
        vocab: dict[str, Any] = {}
        inventory: dict[str, Any] = {}
        client: handover.PublishClient | None = None
        if media_mode:  # fail before spending a single token if the media step can't work
            if publish != "render":  # render-only never talks to the publisher, so it needs no key
                env = handover.load_env()
                if "DK_PUBLISH_URL" not in env or "DK_PUBLISH_KEY" not in env:
                    run.emit(
                        "orchestrator", "end", status="failed", error="DK_PUBLISH_URL and DK_PUBLISH_KEY are not set"
                    )
                    return 1
                client = handover.PublishClient(env["DK_PUBLISH_URL"], env["DK_PUBLISH_KEY"])
            if not (media.REELS / "package.json").exists():
                run.emit("orchestrator", "end", status="failed", error=f"the reels engine is not at {media.REELS}")
                return 1
            try:
                vocab, inv = code_calls(run, "orchestrator", "dk_reels_creative",
                                        [("list_templates", {}), ("inventory", {})])  # fmt: skip
            except Exception as ex:  # noqa: BLE001
                run.emit("orchestrator", "end", status="failed", error=f"dk_reels is unavailable: {_short(str(ex))}")
                return 1
            inventory = media.compact_inventory(inv)
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
        if media_mode:
            for b in briefs:
                b["job_id"] = media.job_id(platform, str(b.get("slot_id")), str(b["planned_posted_at"]), suffix)
            # a post can't be changed under its key (and the designer would make a different one): skip what exists
            have = {b["job_id"]: client.post(b["job_id"]) if client else None for b in briefs}
            dropped = dropped + [
                {"slot_id": b["slot_id"], "why": f"already handed over as {b['job_id']} (to redo a rejected one, run "
                 "with --suffix r2)"} for b in briefs if have[b["job_id"]]]  # fmt: skip
            briefs = [b for b in briefs if not have[b["job_id"]]]
            if not briefs:
                run.emit("orchestrator", "end", status="done", proposed=0, skipped=len(dropped), helpers_failed=0,
                         note="every planned slot already has a post: nothing to make")  # fmt: skip
                return 0
        lean = [{k: v for k, v in b.items() if k not in ("examples", "brand")} for b in briefs]
        run.emit("orchestrator", "delegate", to=[f"helper-{i + 1}" for i in range(len(briefs))],
                 note=f"{len(briefs)} writer helpers start at the same time; each gets its evidence and "
                      f"{len(examples)} checked examples",
                 slots=[{"node": f"helper-{i + 1}", **b} for i, b in enumerate(lean)])  # fmt: skip

        results: list[dict[str, Any]] = []
        designs: dict[str, dict[str, Any]] = {}

        def work(i: int, b: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            """A writer, then (only for a caption that passed, and only in media mode) its designer."""
            r = call_agent(run, f"helper-{i + 1}", "writer", json.dumps(b, ensure_ascii=False))
            if not media_mode or r is None or r.get("verdict") != "keep":
                return r, None
            task = media.designer_task(
                jid=b["job_id"], platform=platform, slot_format=str(b.get("format")),
                planned_posted_at=b["planned_posted_at"], angle=str(b.get("angle", "")),
                caption=str(r.get("caption", "")), evidence=b["evidence"], brand=reminders, vocab=vocab,
                inventory=inventory,
            )  # fmt: skip
            return r, call_agent(run, f"designer-{i + 1}", "designer", json.dumps(task, ensure_ascii=False))

        with ThreadPoolExecutor(max_workers=HELPERS) as pool:
            futures = {pool.submit(work, i, b): i for i, b in enumerate(briefs)}
            for f in as_completed(futures):
                r, d = f.result()
                if r is not None:
                    results.append(r)
                    if d is not None:
                        designs[str(r.get("slot_id") or "")] = d
        run.emit("orchestrator", "gathered", ok=len(results), failed=len(briefs) - len(results))

        by_slot = {str(b.get("slot_id")): b for b in briefs}
        for r in results:
            r["slot_id"] = str(r.get("slot_id") or "")
        unmatched = [r for r in results if r["slot_id"] not in by_slot]
        results = [r for r in results if r["slot_id"] in by_slot]
        try:
            if media_mode:
                lead = make_media(run, platform, results, designs, by_slot, publish, client)
            else:
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
    ap.add_argument(
        "--publish",
        default="demo",
        choices=["demo", "render", "dry-run", "drafts"],
        help="demo: propose captions to the toy calendar. render: also design, check and render the media, "
        "nothing sent anywhere. dry-run: then validate with dk-publishing, creating nothing. drafts: create drafts "
        "in the owner's Calendar",
    )
    ap.add_argument("--suffix", default="", help="redo a slot whose post was rejected: it needs a new key, e.g. r2")
    a = ap.parse_args()
    sys.exit(orchestrate(a.platform, a.trigger, a.inputs, a.save_inputs, a.publish, a.suffix))
