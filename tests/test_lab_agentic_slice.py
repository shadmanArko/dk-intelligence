"""lab/agentic-slice: the engine (orchestrator as code) and the Agent Console, with a stand-in `claude` that emits
the same event stream as Claude Code. No model is called; the real run is by hand (the lab README)."""

import importlib.util
import json
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[1] / "lab" / "agentic-slice"


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(f"lab_{name}", LAB / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def engine(tmp_path, monkeypatch):
    run = _load("run")
    monkeypatch.setattr(run, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(run, "LOCK", tmp_path / "runs" / ".running")
    monkeypatch.setattr(run, "CLAUDE", str(LAB / "testing" / "fake_claude.py"))
    monkeypatch.setenv("FAKE_ARGS_LOG", str(tmp_path / "args.jsonl"))
    monkeypatch.setenv("FAKE_DELAY_S", "0.4")
    return run


def _events(engine):  # type: ignore[no-untyped-def]
    (log,) = list(engine.RUNS.glob("*.jsonl"))
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_the_engine_runs_planner_then_parallel_helpers_then_lead(engine, tmp_path):
    assert engine.orchestrate("instagram", "manual") == 0
    ev = _events(engine)
    starts = [e["node"] for e in ev if e["kind"] == "start"]
    assert starts[:2] == ["orchestrator", "planner"] and starts[-1] == "writer-lead"
    assert set(starts[2:5]) == {"helper-1", "helper-2", "helper-3"}
    windows = {e["node"]: [e["ms"]] for e in ev if e["kind"] == "start"}
    for e in ev:
        if e["kind"] == "end" and e["node"] in windows:
            windows[e["node"]].append(e["ms"])
    h = [windows[f"helper-{i}"] for i in (1, 2, 3)]
    assert max(s for s, _ in h) < min(end for _, end in h)  # all three were running at the same moment
    end = ev[-1]
    assert end["node"] == "orchestrator" and end["status"] == "done" and end["proposed"] == 3
    assert any(e["kind"] == "tool_call" and e["server"] == "demo_publisher" for e in ev)
    assert all(e["tool"] != "?" for e in ev if e["kind"] == "tool_result")  # every result is matched to its call
    assert not engine.LOCK.exists() and (engine.RUNS / f"{ev[0]['t'] and _run_id(engine)}.md").exists()


def _run_id(engine):  # type: ignore[no-untyped-def]
    return next(engine.RUNS.glob("*.jsonl")).stem


def test_each_agent_gets_only_its_own_tools_and_no_builtins(engine, tmp_path):
    engine.orchestrate("instagram", "manual")
    calls = [json.loads(line) for line in (tmp_path / "args.jsonl").read_text().splitlines()]
    assert all(c["builtins"] == "" for c in calls)  # no shell, web or file tools for any agent
    planner, *helpers, lead = calls
    assert all(t.startswith(("mcp__dk_intel_planner__", "mcp__demo_publisher__list_slots")) for t in planner["tools"])
    assert all(t.startswith("mcp__dk_intel_writer__") for h in helpers for t in h["tools"])
    assert lead["tools"] == ["mcp__dk_intel_writer__evaluate_drafts", "mcp__demo_publisher__propose_post"]
    cfgs = {p.stem.removesuffix(".mcp"): json.loads(p.read_text()) for p in engine.RUNS.glob("*/*.mcp.json")}
    assert set(cfgs["planner"]["mcpServers"]) == {"dk_intel_planner", "demo_publisher"}
    assert set(cfgs["helper-1"]["mcpServers"]) == {"dk_intel_writer"}  # a writer can't even reach the calendar


def test_a_failed_helper_is_reported_and_the_run_finishes(engine, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_SLOT", "s2")
    assert engine.orchestrate("instagram", "manual") == 0
    ev = _events(engine)
    failed = [e for e in ev if e["kind"] == "end" and e.get("status") == "failed"]
    assert [e["node"] for e in failed] == ["helper-2"] and ev[-1]["proposed"] == 2 and ev[-1]["helpers_failed"] == 1


def test_one_run_at_a_time(engine):
    engine.RUNS.mkdir(parents=True)
    engine.LOCK.write_text("someone")
    assert engine.orchestrate("instagram", "manual") == 2


def test_the_console_lists_streams_and_starts_runs(engine, monkeypatch):
    from fastapi.testclient import TestClient

    console = _load("console")
    monkeypatch.setattr(console, "RUNS", engine.RUNS)
    monkeypatch.setattr(console, "LOCK", engine.LOCK)
    engine.orchestrate("facebook", "schedule")
    c = TestClient(console.app)
    (summary,) = c.get("/api/runs").json()
    assert summary["trigger"] == "schedule" and summary["status"] == "done" and summary["agents"] == 5
    assert summary["proposed"] == 3 and summary["tool_calls"] >= 6 and summary["tokens"] == 5 * 1500
    body = c.get(f"/api/runs/{summary['id']}/stream").text
    assert body.count("data: ") >= 10 and "event: done" in body
    assert "Run now" in c.get("/").text and c.get("/api/runs/..%2Fx/stream").status_code == 404
    engine.LOCK.write_text("busy")
    assert c.post("/api/run", json={"platform": "instagram"}).status_code == 409  # the button is refused mid-run
    assert c.post("/api/run", json={"platform": "tiktok"}).status_code == 422


def test_trace_prints_the_run_even_without_the_audit(engine, monkeypatch, capsys):
    engine.orchestrate("facebook", "schedule")
    monkeypatch.syspath_prepend(str(LAB))
    trace = _load("trace")
    console = __import__("console")
    monkeypatch.setattr(trace, "RUNS", engine.RUNS)
    monkeypatch.setattr(console, "RUNS", engine.RUNS)
    monkeypatch.setattr(trace, "audit", lambda run_id: (_ for _ in ()).throw(OSError("no audit dir")))
    assert trace.main() == 0
    out = capsys.readouterr().out
    assert "delegate → helper-1, helper-2, helper-3" in out and "→ demo_publisher.propose_post" in out
    assert "dk_intel audit: unavailable" in out
