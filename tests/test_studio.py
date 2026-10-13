"""The content studio (ADR 0034): settings as data, real-date slots, budgets, and the whole flow from a plan to 150
drafts with fake agents, renderer and tools, and a fake publisher that enforces the real contract. No model, no
render."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from intel.studio import config as sc
from intel.studio import handover, slots
from intel.studio.budget import Budget
from intel.studio.inputs import chunks
from intel.studio.run import Options, Studio
from tests.studio_fakes import NOW, TODAY, FakeRenderer, FakeRunner, FakeTools, fake_publisher
from tests.test_contracts import errors, validator

SHIPPED = sc.load()


# ---------- settings as data ----------


def test_the_shipped_settings_load_and_describe_150_posts():
    p = SHIPPED.plan
    assert (p.horizon_days, p.ideas_per_day, len(p.platforms)) == (10, 3, 5)
    assert p.horizon_days * p.ideas_per_day * len(SHIPPED.platforms_for("reel")) == 150
    assert SHIPPED.platforms_for("carousel") == [
        "instagram",
        "facebook",
    ]  # TikTok, YouTube: video only; Threads: 1 image
    assert SHIPPED.platforms_for("image") == ["instagram", "facebook", "threads"]
    assert SHIPPED.routes_table()[("video", "9:16")]["tiktok"] == "video"
    assert SHIPPED.handover_cfg()["defaults"]["tiktok"] == {"privacy_level": "only_me"}  # safe until the owner decides
    assert SHIPPED.budgets.max_tokens_per_run == 4_000_000


def _write(tmp_path: Path, **patch: Any) -> Path:
    d = tmp_path / "studio"
    d.mkdir()
    for name in ("plan", "slots", "routes", "budgets"):
        data = yaml.safe_load((sc.CONFIG_DIR / f"{name}.yaml").read_text())
        data.update(patch.get(name, {}))
        (d / f"{name}.yaml").write_text(yaml.safe_dump(data))
    return d


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"plan": {"horizon_dayz": 10}}, "horizon_dayz"),  # a typo is refused, never ignored
        ({"plan": {"ideas_per_day": 4}}, "ideas_per_day"),
        ({"plan": {"primary_platform": "linkedin"}}, "not in platforms"),
        ({"plan": {"format_mix": ["podcast"]}}, "format_mix"),
        ({"slots": {"times": {"instagram": {"lunch": "11:30", "pre_dinner": "5pm", "evening": "20:00"}}}}, "HH:MM"),
        ({"budgets": {"max_turns": {"strategist": 3}}}, "max_turns"),
    ],
)
def test_a_wrong_settings_file_stops_the_run_with_the_line_to_fix(tmp_path, patch, message):
    with pytest.raises(sc.StudioConfigError, match=message):
        sc.load(_write(tmp_path, **patch))


def test_a_missing_window_time_for_a_platform_is_refused(tmp_path):
    times = yaml.safe_load((sc.CONFIG_DIR / "slots.yaml").read_text())["times"]
    del times["tiktok"]["evening"]
    with pytest.raises(sc.StudioConfigError, match="tiktok has no time"):
        sc.load(_write(tmp_path, slots={"times": times}))


# ---------- real dates, windows, keys ----------


def test_the_plan_uses_real_dates_with_the_right_offset_across_the_clock_change():
    days = slots.plan_days(SHIPPED, dt.date(2026, 10, 21))
    assert days[0] == dt.date(2026, 10, 22) and len(days) == 10  # no weekday names: ten real dates from tomorrow
    assert slots.clock(SHIPPED, "instagram", "pre_dinner", dt.date(2026, 10, 24)) == "2026-10-24T17:00:00+02:00"
    assert slots.clock(SHIPPED, "instagram", "pre_dinner", dt.date(2026, 10, 26)) == "2026-10-26T17:00:00+01:00"


def test_the_ideas_of_ten_days_get_distinct_valid_post_keys_and_a_time_per_platform():
    days = slots.plan_days(SHIPPED, TODAY)
    scores = slots.read_scores(slots.probes(SHIPPED, days), [])  # no answer from the scorer: every slot may pass
    ideas, dropped = slots.assign(SHIPPED, days, scores)
    assert len(ideas) == 30 and not dropped
    keys = [i.job_id for i in ideas]
    assert len(set(keys)) == 30 and all(re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$", k) for k in keys)
    assert keys[0] == "2026w42-tue13-i1"
    first_day = [i for i in ideas if i.date == dt.date(2026, 10, 13)]
    assert sorted(i.window for i in first_day) == ["evening", "lunch", "pre_dinner"]  # one idea per window
    assert all(set(i.slots) == set(SHIPPED.plan.platforms) for i in ideas)
    assert slots.job_id(dt.date(2026, 10, 13), 1, "R-2") == "2026w42-tue13-i1-r2"  # a redo needs a new key


def test_the_scorer_decides_the_window_and_an_idea_that_passes_nowhere_is_reported_not_written():
    days = slots.plan_days(SHIPPED.with_plan(horizon_days=2), TODAY)
    items = slots.probes(SHIPPED, days)
    low = {
        slots.probe_ref(p) for p in items if p[0] == days[0] and p[2] == "pre_dinner"
    }  # day 1: the best window fails
    results = [{"ref": slots.probe_ref(p), "verdict": "rewrite" if slots.probe_ref(p) in low else "keep",
                "scores": {"content_scorer": {"probability": 0.4 if slots.probe_ref(p) in low else 0.6}}}
               for p in items]  # fmt: skip
    ideas, dropped = slots.assign(SHIPPED, days, slots.read_scores(items, results))
    # three ideas need all three windows, so one of day 1's ideas must take the failing one and is reported
    assert [d.date for d in dropped] == [days[0]] and "40%" in dropped[0].why
    assert sorted(i.window for i in ideas if i.date == days[0]) == ["evening", "lunch"] and len(ideas) == 5
    everywhere_low = [{**r, "verdict": "rewrite", "scores": {"content_scorer": {"probability": 0.3}}} for r in results]
    ideas, dropped = slots.assign(SHIPPED, days, slots.read_scores(items, everywhere_low))
    assert not ideas and len(dropped) == 6 and "30%" in dropped[0].why


def test_chunks_never_exceed_twenty_and_lose_nothing():
    parts = chunks(list(range(150)))
    assert [len(p) for p in parts] == [20] * 7 + [10] and sum(parts, []) == list(range(150))


# ---------- budgets ----------


def test_the_budget_stops_new_calls_at_the_cap_and_alerts_once():
    b = Budget(max_tokens=1000, alert_at=0.8, max_calls_per_idea=2)
    assert b.allow("i1") is None
    assert b.charge(700, "i1") is False and b.charge(150, "i1") is True and b.charge(10) is False  # alert fires once
    assert "2 AI calls" in (b.allow("i1") or "") and b.allow("i2") is None
    b.charge(200, "i2")
    assert "token budget" in (b.allow("i2") or "")


# ---------- the whole flow ----------


class World:
    def __init__(self, tmp_path: Path, cfg: sc.StudioConfig = SHIPPED, publisher: bool = True) -> None:
        self.tools, self.runner = FakeTools(), FakeRunner()
        self.renderer = FakeRenderer(tmp_path / "reels-out")
        self.client, self.fake = fake_publisher() if publisher else (None, None)
        self.data = tmp_path / "data"
        self.cfg = cfg
        self.studio = Studio(cfg=cfg, tools=self.tools, make_runner=self._runner, renderer=self.renderer,
                             publisher=self.client, data_dir=self.data, now=lambda: NOW,
                             stay_awake=contextlib.nullcontext)  # fmt: skip

    def _runner(self, log: Any) -> FakeRunner:
        return self.runner

    def go(self, publish: str = "dry-run", **kw: Any) -> int:
        return self.studio.run(Options(publish=publish, today=TODAY, **kw))

    @property
    def folder(self) -> Path:
        (run,) = sorted((self.data / "runs").iterdir())
        return run

    def summary(self) -> dict[str, Any]:
        return json.loads((self.folder / "summary.json").read_text())

    def result(self) -> list[dict[str, Any]]:
        return json.loads((self.folder / "result.json").read_text())

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(x) for x in (self.folder / "events.jsonl").read_text().splitlines()]


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_ten_days_become_thirty_ideas_and_150_posts_all_valid_at_the_publisher(world):
    world.runner.delay = 0.01
    assert world.go("dry-run") == 0
    s = world.summary()
    assert s["ideas"] == {"planned": 30, "dropped_by_scorer": 0, "completed": 30, "held": 0}
    assert s["variants"]["planned"] == s["variants"]["made"] == 150 and s["variants"]["held_reasons"] == {}
    assert len(s["handover"]["would_submit"]) == 30 and not s["handover"]["failed"] and not s["handover"]["held"]
    assert not world.fake.posts  # a dry run creates nothing
    # a step that checks many items never sends more than dk_intel takes
    evals = [a["drafts"] for _, t, a in world.tools.calls if t == "evaluate_drafts"]
    assert max(len(d) for d in evals) <= 20 and sum(len(d) for d in evals) > 150
    # AI work: 3 strategist chunks, then per idea a copywriter, an adapter and a designer, and nothing else
    by_agent = Counter_(world.runner.calls)
    assert by_agent == {"strategist": 3, "copywriter": 30, "adapter": 30, "designer": 30}
    assert s["tokens"]["total"] == 93 * 1200 and s["tokens"]["per_idea"] == 3720
    # renders are strictly one at a time, ideas are written side by side
    assert world.renderer.max_active == 1 and 1 < world.runner.max_active <= SHIPPED.budgets.parallel_ideas


def test_drafts_mode_creates_one_post_per_idea_with_a_native_variant_per_platform(world):
    assert world.go("drafts") == 0
    assert len(world.fake.posts) == 30 and len(world.summary()["handover"]["submitted"]) == 30
    post = world.fake.posts["2026w42-tue13-i1"]
    by = {v["platform"]: v for v in post["variants"]}
    assert set(by) == {"instagram", "facebook", "tiktok", "youtube", "threads"}
    assert by["instagram"]["format"] == "reel" and by["facebook"]["format"] == "video"
    assert (
        "instagram Kacchi Berlin d01i" in by["instagram"]["caption"]
        and by["threads"]["caption"] != by["instagram"]["caption"]
    )
    assert by["youtube"]["options"]["title"].startswith("Kacchi in Berlin") and "caption" not in by["youtube"]
    assert by["youtube"]["options"]["description"] and by["youtube"]["options"]["made_for_kids"] == "no"
    assert by["tiktok"]["options"] == {"privacy_level": "only_me"}  # the adapter's "public" was dropped: code's default
    assert len({v["slot"] for v in post["variants"]}) == 5  # each platform has its own minute in the window
    assert all(
        len(v["media"]) == 1 and v["media"][0] == by["instagram"]["media"][0] for v in post["variants"]
    )  # one master
    assert len(world.fake.media) == 30  # one upload per master, each once


def test_every_spec_sent_to_the_renderer_is_a_valid_content_spec_with_all_platforms(world):
    world.go("render")
    v = validator("reels.content_spec")
    specs = sorted(world.folder.glob("*.spec.json"))
    assert len(specs) == 30
    for path in specs:
        spec = json.loads(path.read_text())
        assert not errors(v, spec), path.name
        assert set(spec["publish"]["slots"]) == set(SHIPPED.plan.platforms) and spec["publish"]["at"] == min(
            spec["publish"]["slots"].values()
        )
        assert "instagram" in spec["captions"] and "youtube" not in spec["captions"]
        assert (
            spec["captions"]["instagram"] != "must be ignored" and spec["publish"]["at"] != "2020-01-01T00:00:00+00:00"
        )
    assert not world.fake.posts and not any(m == "POST" for m, _ in world.fake.calls)  # render mode sends nothing


def test_carousels_reach_only_the_platforms_that_take_slides(tmp_path):
    w = World(tmp_path, SHIPPED.with_plan(horizon_days=2, format_mix=["reel", "carousel", "image"]))
    assert w.go("drafts") == 0
    s = w.summary()
    assert s["variants"] == {"planned": 2 * (5 + 2 + 3), "made": 20, "held_reasons": {}}
    carousel = w.fake.posts["2026w42-tue13-i2"]
    assert {v["platform"]: v["format"] for v in carousel["variants"]} == {"instagram": "carousel", "facebook": "photo"}
    image = w.fake.posts["2026w42-tue13-i3"]  # one picture: Threads takes it too, and Instagram makes it a feed post
    assert {v["platform"]: v["format"] for v in image["variants"]} == {
        "instagram": "feed",
        "facebook": "photo",
        "threads": "image",
    }


def test_the_strategist_works_in_chunks_never_repeats_an_angle_and_keeps_only_real_citations(world):
    world.runner.dup_first = True
    world.go("render")
    strategist = [c for c in world.runner.calls if c["agent"] == "strategist"]
    assert [c["node"] for c in strategist] == ["strategist-1", "strategist-1-redo", "strategist-2", "strategist-3"]
    assert all(len(c["task"]["slots"]) <= 10 for c in strategist)
    assert (
        strategist[2]["task"]["previous"] and strategist[3]["task"]["pillar_counts"]
    )  # each chunk sees what came before
    assert len(strategist[1]["task"]["slots"]) == 1  # only the repeated slot is asked again
    rows = world.result()
    assert len({r["angle"] for r in rows}) == len(rows)
    brief = next(c["task"] for c in world.runner.calls if c["agent"] == "copywriter")
    assert brief["sources"] == ["R-AAAAAAAA"] and brief["platform"] == "instagram"  # the invented R-MADEUP00 is gone


def test_a_failed_agent_holds_only_its_idea_and_every_hold_has_a_reason(world):
    world.runner.fail = {"copywriter:d02i1", "copywriter:d02i1-retry"}
    assert world.go("dry-run") == 0
    s = world.summary()
    assert s["ideas"]["completed"] == 29 and s["variants"]["made"] == 145
    held = next(r for r in world.result() if r["idea_id"] == "d02i1")
    assert held["outcome"] is None and "the copywriter failed" in held["holds"]["*"]["why"]
    assert "d02i1" in (world.folder / "report.md").read_text()
    assert not any(
        c["node"].endswith("d02i1") and c["agent"] != "copywriter" for c in world.runner.calls
    )  # no tokens after it


def test_a_platform_text_that_breaks_a_brand_rule_is_repaired_once_and_a_stubborn_one_is_held(world):
    world.runner.bad_platforms = {"threads", "tiktok"}
    world.go("dry-run")
    repairs = [c for c in world.runner.calls if c["node"].startswith("adapter-repair")]
    assert len(repairs) == 30 and set(repairs[0]["task"]["repair"]) == {"threads", "tiktok"}
    assert repairs[0]["task"]["repair"]["threads"]["problems"] == ["HALAL_UNCERTIFIED"]  # exactly what the checker said
    assert world.summary()["variants"]["made"] == 150  # the repair fixed them: nothing lost
    w2 = World(world.data.parent / "w2")
    w2.runner._adapter = lambda t, f=w2.runner._adapter: {
        **f({**t, "repair": None}),
        "idea_id": t["idea_id"],
    }  # always bad
    w2.runner.bad_platforms = {"threads"}
    w2.go("dry-run")
    s = w2.summary()
    assert s["variants"]["made"] == 120 and s["variants"]["held_reasons"] == {"text_failed": 30}
    assert "the threads text failed the brand check" in w2.result()[0]["holds"]["threads"]["why"]
    assert w2.summary()["ideas"]["completed"] == 30  # the other four platforms still went out


def test_on_screen_words_go_through_the_brand_check_before_any_render(world):
    world.tools.bad_text = "sealed"
    world.go("render")
    rows = world.result()
    assert all(r["holds"]["*"]["code"] == "text_on_screen" for r in rows) and not any(
        c[0] == "render" for c in world.renderer.calls
    )


def test_a_failed_render_is_reported_and_the_queue_goes_on(world):
    world.renderer.fail_render = {"2026w42-wed14-i2"}
    world.go("dry-run")
    held = [r for r in world.result() if r["holds"]]
    assert [r["job_id"] for r in held] == ["2026w42-wed14-i2"] and held[0]["holds"]["*"]["code"] == "render_failed"
    assert world.summary()["ideas"]["completed"] == 29


def test_the_token_budget_stops_new_ai_calls_finishes_what_started_and_names_every_idea_it_missed(tmp_path):
    cfg = sc.StudioConfig.model_validate(
        {**SHIPPED.model_dump(), "budgets": {**SHIPPED.budgets.model_dump(), "max_tokens_per_run": 30_000}}
    )
    w = World(tmp_path, cfg)
    assert w.go("render") == 0
    used = w.summary()["tokens"]["total"]
    assert used <= 30_000 + cfg.budgets.parallel_ideas * 1200  # no new call starts after the cap; in-flight ones finish
    held = [r for r in w.result() if r["holds"]]
    assert (
        held
        and all(r["holds"]["*"]["code"] == "budget" for r in held)
        and len(held) + w.summary()["ideas"]["completed"] == 30
    )
    assert any(e["kind"] == "budget_alert" for e in w.events()) and any(e["kind"] == "budget_stop" for e in w.events())


def test_a_stopped_run_resumes_without_paying_twice(tmp_path):
    class Crash(BaseException):
        pass

    w = World(tmp_path)
    original = w.runner.run
    count = {"n": 0}

    def dying(node: str, agent: str, task: str) -> Any:
        count["n"] += 1
        if count["n"] > 40:
            raise Crash
        return original(node, agent, task)

    w.runner.run = dying  # type: ignore[method-assign]
    with pytest.raises(Crash):
        w.go("dry-run")
    run_id = w.folder.name
    first = [(c["node"]) for c in w.runner.calls]
    assert not (w.data / ".running").exists()  # the lock is released: resuming is allowed
    w.runner.run = original  # type: ignore[method-assign]
    assert w.studio.run(Options(publish="dry-run", resume=run_id)) == 0
    second = [c["node"] for c in w.runner.calls][len(first) :]
    assert not set(first) & set(second)  # no finished call was made again
    assert w.summary()["ideas"]["completed"] == 30 and w.summary()["variants"]["made"] == 150
    assert w.studio.run(Options(publish="dry-run", resume="nope")) == 2


def test_an_idea_that_already_has_a_post_is_skipped_before_any_ai(world):
    world.fake.posts["2026w42-tue13-i1"] = {"post_key": "2026w42-tue13-i1"}
    world.go("dry-run")
    held = next(r for r in world.result() if r["idea_id"] == "d01i1")
    assert held["holds"]["*"]["code"] == "exists" and "--suffix r2" in held["holds"]["*"]["why"]
    assert not any(c["node"].endswith("d01i1") for c in world.runner.calls)
    w2 = World(world.data.parent / "w2")
    w2.go("dry-run", suffix="r2")  # a redo key is new
    assert w2.result()[0]["job_id"] == "2026w42-tue13-i1-r2"


def test_the_start_checks_stop_a_run_before_a_single_token_is_spent(tmp_path):
    w = World(tmp_path, publisher=False)
    assert w.go("drafts") == 1 and not w.runner.calls  # no key, no run
    w2 = World(tmp_path / "b")
    w2.tools.vocab["contracts"] = {"content_spec": 2, "manifest": 1}
    assert w2.go("dry-run") == 1 and not w2.runner.calls and "reels speaks" in json.dumps(w2.events()[-1])
    w3 = World(tmp_path / "c")
    w3.studio.publisher = handover.PublishClient(  # an unreachable publisher
        "https://publish.test", "k", sleep=lambda s: None,
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))))  # fmt: skip
    assert w3.go("dry-run") == 1 and not w3.runner.calls


def test_one_run_at_a_time(world):
    world.data.mkdir(parents=True)
    import os

    (world.data / ".running").write_text(f"{os.getpid()} other")  # a live holder
    assert world.go("render") == 2
    (world.data / ".running").write_text("999999 stale")  # a dead holder: taken over
    assert world.go("render") == 0


def test_frozen_inputs_replay_without_fetching_and_are_saved(world, tmp_path):
    frozen = tmp_path / "frozen.json"
    world.go("render", save_inputs=frozen)
    data = json.loads(frozen.read_text())
    assert {"pack", "probe_results", "vocab", "inventory", "hashtags", "examples", "today"} <= set(data)
    w2 = World(tmp_path / "w2")
    assert w2.studio.run(Options(publish="render", inputs=frozen)) == 0
    assert not [
        t for _, t, _ in w2.tools.calls if t in ("grounding_pack", "list_templates", "inventory", "brand_rules")
    ]


def test_the_adapter_is_told_each_platforms_rules_from_the_publisher_never_from_its_own_prompt(world):
    world.go("render")
    task = next(c["task"] for c in world.runner.calls if c["agent"] == "adapter")
    p = task["platforms"]
    assert (
        p["threads"]["caption_max"] == 500
        and p["threads"]["hashtags"] == [0, 1]
        and p["instagram"]["hashtags"] == [3, 5]
    )
    assert p["youtube"]["caption_max"] is None and set(p["youtube"]["option_fields"]) == {"title", "description"}
    assert p["tiktok"]["option_fields"] == {}  # privacy is code's, so the adapter is not even offered it
    assert p["youtube"]["feel"] and task["idea"]["core_caption"] and task["brand"]
    prompt = (Path(sc.__file__).parent / "agents" / "adapter.md").read_text()
    assert "2200" not in prompt and "500" not in prompt  # no platform limit is copied into the prompt


def Counter_(calls: list[dict[str, Any]]) -> dict[str, int]:  # noqa: N802
    out: dict[str, int] = {}
    for c in calls:
        out[c["agent"]] = out.get(c["agent"], 0) + 1
    return out


# ---------- one agent = one headless Claude Code process ----------


@pytest.fixture
def claude(tmp_path, monkeypatch):
    from intel.studio.agents import ClaudeRunner
    from intel.studio.runlog import RunLog

    monkeypatch.setenv("FAKE_ARGS_LOG", str(tmp_path / "args.jsonl"))
    log = RunLog(tmp_path / "run", "r1")
    servers = {"dk_intel_writer": {"command": "x", "args": []}}
    runner = ClaudeRunner(log, FakeTools(), servers, SHIPPED.budgets.max_turns, 60,
                          claude_bin=str(Path(__file__).parent / "studio_fake_claude.py"))  # fmt: skip
    return runner, log, tmp_path


def _args(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (tmp_path / "args.jsonl").read_text().splitlines()]


def test_an_agent_gets_only_its_own_prompt_tools_turns_and_an_empty_folder(claude):
    runner, log, tmp_path = claude
    res = runner.run("copywriter:d01i1", "copywriter", json.dumps({"slot_id": "d01i1"}))
    assert res.answer == {"ok": True, "task": {"slot_id": "d01i1"}}
    assert (res.tokens_in, res.tokens_out, res.turns) == (1050, 25, 2) and res.tool_calls == ["evaluate_drafts"]
    (call,) = _args(tmp_path)
    a = call["args"]
    assert a[a.index("--tools") + 1] == "" and a[a.index("--max-turns") + 1] == "6"  # no built-in tools, a turn cap
    assert a[a.index("--system-prompt") + 1].startswith("You are a COPYWRITER")  # its own prompt, not Claude Code's
    assert a[a.index("--allowedTools") + 1] == "mcp__dk_intel_writer__evaluate_drafts"
    assert sorted(a[a.index("--disallowedTools") + 1].split(",")) == ["mcp__dk_intel_writer__brand_rules",
                                                                       "mcp__dk_intel_writer__get_facts"]  # fmt: skip
    assert "--strict-mcp-config" in a and call["tool_search"] == "false"
    assert not Path(call["cwd"]).resolve().is_relative_to(sc.REPO)  # no CLAUDE.md, no memory
    events = [json.loads(x) for x in log.path.read_text().splitlines()]
    assert [e["kind"] for e in events] == ["start", "tool_call", "tool_result", "end"] and events[-1][
        "tokens_in"
    ] == 1050


def test_an_agent_with_no_tools_is_given_no_tool_flags_and_a_failure_is_reported_not_raised(claude, monkeypatch):
    runner, log, tmp_path = claude
    assert runner.run("strategist-1", "strategist", "{}").answer is not None
    a = _args(tmp_path)[0]["args"]
    assert "--allowedTools" not in a and "--disallowedTools" not in a and a[a.index("--max-turns") + 1] == "3"
    monkeypatch.setenv("FAKE_FAIL", "1")
    res = runner.run("adapter:d01i1", "adapter", "{}")
    assert res.answer is None and "boom" in res.error
    assert json.loads(log.path.read_text().splitlines()[-1])["status"] == "failed"


# ---------- the console ----------


def test_the_console_shows_a_run_idea_by_idea_and_never_starts_drafts_without_confirmation(world, monkeypatch):
    from fastapi.testclient import TestClient

    from intel.studio import console

    world.runner.fail = {"copywriter:d02i1", "copywriter:d02i1-retry"}
    world.go("dry-run")
    monkeypatch.setattr(console, "DATA", world.data)
    c = TestClient(console.app)
    (summary,) = c.get("/api/runs").json()
    assert summary["status"] == "done" and summary["ideas"] == "29/30" and summary["publish"] == "dry-run"
    run = c.get(f"/api/runs/{summary['id']}").json()
    assert (
        len(run["ideas"]) == 30 and run["tokens"] == 90 * 1200 + 200
    )  # a failed copywriter and its retry spent 100 each
    held = next(i for i in run["ideas"] if i["id"] == "d02i1")
    assert held["holds"]["*"]["code"] == "agent_failed" and held["outcome"] is None
    ok = next(i for i in run["ideas"] if i["id"] == "d01i1")
    assert (
        set(ok["outcome"]["platforms"]) == set(SHIPPED.plan.platforms) and ok["angle"] and ok["captions"]["instagram"]
    )
    assert [p["phase"] for p in run["phases"] if p["status"] == "end"] == [
        "inputs",
        "strategist",
        "production",
        "handover",
    ]
    ev = c.get(f"/api/runs/{summary['id']}/events").json()
    assert ev["next"] > 100 and c.get(f"/api/runs/{summary['id']}/events?after={ev['next']}").json()["events"] == []
    assert c.get("/api/runs/..%2Fx").status_code == 404 and c.get("/api/runs/20200101T000000Z").status_code == 404
    assert c.get(f"/api/runs/{summary['id']}/thumb/..%2Fstate.json").status_code == 404
    assert c.post("/api/run", json={"publish": "drafts"}).status_code == 409  # drafts need the owner's confirmation
    assert c.post("/api/run", json={"publish": "post-now"}).status_code == 422
    (world.data / ".running").write_text("1 20261012T180000Z")
    assert c.post("/api/run", json={"publish": "render"}).status_code == 409 and c.get("/api/status").json()["running"]
    assert "Run now" in c.get("/").text


# ---------- what the first real runs taught ----------


def test_every_agent_is_told_what_is_not_on_the_menu_and_each_platforms_hard_limits(world):
    world.go("render")
    by = {c["agent"]: c["task"] for c in reversed(world.runner.calls)}
    assert by["strategist"]["not_on_the_menu"] == by["copywriter"]["not_on_the_menu"] == ["Smokey Lamb Box"]
    assert by["adapter"]["not_on_the_menu"] == by["designer"]["not_on_the_menu"] == ["Smokey Lamb Box"]
    threads = by["adapter"]["platforms"]["threads"]
    assert (threads["first_line_max"], threads["emoji_max"], threads["hashtags"]) == (60, 4, [0, 1])


def test_a_refused_spec_or_blocked_on_screen_word_goes_back_to_the_designer_once(world):
    refused = {"n": 0}
    original = world.renderer.dry_run

    def picky(path):  # the first dry run of one idea's spec is refused; the second is fine
        spec = json.loads(path.read_text())
        if spec["job_id"] == "2026w42-tue13-i1" and refused["n"] == 0:
            refused["n"] += 1
            return 2, {"ok": False, "issues": [{"path": "shots[3]", "message": 'no video in category "Plated"'}]}
        return original(path)

    world.renderer.dry_run = picky  # type: ignore[method-assign]
    world.go("render")
    repair = [c for c in world.runner.calls if c["node"] == "designer-repair:d01i1"]
    assert len(repair) == 1 and repair[0]["task"]["repair"]["problems"] == ['shots[3]: no video in category "Plated"']
    assert world.summary()["ideas"]["completed"] == 30  # the one repair saved the idea
    w2 = World(world.data.parent / "w2")
    w2.renderer.dry_run = lambda path: (2, {"ok": False, "issues": [{"path": "x", "message": "still wrong"}]})  # type: ignore[method-assign]
    w2.go("render")
    rows = w2.result()
    assert all(r["holds"]["*"]["code"] == "spec_refused" for r in rows)
    assert sum(c["agent"] == "designer" for c in w2.runner.calls) == 60  # one repair each, never a loop


def test_an_unusable_agent_answer_is_asked_for_once_more_and_never_more(world):
    original = world.runner.run
    seen: dict[str, int] = {}

    def flaky(node: str, agent: str, task: str):
        seen[node] = seen.get(node, 0) + 1
        if node == "copywriter:d01i1" and seen[node] == 1:  # the first answer had no JSON in it
            from intel.studio.ports import AgentResult

            return AgentResult(None, 500, 20, error="no JSON in the answer")
        return original(node, agent, task)

    world.runner.run = flaky  # type: ignore[method-assign]
    world.go("render")
    assert world.summary()["ideas"]["completed"] == 30
    assert seen["copywriter:d01i1"] == 1 and seen["copywriter:d01i1-retry"] == 1
    assert any(e["kind"] == "retry" for e in world.events())
    w2 = World(world.data.parent / "w2")
    w2.runner.fail = {"copywriter:d01i1", "copywriter:d01i1-retry"}
    w2.go("render")
    assert next(r for r in w2.result() if r["idea_id"] == "d01i1")["holds"]["*"]["code"] == "agent_failed"
    assert sum(c["node"].startswith("copywriter:d01i1") for c in w2.runner.calls) == 2  # the call and one retry


# ---------- S4: what worked reaches the plan ----------


def test_what_the_numbers_support_reaches_the_strategist_and_each_platforms_adapter(world):
    import numpy as np

    from intel.performance import playbook
    from tests.test_performance import _frame

    rng = np.random.default_rng(3)
    pb = playbook.build(_frame("instagram", 80, rng), "pullX")
    playbook.write(world.data.parent / "performance", pb, _frame("instagram", 80, rng))
    world.go("render")
    strategist = next(c["task"] for c in world.runner.calls if c["agent"] == "strategist")
    ig = strategist["what_worked"]["instagram"]
    assert len(ig["works"]) <= 2 and len(ig["hurts"]) <= 1 and "format = reel" in " ".join(ig["works"])
    assert strategist["what_worked"]["tiktok"]["status"].startswith(
        "no performance data"
    )  # no data is said, not hidden
    adapter = next(c["task"] for c in world.runner.calls if c["agent"] == "adapter")
    assert adapter["platforms"]["instagram"]["what_worked"]["works"] and adapter["platforms"]["youtube"]["what_worked"]
    assert all(r["angle"] for r in world.result())
    state = json.loads((world.folder / "state.json").read_text())
    assert state["ideas"]["d01i1"]["playbook_note"] == "instagram: reels reach more"  # the plan cites what worked
    frozen = json.loads((world.folder / "inputs.json").read_text())
    assert frozen["performance"]["_basis"]["pull"] == "pullX"  # a frozen input carries it, so a replay is comparable


def test_without_a_playbook_the_agents_are_told_there_is_none_not_given_invented_advice(world):
    world.go("render")
    strategist = next(c["task"] for c in world.runner.calls if c["agent"] == "strategist")
    assert (
        strategist["what_worked"]["instagram"]["status"] == "no playbook yet"
        and not strategist["what_worked"]["instagram"]["works"]
    )
    assert all(r["angle"] for r in world.result())
