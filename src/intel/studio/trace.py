"""The agent layer, step by step: who called whom, with what, and what came back (ADR 0040).

Two halves, both over the run's own `events.jsonl`:

    recording   LoggedTools / LoggedPublisher wrap the ports the ORCHESTRATOR (code) uses, so every MCP call and every
                publisher call that is not made by an agent leaves an event too (`code_call`, `publisher_call`). Agents'
                own tool calls were already recorded (`tool_call`, `tool_result`).
    reading     topology()   the fixed structure: workflow phases, agents and the tools each may use, the MCP servers,
                             the ports. From the agent files and the server list: no run needed.
                layers()     the same structure with live numbers: calls, tokens, time, errors, and who called what.
                calls()      every unit of work in order; call_detail() one of them in full (the task it was given,
                             the tools it called with inputs and results, its thoughts, its answer, its retries).

Pure functions over event lists, so each is a test. Nothing here changes anything.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from intel.studio.agents import AGENTS_DIR, agent_spec
from intel.studio.mcp import server_specs
from intel.studio.runlog import RunLog, short

AGENTS = ("strategist", "director", "copywriter", "adapter", "designer", "curator", "analyst")
PHASES = (
    (
        "inputs",
        "Inputs",
        "code reads the brand pack, scores the posting windows, lists templates and footage (0 tokens)",
    ),
    ("strategist", "Strategist", "one agent call per chunk of ideas: the angle of each idea"),
    ("director", "Director", "one agent call per day: the look of each idea; code settles variety"),
    ("production", "Production", "per idea: copywriter, adapter, designer, then code checks, render and upload"),
    ("handover", "Handover", "code sends the drafts to the publisher's Calendar"),
)
WORKFLOW = (  # who works in which phase (the fixed shape of the system)
    ("inputs", "code"),
    ("strategist", "strategist"),
    ("director", "director"),
    ("production", "copywriter"),
    ("production", "adapter"),
    ("production", "designer"),
    ("handover", "code"),
)
PORTS = (
    ("publisher", "Publisher API", "drafts into the Calendar (dk-publishing)"),
    ("renderer", "Reels renderer", "a spec in, a video or pictures out (dhaka-kacchi-reels, on this Mac)"),
)
PREVIEW = 1500


# ---------- recording: what code itself calls ----------


class LoggedTools:
    """A ToolPort that notes every call the orchestrator makes (not agents: their calls are noted by the runner)."""

    def __init__(self, inner: Any, log: RunLog) -> None:
        self.inner, self.log = inner, log

    def call(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        t0 = time.monotonic()
        try:
            out = self.inner.call(server, tool, args)
        except Exception as ex:
            self._note(server, tool, args, None, t0, error=short(repr(ex), 300))
            raise
        err = out.get("error") if isinstance(out, dict) else None
        self._note(server, tool, args, out, t0, error=short(str(err), 300) if err else None)
        return out

    def _note(self, server: str, tool: str, args: Any, out: Any, t0: float, error: str | None) -> None:
        self.log.emit(f"code:{self.log.current_phase or 'run'}", "code_call", server=server, tool=tool,
                      input=short(args, PREVIEW), output=short(out, PREVIEW) if out is not None else "",
                      took_ms=round((time.monotonic() - t0) * 1000), ok=error is None, error=error)  # fmt: skip

    def offered(self, server: str) -> list[str]:
        out: list[str] = self.inner.offered(server)
        return out


class LoggedPublisher:
    """The publisher port with a note for every call (method, how long, whether it worked). Never the payload."""

    def __init__(self, inner: Any, log: RunLog) -> None:
        self.inner, self.log = inner, log

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.inner, name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            t0 = time.monotonic()
            try:
                out = target(*args, **kwargs)
            except Exception as ex:
                self._note(name, args, t0, short(repr(ex), 300))
                raise
            self._note(name, args, t0, None)
            return out

        return call

    def _note(self, method: str, args: tuple[Any, ...], t0: float, error: str | None) -> None:
        what = ""
        if args:
            first = args[0]
            what = (
                first.name
                if isinstance(first, Path)
                else (f"{len(first)} posts" if isinstance(first, list) else short(first, 80))
            )
        self.log.emit("publisher", "publisher_call", method=method, what=what,
                      took_ms=round((time.monotonic() - t0) * 1000), ok=error is None, error=error)  # fmt: skip


# ---------- reading: the fixed structure ----------


def _split(tool_id: str) -> tuple[str, str]:
    """`mcp__dk_intel_writer__evaluate_drafts` → (dk_intel_writer, evaluate_drafts)."""
    _, server, tool = tool_id.split("__", 2)
    return server, tool


def topology(agents_dir: Path = AGENTS_DIR) -> dict[str, Any]:
    servers: dict[str, set[str]] = {s: set() for s in server_specs(Path("."))}
    agents = []
    for name in AGENTS:
        if not (agents_dir / f"{name}.md").exists():
            continue
        spec = agent_spec(name, agents_dir)
        tools = []
        for t in spec["tools"]:
            server, tool = _split(t)
            servers.setdefault(server, set()).add(tool)
            tools.append({"server": server, "tool": tool})
        agents.append({"name": name, "model": spec["model"], "tools": tools, "builtin": spec["builtin"],
                       "prompt_chars": len(spec["prompt"])})  # fmt: skip
    return {
        "phases": [{"id": p, "label": label, "what": what} for p, label, what in PHASES],
        "workflow": [{"phase": p, "worker": w} for p, w in WORKFLOW],
        "agents": agents,
        "servers": [{"name": s, "tools": sorted(t)} for s, t in sorted(servers.items())],
        "ports": [{"id": i, "label": label, "what": what} for i, label, what in PORTS],
    }


# ---------- reading: the live numbers ----------


def _role(node: str) -> str:
    return node.split(":")[0].split("-")[0]


def _n(e: dict[str, Any], key: str) -> int:
    return int(e.get(key) or 0)


def _bump(d: dict[str, Any], key: str, **add: float) -> dict[str, Any]:
    row = d.setdefault(key, defaultdict(float))
    for k, v in add.items():
        row[k] += v
    return row  # type: ignore[no-any-return]


def layers(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Live numbers per layer and who called whom, from the log."""
    agents: dict[str, Any] = {}
    tools: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    open_calls: dict[str, str] = {}
    publisher: dict[str, Any] = {}
    renderer = {"renders": 0, "ok": 0, "seconds": 0.0}
    phases: dict[str, dict[str, Any]] = {}
    uploads = 0

    def tool_row(server: str, tool: str) -> dict[str, Any]:
        return tools.setdefault(f"{server}.{tool}", {"server": server, "tool": tool, "by_agents": 0, "by_code": 0,
                                                     "errors": 0, "ms": 0, "ms_max": 0})  # fmt: skip

    for e in events:
        kind, node = e.get("kind"), str(e.get("node", ""))
        if kind == "phase":
            row = phases.setdefault(e["phase"], {"phase": e["phase"], "status": "pending", "seconds": None,
                                                 "start_ms": None, "end_ms": None})  # fmt: skip
            row.update(status=e["status"], seconds=e.get("seconds"))
            row["start_ms" if e["status"] == "start" else "end_ms"] = e["ms"]
        elif kind == "start" and e.get("agent"):
            a = _bump(agents, e["agent"], calls=1, running=1)
            a["model"] = e.get("model")
            open_calls[node] = e["agent"]
            edges[("orchestrator", f"agent:{e['agent']}")]["calls"] += 1
        elif kind == "end" and node in open_calls:
            name = open_calls.pop(node)
            _bump(agents, name, running=-1, tokens_in=_n(e, "tokens_in"), tokens_out=_n(e, "tokens_out"),
                  tokens_cached=_n(e, "tokens_cached"), seconds=float(e.get("ms_total") or 0) / 1000,
                  turns=_n(e, "turns"), failed=0 if e.get("status") == "done" else 1)  # fmt: skip
        elif kind == "retry":
            _bump(agents, _role(node), retries=1)
        elif kind == "tool_call":
            row = tool_row(str(e.get("server")), str(e.get("tool")))
            row["by_agents"] += 1
            agent = _role(node)
            _bump(agents, agent, tool_calls=1)
            edges[(f"agent:{agent}", f"server:{e.get('server')}")]["calls"] += 1
            edges[(f"server:{e.get('server')}", f"tool:{e.get('server')}.{e.get('tool')}")]["calls"] += 1
        elif kind == "tool_result":
            row = tool_row(str(e.get("server") or _server_of(tools, str(e.get("tool")))), str(e.get("tool")))
            row["ms"] += int(e.get("took_ms") or 0)
            row["ms_max"] = max(row["ms_max"], int(e.get("took_ms") or 0))
            row["errors"] += 1 if e.get("is_error") else 0
        elif kind == "code_call":
            row = tool_row(str(e.get("server")), str(e.get("tool")))
            row["by_code"] += 1
            row["ms"] += int(e.get("took_ms") or 0)
            row["ms_max"] = max(row["ms_max"], int(e.get("took_ms") or 0))
            row["errors"] += 0 if e.get("ok") else 1
            edges[("orchestrator", f"server:{e.get('server')}")]["calls"] += 1
            edges[(f"server:{e.get('server')}", f"tool:{e.get('server')}.{e.get('tool')}")]["calls"] += 1
        elif kind == "publisher_call":
            _bump(
                publisher, str(e.get("method")), calls=1, ms=int(e.get("took_ms") or 0), errors=0 if e.get("ok") else 1
            )
            edges[("orchestrator", "port:publisher")]["calls"] += 1
        elif kind == "render" and e.get("status") == "end":
            renderer["renders"] += 1
            renderer["ok"] += 0 if e.get("code") else 1
            renderer["seconds"] += float(e.get("seconds") or 0)
            edges[("orchestrator", "port:renderer")]["calls"] += 1
        elif kind == "upload":
            uploads += 1
    return {
        "phases": list(phases.values()),
        "agents": {
            k: {a: (round(b, 1) if isinstance(b, float) else b) for a, b in v.items()} for k, v in agents.items()
        },
        "tools": sorted(tools.values(), key=lambda r: (r["server"], r["tool"])),
        "edges": [{"from": a, "to": b, "calls": int(v["calls"])} for (a, b), v in sorted(edges.items())],
        "publisher": {k: dict(v) for k, v in publisher.items()},
        "renderer": renderer,
        "uploads": uploads,
    }


def _server_of(tools: dict[str, dict[str, Any]], tool: str) -> str:
    """A tool_result carries no server: it belongs to the most recent call of that tool."""
    for row in reversed(list(tools.values())):
        if row["tool"] == tool:
            return str(row["server"])
    return "?"


# ---------- reading: every unit of work, and one in full ----------


def calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every agent call, code call, publisher call, render and upload in time order, small enough to list."""
    seen: dict[str, int] = defaultdict(int)
    open_: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for e in events:
        kind, node = e.get("kind"), str(e.get("node", ""))
        if kind == "start" and e.get("agent"):
            n = seen[node]
            seen[node] += 1
            row = {"id": f"{node}#{n}", "type": "agent", "who": e["agent"], "node": node, "start": e["ms"], "end": None,
                   "status": "working", "tokens": 0, "turns": None, "tools": 0, "model": e.get("model")}  # fmt: skip
            open_[node] = row
            out.append(row)
        elif kind == "tool_call":
            if node in open_:
                open_[node]["tools"] += 1
            tid = f"tool#{len(out)}"
            row = {"id": tid, "type": "tool", "who": f"{e.get('server')}.{e.get('tool')}", "node": node,
                   "start": e["ms"], "end": None, "status": "working", "tokens": 0, "turns": None, "tools": 0,
                   "ref": e["ms"]}  # fmt: skip
            open_[f"tool:{node}:{e.get('tool')}"] = row
            out.append(row)
        elif kind == "tool_result" and f"tool:{node}:{e.get('tool')}" in open_:
            open_.pop(f"tool:{node}:{e.get('tool')}").update(
                end=e["ms"], status="failed" if e.get("is_error") else "done"
            )
        elif kind == "end" and node in open_:
            row = open_.pop(node)
            row.update(end=e["ms"], status=e.get("status") or "done", tokens=_n(e, "tokens_in") + _n(e, "tokens_out"),
                       turns=e.get("turns"))  # fmt: skip
        elif kind == "code_call":
            who = f"{e.get('server')}.{e.get('tool')}"
            out.append({"id": f"code#{len(out)}", "type": "code", "who": who, "node": node,
                        "start": e["ms"] - _n(e, "took_ms"), "end": e["ms"],
                        "status": "done" if e.get("ok") else "failed", "tokens": 0, "turns": None, "tools": 0,
                        "ref": e["ms"]})  # fmt: skip
        elif kind == "publisher_call":
            out.append({"id": f"pub#{len(out)}", "type": "publisher", "who": f"publisher.{e.get('method')}",
                        "node": node, "start": e["ms"] - _n(e, "took_ms"), "end": e["ms"],
                        "status": "done" if e.get("ok") else "failed", "tokens": 0, "turns": None, "tools": 0,
                        "ref": e["ms"], "what": e.get("what")})  # fmt: skip
        elif kind == "render" and e.get("status") == "start":
            rid = f"render:{node}#{seen['render:' + node]}"
            row = {"id": rid, "type": "render", "who": "renderer", "node": node, "start": e["ms"], "end": None,
                   "status": "working", "tokens": 0, "turns": None, "tools": 0}  # fmt: skip
            seen["render:" + node] += 1
            open_["render:" + node] = row
            out.append(row)
        elif kind == "render" and e.get("status") == "end" and f"render:{node}" in open_:
            open_.pop(f"render:{node}").update(end=e["ms"], status="done" if not e.get("code") else "failed")
        elif kind == "upload":
            out.append({"id": f"upload:{node}#{len(out)}", "type": "upload", "who": "publisher.upload", "node": node,
                        "start": e["ms"] - int(float(e.get("seconds") or 0) * 1000), "end": e["ms"], "status": "done",
                        "tokens": 0, "turns": None, "tools": 0})  # fmt: skip
    return out


def _json(text: Any) -> Any:
    if isinstance(text, str):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def call_detail(events: list[dict[str, Any]], call_id: str) -> dict[str, Any] | None:
    """One call in full. For an agent: what it was given, every tool it used (input, result, time), what it thought and
    answered, tokens and retries. For a code or publisher call: the call itself."""
    row = next((c for c in calls(events) if c["id"] == call_id), None)
    if row is None:
        return None
    if row["type"] in ("code", "publisher"):
        ev = next((e for e in events if e["ms"] == row.get("ref") and e["kind"] in ("code_call", "publisher_call")), {})
        return {**row, "input": _json(ev.get("input")), "output": _json(ev.get("output")), "error": ev.get("error"),
                "method": ev.get("method"), "what": ev.get("what")}  # fmt: skip
    if row["type"] == "tool":
        tool = str(row["who"]).split(".", 1)[1]
        call = next((e for e in events if e["ms"] == row["ref"] and e["kind"] == "tool_call"), {})
        res = next((e for e in events if e["kind"] == "tool_result" and e["node"] == row["node"]
                    and e.get("tool") == tool and e["ms"] >= row["ref"]), {})  # fmt: skip
        return {**row, "input": _json(call.get("input")), "output": _json(res.get("output")),
                "took_ms": res.get("took_ms"), "error": bool(res.get("is_error"))}  # fmt: skip
    if row["type"] != "agent":
        return dict(row)
    node, start, end = row["node"], row["start"], row["end"]
    mine = [e for e in events if e.get("node") == node and start <= e["ms"] and (end is None or e["ms"] <= end)]
    first = next((e for e in mine if e["kind"] == "start"), {})
    last: dict[str, Any] = next((e for e in reversed(mine) if e["kind"] == "end"), {})
    pending: dict[str, dict[str, Any]] = {}
    used: list[dict[str, Any]] = []
    for e in mine:
        if e["kind"] == "tool_call":
            item = {"server": e.get("server"), "tool": e.get("tool"), "input": _json(e.get("input")), "at": e["ms"],
                    "output": None, "took_ms": None, "error": False}  # fmt: skip
            pending[str(e.get("tool"))] = item
            used.append(item)
        elif e["kind"] == "tool_result" and str(e.get("tool")) in pending:
            p = pending.pop(str(e.get("tool")))
            p.update(output=_json(e.get("output")), took_ms=e.get("took_ms"), error=bool(e.get("is_error")))
    return {
        **row,
        "tools_allowed": first.get("tools"), "tools_hidden": first.get("hidden"),
        "task": _json(first.get("task")), "thoughts": [e.get("text") for e in mine if e["kind"] == "thought"],
        "tool_calls": used, "output": _json(last.get("output")),
        "tokens_in": last.get("tokens_in"), "tokens_out": last.get("tokens_out"),
        "tokens_cached": last.get("tokens_cached"),
        "retries": [e.get("reason") for e in events if e.get("kind") == "retry" and e.get("node") == node],
    }  # fmt: skip
