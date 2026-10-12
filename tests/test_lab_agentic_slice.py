"""lab/agentic-slice: the engine (orchestrator as code) and the Agent Console, with a stand-in `claude` that emits
the same event stream as Claude Code and a stand-in for the MCP calls the engine makes itself. No model is called and
no server is started; the real run is by hand (the lab README)."""

import importlib.util
import json
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[1] / "lab" / "agentic-slice"


OFFERED = {
    "dk_intel_planner": ["grounding_pack", "upcoming_occasions", "get_facts", "search_posts", "brand_rules"],
    "dk_intel_writer": ["grounding_pack", "evaluate_drafts", "brand_rules", "get_facts", "search_posts"],
    "demo_publisher": ["list_slots", "propose_post"],
}
PACK = {
    "competitor_gaps": [{"aspect": "delivery_time", "quote": {"cite": "R-AAAAAAAA", "text": "Essen kam zu spät"}}],
    "our_best_posts": [
        {"cite": "P-GOOD0001", "text": "Mutton Kacchi, slow dum, Berlin", "perf": {"vs_our_median": 3.5}},
        {"cite": "P-BLOCKED1", "text": "Review from a little one MashaAllah", "perf": {"vs_our_median": 9.0}},
    ],
    "opportunities": [{"our_posts": [{"cite": "P-GOOD0002", "text": "Smokey rice", "vs_our_median": 5.0}]}],
    "brand_reminders": ["always say: Kacchi, Berlin"],
    "prov": {"source": "test"},
}
SLOTS = [{"id": f"s{i}", "platform": "instagram", "day": d, "time": "17:00", "format": "reel"}
         for i, d in enumerate(["Tue", "Thu", "Sat"], start=1)]  # fmt: skip


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
    monkeypatch.setattr(run, "_OFFERED", {})
    run.mcp_log = []
    run.slot_fails = set()  # slots whose probe the fake scorer puts below the bar

    def fake_session(server, calls):  # type: ignore[no-untyped-def]
        run._OFFERED[server] = OFFERED[server]
        out = []
        for tool, args in calls:
            run.mcp_log.append((server, tool, args))
            if tool == "list_slots":
                out.append({"slots": SLOTS})
            elif tool == "grounding_pack":
                out.append(json.loads(json.dumps(PACK)))
            elif tool == "evaluate_drafts":
                res = []
                for d in args["drafts"]:
                    if d["ref"][1:] in run.slot_fails and d["caption"] == run.PROBE:
                        res.append({"ref": d["ref"], "verdict": "rewrite", "errors": [], "warnings": [],
                                    "scores": {"content_scorer": {"probability": 0.45, "above": False}}})  # fmt: skip
                    else:
                        res.append({"ref": d["ref"], "verdict": "blocked" if "MashaAllah" in d["caption"] else "keep"})
                out.append({"results": res})
            elif tool == "propose_post":
                out.append({"proposal_id": "p-" + args["slot_id"]})
        return out

    monkeypatch.setattr(run, "mcp_session", fake_session)
    monkeypatch.setenv("FAKE_ARGS_LOG", str(tmp_path / "args.jsonl"))
    monkeypatch.setenv("FAKE_DELAY_S", "0.4")
    return run


def _events(engine):  # type: ignore[no-untyped-def]
    (log,) = list(engine.RUNS.glob("*.jsonl"))
    return [json.loads(line) for line in log.read_text().splitlines()]


def _run_id(engine):  # type: ignore[no-untyped-def]
    return next(engine.RUNS.glob("*.jsonl")).stem


def test_code_fetches_ai_plans_and_writes_in_parallel_code_checks_and_proposes(engine):
    assert engine.orchestrate("instagram", "manual") == 0
    ev = _events(engine)
    assert [e["ms"] for e in ev] == sorted(e["ms"] for e in ev)  # one timeline, never overwritten by event data
    starts = [e["node"] for e in ev if e["kind"] == "start"]
    assert starts[:2] == ["orchestrator", "planner"] and starts[-1] == "checker"
    assert set(starts[2:5]) == {"helper-1", "helper-2", "helper-3"}
    windows = {e["node"]: [e["ms"]] for e in ev if e["kind"] == "start"}
    for e in ev:
        if e["kind"] == "end" and e["node"] in windows:
            windows[e["node"]].append(e["ms"])
    h = [windows[f"helper-{i}"] for i in (1, 2, 3)]
    assert max(s for s, _ in h) < min(end for _, end in h)  # all three were running at the same moment
    end = ev[-1]
    assert end["node"] == "orchestrator" and end["status"] == "done" and end["proposed"] == 3
    # the fixed steps are code: fetching before the planner, re-check and proposals after the writers
    tools = [(s, t) for s, t, _ in engine.mcp_log]
    assert set(tools[:2]) == {("demo_publisher", "list_slots"), ("dk_intel_planner", "grounding_pack")}
    assert tools[-4:] == [("dk_intel_writer", "evaluate_drafts")] + [("demo_publisher", "propose_post")] * 3
    checker = next(e for e in ev if e["node"] == "checker" and e["kind"] == "end")
    assert checker["tokens_in"] == 0 and checker["output"]["proposed"][0]["proposal_id"].startswith("p-")
    assert all(e["tool"] != "?" for e in ev if e["kind"] == "tool_result")  # every result is matched to its call
    assert not engine.LOCK.exists() and (engine.RUNS / f"{_run_id(engine)}.md").exists()


def test_each_agent_gets_only_what_its_job_needs(engine, tmp_path):
    engine.orchestrate("instagram", "manual")
    calls = [json.loads(line) for line in (tmp_path / "args.jsonl").read_text().splitlines()]
    assert all(c["builtins"] == "" for c in calls)  # no shell, web or file tools for any agent
    assert all(c["tool_search"] == "false" for c in calls)  # else the MCP tools hide behind the removed ToolSearch
    assert all(not Path(c["cwd"]).resolve().is_relative_to(LAB.parents[1]) for c in calls)  # no CLAUDE.md, no memory
    planner, *helpers = calls
    assert len(helpers) == 3
    assert planner["tools"] == [] and planner["hidden"] == []  # its data is in the task: no tools, one turn
    assert json.loads(planner["task"])["grounding_pack"]["competitor_gaps"] and "prov" not in planner["task"]
    assert all(h["tools"] == ["mcp__dk_intel_writer__evaluate_drafts"] for h in helpers)
    assert sorted(helpers[0]["hidden"]) == sorted(f"mcp__dk_intel_writer__{t}" for t in OFFERED["dk_intel_writer"]
                                                   if t != "evaluate_drafts")  # fmt: skip
    cfgs = {p.stem.removesuffix(".mcp"): json.loads(p.read_text()) for p in engine.RUNS.glob("*/*.mcp.json")}
    assert cfgs["planner"]["mcpServers"] == {} and set(cfgs["helper-1"]["mcpServers"]) == {"dk_intel_writer"}
    brief = json.loads(helpers[0]["task"])
    assert brief["planned_posted_at"].endswith(("+01:00", "+02:00")) and brief["content_type"] == "reel"
    assert brief["evidence"] == [{"cite": "R-AAAAAAAA", "text": "Essen kam zu spät"}]  # its own evidence only
    # examples: our best posts, best first, and never one that dk_intel's own check blocks
    assert [e["cite"] for e in brief["examples"]] == ["P-GOOD0002", "P-GOOD0001"]


def test_a_failed_helper_is_reported_and_the_run_finishes(engine, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_SLOT", "s2")
    assert engine.orchestrate("instagram", "manual") == 0
    ev = _events(engine)
    failed = [e for e in ev if e["kind"] == "end" and e.get("status") == "failed"]
    assert [e["node"] for e in failed] == ["helper-2"] and ev[-1]["proposed"] == 2 and ev[-1]["helpers_failed"] == 1


def test_only_keep_is_ever_proposed(engine, monkeypatch):
    monkeypatch.setenv("FAKE_REWRITE_SLOT", "s3")
    engine.orchestrate("instagram", "manual")
    proposed = [a["slot_id"] for s, t, a in engine.mcp_log if t == "propose_post"]
    rechecked = [d["ref"] for s, t, a in engine.mcp_log if t == "evaluate_drafts" for d in a["drafts"]]
    assert sorted(proposed) == ["s1", "s2"] and "s3" not in rechecked
    assert "rewrite" in (engine.RUNS / f"{_run_id(engine)}.md").read_text()


def test_frozen_inputs_replay_without_fetching(engine, tmp_path):
    frozen = tmp_path / "week.json"
    engine.orchestrate("instagram", "manual", save_inputs=frozen)
    for p in engine.RUNS.glob("*.jsonl"):
        p.rename(p.with_suffix(".old"))
    engine.mcp_log.clear()
    assert engine.orchestrate("instagram", "eval", inputs=frozen) == 0
    assert not {t for _, t, _ in engine.mcp_log} & {"list_slots", "grounding_pack"}
    assert _events(engine)[0]["inputs"] == str(frozen)


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
    assert summary["proposed"] == 3 and summary["tool_calls"] >= 9 and summary["tokens"] == 4 * 1500
    body = c.get(f"/api/runs/{summary['id']}/stream").text
    assert body.count("data: ") >= 10 and "event: done" in body
    assert "Run now" in c.get("/").text and c.get("/api/runs/..%2Fx/stream").status_code == 404
    engine.LOCK.write_text("busy")
    assert c.post("/api/run", json={"platform": "instagram"}).status_code == 409  # the button is refused mid-run
    assert c.post("/api/run", json={"platform": "tiktok"}).status_code == 422


def test_the_result_view_shows_each_post_and_what_became_of_it(engine, monkeypatch):
    from fastapi.testclient import TestClient

    console = _load("console")
    monkeypatch.setattr(console, "RUNS", engine.RUNS)
    monkeypatch.setenv("FAKE_FAIL_SLOT", "s2")
    engine.orchestrate("instagram", "button")
    c = TestClient(console.app)
    (run,) = c.get("/api/runs").json()
    r = c.get(f"/api/runs/{run['id']}/result").json()
    assert r["run"]["platform"] == "instagram" and r["run"]["status"] == "done"
    by = {p["slot_id"]: p for p in r["posts"]}
    assert [by[s]["status"] for s in ("s1", "s2", "s3")] == ["proposed", "failed", "proposed"]
    s1 = by["s1"]
    assert s1["caption"] and s1["angle"] == "rice" and s1["score"] == 0.6 and s1["tried"] == 3 and s1["date"]
    assert "p-s1" in s1["why"] and by["s2"]["caption"] is None and "What it produced" in c.get("/").text


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


def test_eval_measures_quality_and_cost_per_version(engine, monkeypatch, tmp_path, capsys):
    engine.orchestrate("instagram", "eval")
    monkeypatch.syspath_prepend(str(LAB))
    ev_mod = _load("eval")
    monkeypatch.setattr(ev_mod.console, "RUNS", engine.RUNS)
    monkeypatch.setattr(ev_mod, "EVAL", tmp_path / "eval")
    monkeypatch.setattr(ev_mod, "RESULTS", tmp_path / "eval" / "results.jsonl")
    row = ev_mod.measure(_run_id(engine), "v3")
    assert (row["proposed"], row["posts"], row["mean_score"], row["tokens"]) == (3, 3, 0.6, 6000)
    ev_mod.show()
    assert "v3" in capsys.readouterr().out.splitlines()[-1]


def test_a_slot_no_caption_can_save_gets_no_writer(engine, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    engine.slot_fails.add("s3")
    assert engine.orchestrate("instagram", "manual") == 0
    ev = _events(engine)
    assert {e["node"] for e in ev if e["kind"] == "start"} == {"orchestrator", "planner", "helper-1", "helper-2",
                                                               "checker"}  # fmt: skip
    planner_task = json.loads(json.loads((tmp_path / "args.jsonl").read_text().splitlines()[0])["task"])
    assert [s["slot_id"] for s in planner_task["slots"]] == ["s1", "s2"]  # the planner never sees the lost slot
    assert ev[-1]["proposed"] == 2 and ev[-1]["skipped"] == 1
    console = _load("console")
    monkeypatch.setattr(console, "RUNS", engine.RUNS)
    posts = TestClient(console.app).get(f"/api/runs/{_run_id(engine)}/result").json()["posts"]
    s3 = next(p for p in posts if p["slot_id"] == "s3")
    assert s3["status"] == "not_planned" and "45%" in s3["why"] and s3["caption"] is None
