"""Mission control: what a run is doing, as numbers, from the run's own events. Nothing here is invented motion.

    events.jsonl + state.json  →  telemetry(view, events, now_ms)  →  one JSON the page draws

The page shows: a progress bar with an honest estimate, the pipeline with how many ideas wait, work and finish at each
stage, a timeline of every agent call (a Gantt chart), a matrix of every idea against every stage, token use by agent,
and the last events in plain words. A demo run (`demo`) is made of synthetic EVENTS and goes through the same function,
so what you see in the demo is what a real run draws.

Pure functions: easy to test, no clocks, no files.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

STAGES = (
    ("plan", "Plan", "strategist", 0.05),
    ("direct", "Direct", "director", 0.05),
    ("write", "Write", "copywriter", 0.15),
    ("adapt", "Adapt", "adapter", 0.10),
    ("design", "Design", "designer", 0.20),
    ("render", "Render", "renderer", 0.35),
    ("upload", "Upload", "uploader", 0.10),
)
STAGE_IDS = tuple(s[0] for s in STAGES)
ROLE_STAGE = {
    "strategist": "plan",
    "director": "direct",
    "copywriter": "write",
    "adapter": "adapt",
    "designer": "design",
}
LANES = ("strategist", "director", "copywriter", "adapter", "designer", "render", "upload")
PHASES = ("inputs", "strategist", "director", "production", "handover")


def _role(node: str) -> str:
    return node.split(":")[0].split("-")[0]


def _idea_of(node: str) -> str | None:
    tail = node.split(":", 1)[1] if ":" in node else node
    return tail if len(tail) >= 4 and tail[0] == "d" and tail[1:3].isdigit() else None


def _tokens(e: dict[str, Any]) -> int:
    return int(e.get("tokens_in") or 0) + int(e.get("tokens_out") or 0)


def _bar(lane: str, key: str, idea: str | None, start: int, end: int | None = None, **more: Any) -> dict[str, Any]:
    return {
        "lane": lane,
        "key": key,
        "idea": idea,
        "start": start,
        "end": end,
        "tokens": 0,
        "ok": True,
        "model": None,
        "turns": None,
        **more,
    }


def calls(events: list[dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    """Every unit of work as a bar: an agent call, a render, an upload. `running` is true while it is still working."""
    out: list[dict[str, Any]] = []
    open_: dict[str, dict[str, Any]] = {}
    for e in events:
        node, kind = str(e.get("node", "")), e.get("kind")
        if kind == "start" and e.get("agent"):
            open_[node] = _bar(e["agent"], node, _idea_of(node), e["ms"], model=e.get("model"))
            out.append(open_[node])
        elif kind == "end" and node in open_:
            open_.pop(node).update(end=e["ms"], tokens=_tokens(e), ok=e.get("status") == "done", turns=e.get("turns"))
        elif kind == "render" and e.get("status") == "start":
            open_[f"render:{node}"] = _bar("render", f"render:{node}", node, e["ms"])
            out.append(open_[f"render:{node}"])
        elif kind == "render" and e.get("status") == "end" and f"render:{node}" in open_:
            open_.pop(f"render:{node}").update(end=e["ms"], ok=not e.get("code"))
        elif kind == "upload":
            begin = max(0, e["ms"] - int(float(e.get("seconds") or 0.5) * 1000))
            out.append(_bar("upload", f"upload:{node}", node, begin, e["ms"]))
    for bar in out:
        bar["running"] = bar["end"] is None
        if bar["end"] is None:
            bar["end"] = now_ms
    return out


def _idea_cells(view_idea: dict[str, Any], bars: list[dict[str, Any]], treated: set[str], phase_open: set[str],
                uploads_off: bool) -> dict[str, str]:  # fmt: skip
    """Stage → pending | active | done | held | skipped for one idea."""
    iid = view_idea["id"]
    own = {s: [b for b in bars if b["idea"] == iid and ROLE_STAGE.get(b["lane"], b["lane"]) == s] for s, *_ in STAGES}
    cells: dict[str, str] = {}
    angled = bool(view_idea.get("angle"))
    cells["plan"] = "done" if angled else ("active" if "strategist" in phase_open else "pending")
    directing = any(
        b["running"] and b["lane"] == "director" and b["key"].endswith(str(view_idea["date"])) for b in bars
    )
    cells["direct"] = "done" if iid in treated or view_idea.get("template") else ("active" if directing else "pending")
    for stage in ("write", "adapt", "design", "render", "upload"):
        mine = own[stage]
        if any(b["running"] for b in mine):
            cells[stage] = "active"
        elif any(not b["running"] and b["ok"] for b in mine):
            cells[stage] = "done"
        else:
            cells[stage] = "pending"
    if uploads_off and cells["upload"] == "pending":
        cells["upload"] = "skipped"
    if view_idea.get("outcome"):  # the run finished this idea: whatever it did not need is skipped
        cells = {s: ("done" if c in ("done", "active") else "skipped") for s, c in cells.items()}
    held = "*" in (view_idea.get("holds") or {})
    if held:
        seen_gap = False
        for s in STAGE_IDS:
            if cells[s] == "done":
                continue
            cells[s] = "held" if not seen_gap else "skipped"
            seen_gap = True
    return cells


def _say(e: dict[str, Any]) -> str | None:
    """One event in plain words, or None for the ones that are noise."""
    node, kind = str(e.get("node", "")), e.get("kind")
    who = _idea_of(node) or ""
    if kind == "start" and e.get("agent"):
        return f"{e['agent']} starts{' on ' + who if who else ''}"
    if kind == "end" and node != "orchestrator":
        sec = float(e.get("ms_total") or 0) / 1000
        return f"{_role(node)} finished{' ' + who if who else ''} · {sec:.0f} s · {_tokens(e):,} tokens"
    if kind == "end":
        return f"run {e.get('status')}: {e.get('completed', '?')} of {e.get('ideas', '?')} ideas"
    if kind == "phase":
        return f"phase {e.get('phase')} {e.get('status')}"
    if kind == "treatment":
        return f"director chose {e.get('template')} for {node}"
    if kind == "render":
        return f"render {e.get('status')} {node}"
    if kind == "upload":
        return f"uploaded {node}"
    if kind == "retry":
        return f"retry {_role(node)} {who}: {e.get('reason')}"
    if kind == "tool_call":
        return f"{_role(node)} used {e.get('tool')}"
    if kind == "chunk":
        return f"strategist wrote angles for {len(e.get('ideas') or [])} ideas"
    if kind == "ideas":
        return f"{e.get('planned')} ideas planned"
    return None


def telemetry(view: dict[str, Any], events: list[dict[str, Any]], now_ms: int | None = None) -> dict[str, Any]:
    """Everything the mission-control page draws, for one run. `view` is the console's run_view; `events` its log."""
    running = view.get("status") == "running"
    last_ms = events[-1]["ms"] if events else 0
    now = max(now_ms if now_ms is not None else last_ms, last_ms) if running else last_ms
    bars = calls(events, now)
    ideas = view.get("ideas") or []
    treated = {str(e["node"]) for e in events if e.get("kind") == "treatment"}
    phase_open = {b["lane"] for b in bars if b["running"]}
    uploads_off = view.get("publish") == "render"
    matrix = []
    weights = {s: w for s, _, _, w in STAGES}
    score = 0.0
    for idea in ideas:
        cells = _idea_cells(idea, bars, treated, phase_open, uploads_off)
        for s, c in cells.items():
            score += weights[s] * (1 if c in ("done", "skipped", "held") else 0.5 if c == "active" else 0)
        matrix.append({"id": idea["id"], "date": idea["date"], "format": idea["format"],
                       "template": idea.get("template"), "angle": idea.get("angle"), "cells": cells,
                       "held": "*" in (idea.get("holds") or {}),
                       "outcome": (idea.get("outcome") or {}).get("state")})  # fmt: skip
    n = len(ideas)
    percent = 100.0 if (view.get("status") in ("done", "partial") and not running) else (100 * score / n if n else 0.0)
    phases = {e["phase"]: e["status"] for e in events if e.get("kind") == "phase"}
    if n == 0 and running:  # before the plan exists: the inputs phase is all there is to show
        percent = 2.0 if phases.get("inputs") == "start" else 0.0
    elapsed = now / 1000
    eta = round(elapsed * (100 - percent) / percent) if running and percent >= 8 else None
    stages = []
    for sid, label, worker, _w in STAGES:
        cs = [m["cells"][sid] for m in matrix]
        stages.append({"id": sid, "label": label, "worker": worker, "total": n,
                       "done": sum(c == "done" for c in cs), "active": sum(c == "active" for c in cs),
                       "waiting": sum(c == "pending" for c in cs), "held": sum(c == "held" for c in cs),
                       "skipped": sum(c == "skipped" for c in cs)})  # fmt: skip
    roles: dict[str, dict[str, Any]] = {}
    for b in bars:
        r = roles.setdefault(b["lane"], {"calls": 0, "tokens": 0, "seconds": 0.0, "running": 0})
        r["calls"] += 1
        r["tokens"] += b["tokens"]
        r["seconds"] += (b["end"] - b["start"]) / 1000
        r["running"] += 1 if b["running"] else 0
    tokens = int(view.get("tokens") or 0) or sum(r["tokens"] for r in roles.values())
    series, acc = [], 0
    for e in events:
        if e.get("kind") == "end" and e.get("node") != "orchestrator":
            acc += _tokens(e)
            series.append([e["ms"], acc])
    log = [t for e in events[-60:] if (t := _say(e))][-14:]
    return {
        "run": view.get("id"),
        "status": view.get("status"),
        "publish": view.get("publish"),
        "running": running,
        "percent": round(min(percent, 100.0), 1),
        "elapsed_s": round(elapsed),
        "eta_s": eta,
        "now_ms": now,
        "ideas": n,
        "ideas_done": sum(1 for m in matrix if m["outcome"]),
        "ideas_held": sum(m["held"] for m in matrix),
        "tokens": tokens,
        "cap": view.get("cap"),
        "tokens_in": sum(int(e.get("tokens_in") or 0) for e in events if e.get("kind") == "end"),
        "tokens_out": sum(int(e.get("tokens_out") or 0) for e in events if e.get("kind") == "end"),
        "tokens_cached": sum(int(e.get("tokens_cached") or 0) for e in events if e.get("kind") == "end"),
        "retries": sum(1 for e in events if e.get("kind") == "retry"),
        "tool_calls": sum(1 for e in events if e.get("kind") == "tool_call"),
        "stages": stages,
        "matrix": matrix,
        "bars": bars,
        "roles": roles,
        "lanes": list(LANES),
        "phases": [{"phase": p, "status": phases.get(p, "pending")} for p in PHASES],
        "series": series,
        "log": log,
        "working": [
            {"lane": b["lane"], "idea": b["idea"], "seconds": round((now - b["start"]) / 1000)}
            for b in bars
            if b["running"]
        ][:12],  # fmt: skip
    }


# ---------- demo: synthetic events that go through the very same function ----------

DEMO_SPEEDUP_MS = 6000  # one poll of the demo moves the demo clock this far


def demo_events(seed: int = 7, n_ideas: int = 12) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A believable run of `n_ideas` ideas over four days: four ideas at a time through the crew, one render at a time.
    Returns (events, a run view with the ideas). The numbers are made up; the shape is a real run's."""
    rnd = random.Random(seed)  # noqa: S311 - a demo, not security
    ev: list[dict[str, Any]] = []
    templates = ["one-take", "kinetic-text", "photo-motion", "collage", "steps", "split", "statement"]
    fmts = ["reel", "reel", "carousel", "reel", "story", "reel", "image", "reel"]
    ideas: list[dict[str, Any]] = [{"id": f"d{1 + k // 3:02d}i{1 + k % 3}", "date": f"2026-10-{14 + k // 3:02d}",
              "format": fmts[k % len(fmts)],
              "template": None, "angle": None, "holds": {}, "outcome": None} for k in range(n_ideas)]  # fmt: skip

    def add(ms: int, node: str, kind: str, **kw: Any) -> None:
        ev.append({"t": "", "ms": ms, "node": node, "kind": kind, **kw})

    add(1, "orchestrator", "start", trigger="demo", publish="drafts")
    add(300, "orchestrator", "phase", phase="inputs", status="start")
    add(8000, "orchestrator", "phase", phase="inputs", status="end", seconds=7.7)
    add(8100, "orchestrator", "ideas", planned=n_ideas, dropped=[])
    add(8200, "orchestrator", "phase", phase="strategist", status="start")
    t = 8300
    for c in range(0, n_ideas, 6):
        dur = rnd.randint(40000, 56000)
        add(t, f"strategist-{c // 6 + 1}", "start", agent="strategist", model="sonnet")
        add(
            t + dur,
            f"strategist-{c // 6 + 1}",
            "end",
            status="done",
            ms_total=dur,
            tokens_in=5300,
            tokens_out=5200,
            turns=1,
        )
        add(
            t + dur + 10,
            "orchestrator",
            "chunk",
            chunk=f"strategist-{c // 6 + 1}",
            ideas=[i["id"] for i in ideas[c : c + 6]],
            angled=6,
        )
        t += dur + 20
    add(t, "orchestrator", "phase", phase="strategist", status="end", seconds=(t - 8200) / 1000)
    add(t + 10, "orchestrator", "phase", phase="director", status="start")
    t += 20
    angle_at = {i["id"]: t for i in ideas}
    for day in sorted({i["date"] for i in ideas}):
        dur = rnd.randint(15000, 24000)
        add(t, f"director:{day}", "start", agent="director", model="sonnet")
        add(t + dur, f"director:{day}", "end", status="done", ms_total=dur, tokens_in=5200, tokens_out=1700, turns=1)
        for i in [x for x in ideas if x["date"] == day]:
            add(t + dur + 5, i["id"], "treatment", template=rng_pick(rnd, templates), variant="", directed=True)
        t += dur + 10
    add(t, "orchestrator", "phase", phase="director", status="end", seconds=60)
    add(t + 5, "orchestrator", "phase", phase="production", status="start")
    t0 = t + 10
    workers = [t0] * 4
    render_free = t0
    for i in ideas:
        w = min(range(4), key=lambda x: workers[x])
        s = workers[w]
        for role, lo, hi, tok in (
            ("copywriter", 9000, 16000, 7800),
            ("adapter", 4000, 8000, 3600),
            ("designer", 11000, 19000, 10800),
        ):
            dur = rnd.randint(lo, hi)
            add(s, f"{role}:{i['id']}", "start", agent=role, model="sonnet")
            add(
                s + dur,
                f"{role}:{i['id']}",
                "end",
                status="done",
                ms_total=dur,
                tokens_in=tok,
                tokens_out=int(tok * 0.1),
                turns=2,
            )
            s += dur
        r0 = max(s, render_free)
        rd = rnd.randint(20000, 42000)
        add(r0, i["id"], "render", status="start")
        add(r0 + rd, i["id"], "render", status="end", code=0, seconds=rd / 1000)
        add(r0 + rd + 900, i["id"], "upload", files=1, seconds=0.9)
        render_free = r0 + rd
        workers[w] = s
    end = render_free + 1500
    add(end, "orchestrator", "phase", phase="production", status="end", seconds=(end - t0) / 1000)
    add(end + 10, "orchestrator", "phase", phase="handover", status="start")
    add(end + 6000, "orchestrator", "phase", phase="handover", status="end", seconds=6.0)
    add(
        end + 6010,
        "orchestrator",
        "end",
        status="done",
        ideas=n_ideas,
        completed=n_ideas,
        tokens=0,
        seconds=end // 1000,
    )
    ev.sort(key=lambda e: e["ms"])
    view: dict[str, Any] = {
        "id": "demo",
        "status": "running",
        "publish": "drafts",
        "tokens": 0,
        "cap": 4_000_000,
        "ideas": ideas,
    }
    for i in ideas:
        i["angle_at"] = angle_at[i["id"]]
    return ev, view


def rng_pick(rnd: random.Random, options: list[str]) -> str:
    return options[rnd.randrange(len(options))]


def demo(tick: int) -> dict[str, Any]:
    """The demo run as it would look `tick` polls after it began (it loops, with a pause at the end)."""
    events, view = demo_events()
    total = events[-1]["ms"]
    clock = (max(0, tick) * DEMO_SPEEDUP_MS) % (total + 5 * DEMO_SPEEDUP_MS)
    clock = min(clock, total)
    seen = [e for e in events if e["ms"] <= clock]
    ideas = []
    ended = clock >= total
    for i in view["ideas"]:
        row = dict(i)
        row["angle"] = f"demo angle {i['id']}" if clock >= i["angle_at"] else None
        row["template"] = next(
            (str(e.get("template")) for e in seen if e["kind"] == "treatment" and e["node"] == i["id"]), None
        )
        if any(e["kind"] == "upload" and e["node"] == i["id"] for e in seen):
            row["outcome"] = {"state": "drafted", "platforms": ["instagram"]}
        ideas.append(row)
    shown = {**view, "ideas": ideas, "status": "done" if ended else "running"}
    out = telemetry(shown, seen, now_ms=clock)
    out["demo"] = True
    return out


def started_at(events: list[dict[str, Any]]) -> dt.datetime | None:
    """When the run's first event happened (UTC), from the log's own timestamps."""
    for e in events:
        if e.get("t"):
            try:
                return dt.datetime.fromisoformat(str(e["t"])) - dt.timedelta(milliseconds=int(e.get("ms") or 0))
            except ValueError:
                return None
    return None
