"""lab/agentic-slice: the engine (orchestrator as code) and the Agent Console, with a stand-in `claude` that emits
the same event stream as Claude Code and a stand-in for the MCP calls the engine makes itself. No model is called and
no server is started; the real run is by hand (the lab README)."""

import importlib.util
import json
import time
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[1] / "lab" / "agentic-slice"


OFFERED = {
    "dk_intel_planner": ["grounding_pack", "upcoming_occasions", "get_facts", "search_posts", "brand_rules"],
    "dk_intel_writer": ["grounding_pack", "evaluate_drafts", "brand_rules", "get_facts", "search_posts"],
    "demo_publisher": ["list_slots", "propose_post"],
    "dk_reels_creative": ["list_templates", "inventory", "search_assets", "search_music", "preview_spec"],
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
    run.reels_contracts = {"content_spec": 1, "manifest": 1}
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
                        item = {"ref": d["ref"], "verdict": "blocked" if "MashaAllah" in d["caption"] else "keep"}
                        if "halal" in d["caption"].lower():  # dk_intel blocks a halal claim without a certifier
                            item["errors"] = [{"code": "HALAL_UNCERTIFIED"}]
                        res.append(item)
                out.append({"results": res})
            elif tool == "propose_post":
                out.append({"proposal_id": "p-" + args["slot_id"]})
            elif tool == "list_templates":
                out.append({"video_categories": ["Eating"], "music_cultures": ["desi"], "music_moods": ["hip-hop"],
                            "templates": {"slides": {"photo": "x"}},
                            "contracts": run.reels_contracts})  # what dk_reels reports  # fmt: skip
            elif tool == "inventory":
                out.append({"video": {"Pot opening": {"n": 3, "star1": 1, "star2": 1}},
                            "photo": {"Eating": {"n": 2, "star1": 0, "star2": 0}}})  # fmt: skip
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


# ---------- media mode: writer -> designer -> checks -> render -> drafts at dk-publishing ----------


@pytest.fixture
def media_world(engine, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    import threading

    import httpx

    from tests.test_lab_handover import KEY, FakePublisher

    fake = FakePublisher()
    original = engine.handover.PublishClient
    monkeypatch.setattr(
        engine.handover,
        "PublishClient",
        lambda url, key: original(url, key, transport=httpx.MockTransport(fake.handler), sleep=lambda s: None),
    )
    monkeypatch.setenv("DK_PUBLISH_URL", "https://publish.test")
    monkeypatch.setenv("DK_PUBLISH_KEY", KEY)
    reels = tmp_path / "reels"
    reels.mkdir()
    (reels / "package.json").write_text("{}")
    monkeypatch.setattr(engine.media, "REELS", reels)
    monkeypatch.setattr(engine.media, "LEDGER", tmp_path / "asset-ledger.json")
    monkeypatch.setattr(engine, "REPO", tmp_path)  # the contract cache and media ledger land here, not in data/
    state = {"calls": [], "active": 0, "max_active": 0, "specs": {}}
    lock = threading.Lock()

    def fake_reels_job(spec_path, *flags, reels=None):  # type: ignore[no-untyped-def]
        spec = json.loads(Path(spec_path).read_text())
        jid, c = spec["job_id"], spec["content"]
        texts = [{"where": "hook", "text": c["hook"]}] + [
            {"where": f"shots[{i}].caption", "text": sh["caption"]}
            for i, sh in enumerate(c["shots"])
            if "caption" in sh
        ]
        texts += [{"where": f"captions.{k}", "text": v} for k, v in spec["captions"].items()]
        assets = [("v-c0001-0000", "c0001"), ("v-c0167-0168", "c0167"), ("v-c0172-0009", "c0172")]
        plan = {"shots": [{"shot": i, "asset": a, "src": f"footage/k/{clip}.mp4", "start": 0}
                          for i, (a, clip) in enumerate(assets)]}  # fmt: skip
        if "--dry-run" in flags:
            state["calls"].append(("dry", jid))
            return 0, {"ok": True, "dry_run": True, "plan": plan, "texts": texts}
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.05)
        out = tmp_path / "reels-out" / jid
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{jid}_9x16.mp4").write_bytes(b"video-" + jid.encode())
        manifest = {  # a complete manifest: tests/test_contracts.py checks this shape against the pinned contract
            "manifest_version": 1, "campaign": spec.get("campaign"), "spec_hash": "h", "created_at": "t",
            "template": "beat-reel", "review": [],
            "job_id": jid, "status": "ready", "kind": "video", "language": spec["language"], "qa": [],
            "files": [{"path": f"{jid}_9x16.mp4", "kind": "video", "format": "9:16", "width": 1080, "height": 1920,
                       "bytes": 1, "sha256": "a" * 64}], "plan": plan,
            "on_screen_text": texts, "captions": spec["captions"], "publish": spec["publish"],
        }  # fmt: skip
        (out / "manifest.json").write_text(json.dumps(manifest))
        state["calls"].append(("render", jid))
        state["specs"][jid] = spec
        with lock:
            state["active"] -= 1
        return 0, {"ok": True, "job_id": jid, "status": "ready", "manifest": str(out / "manifest.json")}

    def fake_thumbnail(manifest, folder, out):  # type: ignore[no-untyped-def]
        out.write_bytes(b"\xff\xd8\xff fake jpeg")
        return True

    monkeypatch.setattr(engine.media, "reels_job", fake_reels_job)
    monkeypatch.setattr(engine.media, "thumbnail", fake_thumbnail)
    return engine, fake, state


def _end(engine, node):  # type: ignore[no-untyped-def]
    return next(e for e in _events(engine) if e["node"] == node and e["kind"] == "end")


def test_media_mode_designs_renders_and_hands_over_drafts_with_code_in_charge(media_world, tmp_path):
    engine, fake, state = media_world
    assert engine.orchestrate("instagram", "manual", publish="drafts") == 0
    ev = _events(engine)
    # a designer only for each caption that passed; each starts after its own writer ended
    starts = {e["node"]: e["ms"] for e in ev if e["kind"] == "start"}
    ends = {e["node"]: e["ms"] for e in ev if e["kind"] == "end"}
    assert {"designer-1", "designer-2", "designer-3", "renderer", "handover"} <= starts.keys()
    assert all(starts[f"designer-{i}"] >= ends[f"helper-{i}"] for i in (1, 2, 3))
    # renders are one at a time, after every designer is done
    assert state["max_active"] == 1 and len([c for c in state["calls"] if c[0] == "render"]) == 3
    assert starts["renderer"] >= max(ends[f"designer-{i}"] for i in (1, 2, 3))
    # the drafts exist at the publisher, one per slot, with the writer's caption and the code-set slot
    assert {k.rsplit("-", 1)[1] for k in fake.posts} == {"s1", "s2", "s3"}  # one draft per slot
    for key, post in fake.posts.items():
        (variant,) = post["variants"]
        assert variant["platform"] == "instagram" and variant["format"] == "reel"
        assert variant["caption"] == "Samstag gibt es Kacchi in Berlin." and variant["slot"].startswith("20")
        assert key.startswith("20") and "-ig-" in key
    # what the designer wrote for captions, platforms and time was ignored: code set them
    for spec in state["specs"].values():
        assert spec["captions"] == {"instagram": "Samstag gibt es Kacchi in Berlin."}
        assert spec["publish"]["platforms"] == ["instagram"] and not spec["publish"]["at"].startswith("2020")
        assert spec["content"]["formats"] == ["9:16"] and spec["content"]["template"] == "beat-reel"
    end = ev[-1]
    assert end["node"] == "orchestrator" and end["status"] == "done" and end["proposed"] == 3
    assert _end(engine, "handover")["output"]["submitted"] and _end(engine, "renderer")["tokens_in"] == 0
    # the footage used is remembered, and the next spec of the same run already avoids the earlier ones
    ledger = json.loads((tmp_path / "asset-ledger.json").read_text())
    assert len(ledger) == 3 and "c0167" in ledger[0]["assets"]
    specs = list(state["specs"].values())
    assert "avoid_assets" not in specs[0] and "v-c0001-0000" in specs[1]["avoid_assets"]


def test_the_designer_gets_only_its_three_creative_tools_and_a_complete_brief(media_world, tmp_path):
    engine, _, _ = media_world
    engine.orchestrate("instagram", "manual", publish="drafts")
    calls = [json.loads(line) for line in (tmp_path / "args.jsonl").read_text().splitlines()]
    designers = [c for c in calls if "preview_spec" in ",".join(c["tools"])]
    assert len(designers) == 3 and all(c["builtins"] == "" for c in designers)
    assert designers[0]["tools"] == [
        f"mcp__dk_reels_creative__{t}" for t in ("search_assets", "search_music", "preview_spec")
    ]
    assert sorted(designers[0]["hidden"]) == [
        "mcp__dk_reels_creative__inventory",
        "mcp__dk_reels_creative__list_templates",
    ]
    assert not any("render" in t for c in designers for t in c["tools"] + c["hidden"])  # an agent can never render
    brief = json.loads(designers[0]["task"])
    assert brief["make"] == "video" and brief["caption"] and brief["angle"] and brief["evidence"] is not None
    assert brief["footage_categories"] == {"Pot opening": "3, 1 key moments"} and brief["music"]["cultures"] == ["desi"]


def test_dry_run_checks_with_the_publisher_and_creates_nothing(media_world):
    engine, fake, _ = media_world
    assert engine.orchestrate("instagram", "manual", publish="dry-run") == 0
    assert not fake.posts
    assert len(_end(engine, "handover")["output"]["would_submit"]) == 3


def test_a_slot_that_already_has_a_post_is_skipped_before_any_ai_is_spent(media_world):
    engine, fake, state = media_world
    engine.orchestrate("instagram", "manual", publish="drafts")
    for p in engine.RUNS.glob("*.jsonl"):
        p.rename(p.with_suffix(".old"))
    renders = len(state["calls"])
    assert engine.orchestrate("instagram", "manual", publish="drafts") == 0
    ev = _events(engine)
    assert {e["node"] for e in ev if e["kind"] == "start"} == {"orchestrator", "planner"}  # no writer, no designer
    assert len(state["calls"]) == renders and len(fake.posts) == 3  # nothing rendered, nothing duplicated
    assert ev[-1]["status"] == "done" and ev[-1]["skipped"] == 3 and "already" in ev[-1]["note"]


def test_on_screen_text_that_fails_the_brand_check_is_never_rendered(media_world, monkeypatch):
    engine, fake, state = media_world
    monkeypatch.setenv("FAKE_HALAL_SLOT", "s2")
    engine.orchestrate("instagram", "manual", publish="drafts")
    assert sorted(fake.posts) == [k for k in sorted(fake.posts) if not k.endswith("-s2")] and len(fake.posts) == 2
    assert not any(c[1].endswith("-s2") for c in state["calls"] if c[0] == "render")
    why = next(s for s in _skipped(engine) if s["slot_id"] == "s2")["why"]
    assert "brand check" in why and "HALAL_UNCERTIFIED" in why


def _skipped(engine):  # type: ignore[no-untyped-def]
    text = (engine.RUNS / f"{_run_id(engine)}.md").read_text()
    return [{"slot_id": ln.split(":")[0][2:], "why": ln} for ln in text.splitlines() if "skipped (" in ln]


def test_a_design_of_the_wrong_kind_is_skipped_with_its_reason(media_world, monkeypatch):
    engine, fake, _ = media_world
    monkeypatch.setenv("FAKE_WRONG_KIND_SLOT", "s3")
    engine.orchestrate("instagram", "manual", publish="drafts")
    assert len(fake.posts) == 2
    assert "needs a video spec" in next(s for s in _skipped(engine) if s["slot_id"] == "s3")["why"]


def test_a_rewrite_caption_gets_no_designer(media_world, monkeypatch):
    engine, _, _ = media_world
    monkeypatch.setenv("FAKE_REWRITE_SLOT", "s3")
    engine.orchestrate("instagram", "manual", publish="drafts")
    designers = {e["node"] for e in _events(engine) if e["kind"] == "start" and e["node"].startswith("designer")}
    assert designers == {"designer-1", "designer-2"}


def test_media_mode_refuses_to_start_without_a_key_and_spends_nothing(media_world, monkeypatch):
    engine, _, _ = media_world
    monkeypatch.delenv("DK_PUBLISH_KEY")
    monkeypatch.setattr(engine.handover, "load_env", lambda *a, **k: {})
    assert engine.orchestrate("instagram", "manual", publish="drafts") == 1
    ev = _events(engine)
    assert {e["node"] for e in ev if e["kind"] == "start"} == {"orchestrator"} and "DK_PUBLISH_KEY" in ev[-1]["error"]
    assert not engine.LOCK.exists()


def test_the_default_mode_is_unchanged_and_never_touches_the_reels_engine(engine, monkeypatch):
    monkeypatch.setattr(engine.media, "reels_job", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no render")))
    assert engine.orchestrate("instagram", "manual") == 0
    assert not any(s == "dk_reels_creative" for s, _, _ in engine.mcp_log)


def test_render_mode_needs_no_key_and_sends_nothing_anywhere(media_world, monkeypatch):
    engine, fake, state = media_world
    monkeypatch.delenv("DK_PUBLISH_KEY")
    monkeypatch.setattr(engine.handover, "load_env", lambda *a, **k: {})
    assert engine.orchestrate("instagram", "manual", publish="render") == 0
    ev = _events(engine)
    assert not fake.calls and not fake.posts  # the publisher was never contacted, not even to look
    assert len([c for c in state["calls"] if c[0] == "render"]) == 3
    assert not any(e["node"] == "handover" for e in ev) and ev[-1]["status"] == "done" and ev[-1]["proposed"] == 3
    assert "proposal 2026w" in (engine.RUNS / f"{_run_id(engine)}.md").read_text()  # the report names each rendered job


def test_the_console_shows_a_media_run_with_the_picture_the_idea_and_what_became_of_each_post(media_world, monkeypatch):
    from fastapi.testclient import TestClient

    engine, _, _ = media_world
    console = _load("console")
    monkeypatch.setattr(console, "RUNS", engine.RUNS)
    monkeypatch.setenv("FAKE_HALAL_SLOT", "s2")
    engine.orchestrate("instagram", "manual", publish="drafts")
    c = TestClient(console.app)
    (run,) = c.get("/api/runs").json()
    posts = {p["slot_id"]: p for p in c.get(f"/api/runs/{run['id']}/result").json()["posts"]}
    s1 = posts["s1"]
    assert (s1["status"], s1["state"]) == ("proposed", "submitted") and "DRAFT" in s1["why"]
    assert s1["design"]["hook"] == "Sealed. Then opened." and s1["design"]["why"] == "the lid coming off"
    assert s1["job_id"].endswith("-s1")
    thumb = c.get(s1["thumb"])
    assert (
        thumb.status_code == 200
        and thumb.content.startswith(b"\xff\xd8")
        and thumb.headers["content-type"] == "image/jpeg"
    )
    assert posts["s2"]["status"] == "skipped" and "brand check" in posts["s2"]["why"] and posts["s2"]["thumb"] is None
    assert c.get(f"/api/runs/{run['id']}/thumb/..%2F..%2Fx").status_code == 404  # no path tricks
    assert c.get(f"/api/runs/{run['id']}/thumb/2026w42-ig-nothing-s9").status_code == 404
    page = c.get("/").text
    assert "const MEDIA=" in page and "dk_reels_creative" in page and "designer-1" in page  # the media layout exists


def test_an_older_demo_run_still_shows_as_before_in_the_console(engine, monkeypatch):
    from fastapi.testclient import TestClient

    console = _load("console")
    monkeypatch.setattr(console, "RUNS", engine.RUNS)
    engine.orchestrate("instagram", "manual")
    c = TestClient(console.app)
    (run,) = c.get("/api/runs").json()
    posts = c.get(f"/api/runs/{run['id']}/result").json()["posts"]
    assert all(
        p["status"] == "proposed" and p["state"] is None and p["thumb"] is None and p["design"] is None for p in posts
    )
    assert "proposals.json" in posts[0]["why"]


def test_a_reels_engine_speaking_another_contract_stops_the_run_before_any_ai(engine, tmp_path):
    engine.reels_contracts = {"content_spec": 2, "manifest": 1}
    assert engine.orchestrate("instagram", "manual", publish="render") == 1
    end = _events(engine)[-1]
    assert end["status"] == "failed" and "intel contracts sync" in end["error"]
    assert not (tmp_path / "args.jsonl").exists()  # no agent ran
