"""Operations pages (ADR 0040): the agent trace, the ML reader, the run controls and the console's guard."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from intel import mlview
from intel.studio import console, console_system, ops, telemetry, trace
from intel.studio.run import Options
from intel.studio.runlog import RunLog, read_events
from tests.studio_fakes import TODAY
from tests.test_studio import World


def _client() -> TestClient:
    return TestClient(console.app, base_url="http://127.0.0.1:8771", headers={"x-intel-token": console.TOKEN})


# ---------- the agent layer ----------


def test_a_run_records_every_call_the_code_itself_makes_and_the_trace_reads_them_back(tmp_path):
    w = World(tmp_path)
    assert w.go("drafts") == 0
    ev = w.events()
    code = [e for e in ev if e["kind"] == "code_call"]
    pub = [e for e in ev if e["kind"] == "publisher_call"]
    assert {(e["server"], e["tool"]) for e in code} >= {("dk_intel_planner", "grounding_pack")}
    assert all(e["node"].startswith("code:") and "took_ms" in e and e["ok"] in (True, False) for e in code)
    assert {e["method"] for e in pub} >= {"spec", "batch"} and all("payload" not in e for e in pub)
    layers = trace.layers(ev)
    edges = {
        (e["from"], e["to"]) for e in layers["edges"]
    }  # (the test's fake runner writes no agent events: the real one does)
    assert ("orchestrator", "server:dk_intel_planner") in edges and ("orchestrator", "port:publisher") in edges
    assert ("orchestrator", "port:renderer") in edges
    assert any(t["by_code"] for t in layers["tools"]) and layers["renderer"]["renders"] == 30
    ids = [c["id"] for c in trace.calls(ev)]
    assert len(ids) == len(set(ids))  # every call has its own id


def test_one_agent_call_opens_with_what_it_was_given_the_tools_it_used_and_its_answer():
    def ev(ms: int, node: str, kind: str, **kw: Any) -> dict[str, Any]:
        return {"t": "", "ms": ms, "node": node, "kind": kind, **kw}

    events = [
        ev(1, "orchestrator", "start"),
        ev(
            10,
            "copywriter:d01i1",
            "start",
            agent="copywriter",
            model="sonnet",
            tools=["mcp__w__evaluate_drafts"],
            hidden=3,
            task='{"angle": "steam"}',
        ),
        ev(20, "copywriter:d01i1", "tool_call", tool="evaluate_drafts", server="w", input='{"drafts": [1]}'),
        ev(
            60,
            "copywriter:d01i1",
            "tool_result",
            tool="evaluate_drafts",
            is_error=False,
            took_ms=40,
            output='{"ok": true}',
        ),
        ev(70, "copywriter:d01i1", "retry", reason="the answer was not usable"),
        ev(80, "copywriter:d01i1", "thought", text="here is the caption"),
        ev(
            90,
            "copywriter:d01i1",
            "end",
            status="done",
            ms_total=80,
            tokens_in=100,
            tokens_out=20,
            turns=2,
            output={"caption": "hi"},
        ),
    ]
    row = next(c for c in trace.calls(events) if c["type"] == "agent")
    d = trace.call_detail(events, row["id"])
    assert d is not None and d["task"] == {"angle": "steam"} and d["tools_hidden"] == 3
    assert d["tool_calls"] == [{"server": "w", "tool": "evaluate_drafts", "input": {"drafts": [1]}, "at": 20,
                                "output": {"ok": True}, "took_ms": 40, "error": False}]  # fmt: skip
    assert d["thoughts"] == ["here is the caption"] and d["output"] == {"caption": "hi"}
    assert d["retries"] == ["the answer was not usable"] and (d["tokens_in"], d["tokens_out"]) == (100, 20)
    tool = next(c for c in trace.calls(events) if c["type"] == "tool")
    assert trace.call_detail(events, tool["id"])["output"] == {"ok": True}  # a tool call opens on its own too
    assert trace.call_detail(events, "nope#9") is None


def test_the_fixed_structure_comes_from_the_agent_files_and_lists_each_agents_tools():
    topo = trace.topology()
    names = [a["name"] for a in topo["agents"]]
    assert names[:5] == ["strategist", "director", "copywriter", "adapter", "designer"]
    designer = next(a for a in topo["agents"] if a["name"] == "designer")
    assert {"server": "dk_reels_creative", "tool": "preview_spec"} in designer["tools"]
    assert {s["name"] for s in topo["servers"]} >= {"dk_intel_planner", "dk_intel_writer", "dk_reels_creative"}
    assert [p["id"] for p in topo["phases"]] == ["inputs", "strategist", "director", "production", "handover"]


def test_the_log_keeps_a_whole_task_and_the_feed_only_a_line_of_it(tmp_path, monkeypatch):
    log = RunLog(tmp_path / "r", "r")
    big = "x" * 5000
    log.emit("designer:d01i1", "start", agent="designer", task=big)
    assert len(read_events(tmp_path / "r" / "events.jsonl")[0]["task"]) == 5000
    monkeypatch.setattr(console, "DATA", tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "20261013T100000Z").mkdir()
    (tmp_path / "runs" / "20261013T100000Z" / "state.json").write_text("{}")
    (tmp_path / "runs" / "20261013T100000Z" / "events.jsonl").write_text(
        json.dumps({"ms": 1, "node": "n", "kind": "start", "task": big}) + "\n"
    )
    feed = _client().get("/api/runs/20261013T100000Z/events").json()["events"][0]
    assert len(feed["task"]) < 300


# ---------- the machine-learning layer ----------


def _fake_run(root: Path, name: str, verdict: str = "pass") -> None:
    d = root / f"20261011T100000Z_{name}"
    d.mkdir(parents=True)
    (d / "report.json").write_text(json.dumps({
        "version": "x-1", "pull_id": "p1", "market_id": "m1", "split": {"train": 10, "test": 5},
        "judge": {"verdict": verdict, "gain": 0.02, "gates": [{"gate": "data", "status": "pass", "detail": "ok"},
                                                              {"gate": "better", "status": "fail", "detail": "no"}]},
        "challenger": {"model": "logreg", "params": {"C": 1}, "features": "f",
                       "test": {"n": 5, "balanced_accuracy": 0.7}},
        "leaderboard_cv": [{"name": "a", "features": "f", "params": {},
                            "cv_balanced_accuracy@0.5": 0.6}]}))  # fmt: skip
    (d / "insights.json").write_text(json.dumps({"top": ["a", "b"]}))


def test_the_ml_view_reads_the_models_and_steps_from_the_files_the_sunday_run_writes(tmp_path):
    runs, weekly = tmp_path / "runs", tmp_path / "weekly"
    _fake_run(runs, "funnel")
    weekly.mkdir()
    (weekly / "weekly-20261011T100000Z.status.json").write_text(json.dumps({
        "run_id": "r1", "state": "finished", "started_at": "2026-10-11T10:00:00+00:00", "steps": {
            "pull": {"status": "done", "started_at": "2026-10-11T10:00:00+00:00",
                     "finished_at": "2026-10-11T10:00:20+00:00", "note": "40 tables",
                     "details": {"rows": "13871"}}}}))  # fmt: skip
    state = mlview.ml_state(weekly, runs, running=False, history=[], log=[])
    pull = next(s for s in state["steps"] if s["key"] == "pull")
    assert (pull["status"], pull["seconds"], pull["details"]) == ("done", 20.0, {"rows": "13871"})
    assert next(s for s in state["steps"] if s["key"] == "market")["status"] == "pending"
    assert len(state["models"]) == 7 and state["run"]["id"] == "r1" and state["percent"] > 0
    funnel = next(m for m in state["models"] if m["name"] == "funnel")
    assert funnel["run"]["verdict"] == "pass" and [g["status"] for g in funnel["run"]["gates"]] == ["pass", "fail"]
    detail = mlview.model_detail("funnel", runs)
    assert detail and detail["insights_keys"][0]["key"] == "top" and "report.json" in detail["files"]
    assert mlview.model_detail("../../etc/passwd", runs) is None and mlview.insights_text("nope", runs) is None
    stalled = mlview.ml_state(weekly, runs, running=False, history=[], log=[])
    assert stalled["run"]["stalled"] is False  # finished is not stalled


def test_the_ml_demo_walks_every_step_and_ends_with_every_model_judged():
    assert mlview.ml_demo(0)["percent"] == 0.0 and mlview.ml_demo(0)["run"]["running"] is True
    end = mlview.ml_demo(15 * mlview.DEMO_STEP_TICKS + 1)
    assert end["percent"] == 100.0 and all(m["run"]["verdict"] for m in end["models"]) and end["demo"] is True


# ---------- operations: the fixed list, the driver, the guard ----------


def test_the_actions_are_a_fixed_list_and_spending_ones_ask_first():
    base = {"all", "ml.weekly", "agents.drafts", "agents.render", "ml.pull", "ml.market"}
    single = {a for a in ops.ACTIONS if a.startswith("ml.only.")}
    assert set(ops.ACTIONS) == base | single and len(single) == 6  # one per model plug-in, from the code's own plug-ins
    assert [s.id for s in ops.ACTIONS["all"].steps] == ["ml.weekly", "agents.drafts"]
    assert ops.ACTIONS["all"].confirm and ops.ACTIONS["agents.drafts"].confirm and not ops.ACTIONS["ml.pull"].confirm
    assert all(isinstance(part, str) for a in ops.ACTIONS.values() for s in a.steps for part in s.cmd)


def test_the_driver_runs_steps_in_order_stops_at_the_first_failure_and_reports_each(tmp_path):
    seen: list[list[str]] = []
    code = ops.drive("all", tmp_path, run=lambda cmd: (seen.append(cmd), 0)[1])
    assert code == 0 and [c[-1] for c in seen][0].endswith("weekly.sh") and len(seen) == 2
    now = ops.current(tmp_path)
    assert now and now["state"] == "done" and [s["state"] for s in now["steps"]] == ["done", "done"]
    assert ops.drive("ml.pull", tmp_path, run=lambda cmd: 130) == 130
    assert ops.current(tmp_path)["state"] == "stopped" and ops.current(tmp_path)["steps"][0]["state"] == "stopped"
    assert ops.drive("all", tmp_path, run=lambda cmd: 3) == 3
    failed = ops.current(tmp_path)
    assert failed and failed["state"] == "failed"
    assert [(s["state"], s["exit"]) for s in failed["steps"]] == [("failed", 3), ("skipped", None)]


def test_a_driver_that_died_is_shown_as_stopped_and_a_second_start_is_refused(tmp_path, monkeypatch):
    (tmp_path / "current.json").write_text(json.dumps({"state": "running", "pid": 999999, "label": "x", "steps": []}))
    assert ops.current(tmp_path)["state"] == "stopped"  # the process is gone: not "running" forever
    (tmp_path / "current.json").write_text(
        json.dumps({"state": "running", "pid": os.getpid(), "label": "x", "steps": []})
    )
    with pytest.raises(RuntimeError, match="already running"):
        ops.start("ml.pull", tmp_path, lambda: None)
    (tmp_path / "current.json").unlink()
    with pytest.raises(RuntimeError, match="busy"):
        ops.start("ml.pull", tmp_path, lambda: "busy")
    with pytest.raises(KeyError):
        ops.start("rm -rf", tmp_path, lambda: None)


def test_the_console_only_answers_its_own_host_and_only_obeys_posts_with_its_token(tmp_path, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(console_system.ops, "start", lambda a, d, b: (started.append(a), {"started": a})[1])
    monkeypatch.setattr(console_system, "OPS_DIR", tmp_path)
    anon = TestClient(console.app, base_url="http://127.0.0.1:8771")
    assert anon.get("/api/ops").status_code == 200  # reading is fine
    assert anon.post("/api/ops/start", json={"action": "ml.pull"}).status_code == 403
    wrong = TestClient(console.app, base_url="http://127.0.0.1:8771", headers={"x-intel-token": "nope"})
    assert wrong.post("/api/ops/start", json={"action": "ml.pull"}).status_code == 403
    rebind = TestClient(console.app, base_url="http://evil.example.com:8771", headers={"x-intel-token": console.TOKEN})
    assert (
        rebind.get("/api/ops").status_code == 400
        and rebind.post("/api/ops/start", json={"action": "ml.pull"}).status_code == 400
    )
    c = _client()
    assert c.post("/api/ops/start", json={"action": "ml.pull"}).json() == {"started": "ml.pull"}
    assert c.post("/api/ops/start", json={"action": "all"}).status_code == 409  # spends tokens: confirm first
    assert c.post("/api/ops/start", json={"action": "all", "confirm": True}).status_code == 200
    assert c.post("/api/ops/start", json={"action": "nope"}).status_code == 422
    assert started == ["ml.pull", "all"]
    assert (
        c.get("/api/run").status_code in (404, 405)
        and anon.post("/api/run", json={"publish": "render"}).status_code == 403
    )


def test_every_page_has_the_top_bar_and_the_token_and_the_pages_for_the_whole_machine_exist():
    c = _client()
    for url in ("/", "/ops", "/system", "/ml", "/demo", "/live", "/runs", "/guide"):
        html = c.get(url).text
        assert "id=topbar" in html and "intel-token" in html, url
    for name in ("common.js", "system.js", "ml.js", "common.css"):
        assert c.get(f"/static/{name}").status_code == 200
    assert c.get("/static/../console.py").status_code == 404 and c.get("/static/secrets.js").status_code == 404
    demo = c.get("/api/system/demo?tick=60").json()
    assert demo["demo"] and demo["layers"]["agents"]["designer"]["calls"] > 0
    first = next(x["id"] for x in demo["calls"] if x["type"] == "agent")
    assert c.get("/api/system/demo/call", params={"id": first, "tick": 60}).json()["task"]  # ids contain a "#"
    assert (
        c.get("/api/system/agent/designer/prompt").status_code == 200
        and c.get("/api/system/agent/evil/prompt").status_code == 404
    )
    assert c.get("/api/ml/demo?tick=10").json()["demo"] and c.get("/api/ml/model/..%2F..%2Fx").status_code in (404, 422)
    assert telemetry.demo(60)["running"] and TODAY  # the telemetry demo still works with the richer events


# ---------- one model alone, and stopping ----------


def test_one_model_alone_is_a_candidate_run_that_never_uploads_and_never_counts_as_the_weeks_training():
    import datetime as dt

    from intel import weekly

    funnel = ops.ACTIONS["ml.only.funnel"]
    assert funnel.group == "model" and funnel.models == ("funnel",) and not funnel.confirm
    assert funnel.steps[0].cmd[-4:] == ["weekly", "--only", "funnel", "--no-upload"]
    both = ops.ACTIONS["ml.only.train"]
    assert set(both.models) == {"content_scorer", "reach_scorer"}  # the two scorers are trained together
    assert [p.step for p in weekly.select_plugins(["funnel"])] == ["funnel"]
    assert [p.step for p in weekly.select_plugins(["reach_scorer"])] == ["train"]
    assert len(weekly.select_plugins(None) or weekly.discover()) == 6
    with pytest.raises(ValueError, match="unknown model"):
        weekly.select_plugins(["nonsense"])
    catalog = {a["id"]: a for a in ops.catalog()}
    assert catalog["ml.only.funnel"]["models"] == ["funnel"] and catalog["all"]["models"] == []
    # the weekly "done this week" rule ignores a one-model run, and a stopped run is an attempt, not a success
    anchor = dt.datetime(2026, 10, 11, 10, tzinfo=dt.UTC)

    def run_json(tmp: Path, rid: str, **kw: Any) -> None:
        (tmp / "weekly").mkdir(exist_ok=True)
        base = {"run_id": rid, "status": "pass", "started_at": (anchor + dt.timedelta(hours=1)).isoformat(),
                "finished_at": (anchor + dt.timedelta(hours=2)).isoformat(), "dry_run": False}  # fmt: skip
        (tmp / "weekly" / f"weekly-{rid}.run.json").write_text(json.dumps({**base, **kw}))

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        run_json(tmp, "a", only=["funnel"])
        assert weekly.is_due(tmp / "weekly", anchor + dt.timedelta(hours=3))[0]  # still due: it was one model
        run_json(tmp, "b", status="stopped", only=None)
        due, why = weekly.is_due(tmp / "weekly", anchor + dt.timedelta(hours=3))
        assert not due and "did not finish" in why  # not retried straight away ...
        assert weekly.is_due(tmp / "weekly", anchor + dt.timedelta(hours=9))[0]  # ... but it is not "done" either


def test_the_sunday_run_stopped_by_the_owner_says_so_keeps_what_exists_and_skips_the_rest(tmp_path, monkeypatch):
    import intel.weekly as w
    from intel.settings import Settings
    from intel.stopping import Stopped

    def stop_now(*_a: Any, **_k: Any) -> int:
        raise Stopped()

    monkeypatch.setattr(w, "mip_refresh_running", lambda: True)
    monkeypatch.setattr(w, "wait_for_mip", stop_now)
    s = Settings(_env_file=None, intel_data_dir=tmp_path)
    r = w.run_weekly(s, upload=True, log=lambda *_: None)
    assert r["status"] == "stopped" and r["failed_step"] == "wait" and r["error"] == "stopped by the owner"
    st = json.loads((tmp_path / "weekly" / "current.json").read_text())
    assert st["result"] == "stopped" and st["steps"]["wait"]["status"] == "failed"
    assert st["steps"]["knowledge"]["status"] == "skipped" and st["steps"]["report"]["status"] == "skipped"
    assert list((tmp_path / "weekly").glob("weekly-*.run.json")) and not (tmp_path / "weekly" / ".lock").exists()


def test_a_studio_run_that_gets_sigterm_ends_with_stopped_keeps_its_work_and_resumes_without_paying_twice(tmp_path):
    import signal

    from intel.studio import schedule
    from intel.studio.state import RunState

    w = World(tmp_path)
    original = w.runner.run
    count = {"n": 0}

    def dying(node: str, agent: str, task: str, files: Any = None) -> Any:
        count["n"] += 1
        if count["n"] == 12:
            os.kill(os.getpid(), signal.SIGTERM)  # what the Stop button sends
        return original(node, agent, task, files)

    w.runner.run = dying  # type: ignore[method-assign]
    before = signal.getsignal(signal.SIGTERM)
    assert w.go("dry-run", trigger="schedule") == 130
    assert signal.getsignal(signal.SIGTERM) == before  # the handler is put back
    ev = w.events()
    assert ev[-1]["kind"] == "end" and ev[-1]["status"] == "stopped" and ev[-1]["note"] == "stopped by the owner"
    assert not (w.data / ".running").exists() and "STOPPED" in (w.data / "last-summary.md").read_text()
    holds = [h for r in RunState(w.folder / "state.json").data["ideas"].values() for h in r["holds"]]
    assert holds == []  # an agent that was killed by the stop is not a failed idea
    info = schedule.run_info(w.folder)
    assert info is not None and info.finished  # a stop is final: the schedule never restarts it behind your back
    first = [c["node"] for c in w.runner.calls]
    w.runner.run = original  # type: ignore[method-assign]
    assert w.studio.run(Options(publish="dry-run", resume=w.folder.name)) == 0
    assert (
        not set(first) & {c["node"] for c in w.runner.calls[len(first) :]} and w.summary()["ideas"]["completed"] == 30
    )


def test_stop_asks_the_runs_first_then_clears_what_is_left_and_never_touches_its_own_group(tmp_path):
    studio, ml = tmp_path / ".running", tmp_path / "pid"
    studio.write_text("111 20261013T100000Z")
    ml.write_text("222")
    (tmp_path / "ops").mkdir()
    running = {"state": "running", "pid": 333, "label": "x", "steps": []}
    (tmp_path / "ops" / "current.json").write_text(json.dumps(running))
    live = {111, 222, 333}
    log: list[tuple[str, int]] = []
    groups = {111: 900, 222: 901, 333: 902}

    def term(pid: int) -> None:
        log.append(("term", pid))
        if pid in (111, 222):  # the runs stop themselves; the driver would have to be asked again
            live.discard(pid)

    def stop(**over: Any) -> list[str]:
        args: dict[str, Any] = {"alive": lambda p: p in live, "pgid_of": groups.get, "term": term,
                                "term_group": lambda g: log.append(("group", g)),
                                "kill_group": lambda g: log.append(("kill", g)), "sleep": lambda s: None}  # fmt: skip
        return ops.stop_all(studio, ml, tmp_path / "ops", grace_s=2, **{**args, **over})

    assert stop() == ["the studio run", "the ML run", "the operation"]
    assert log[:2] == [("term", 111), ("term", 222)]  # the runs first, so they can write their own ending
    assert ("term", 333) in log and {x for x in log if x[0] == "group"} == {("group", g) for g in (900, 901, 902)}
    assert ("kill", 902) in log  # the driver ignored the polite request: the last resort
    log.clear()
    live.update({111})
    stop(pgid_of=lambda p: os.getpgid(0), term=lambda p: None)
    assert log == []  # this console's own process group is never signalled
    assert ops.stop_all(tmp_path / "none", tmp_path / "none2", tmp_path / "nothing", grace_s=0) == []


def test_the_stop_endpoint_needs_the_token_and_says_when_nothing_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(console_system.ops, "stop_all", lambda *a, **k: [])
    monkeypatch.setattr(console_system, "_ml_paths", lambda: (tmp_path, tmp_path, tmp_path))
    anon = TestClient(console.app, base_url="http://127.0.0.1:8771")
    assert anon.post("/api/ops/stop").status_code == 403
    c = _client()
    assert c.post("/api/ops/stop").status_code == 409
    monkeypatch.setattr(console_system.ops, "stop_all", lambda *a, **k: ["the studio run"])
    assert c.post("/api/ops/stop").json() == {"stopped": ["the studio run"]}
