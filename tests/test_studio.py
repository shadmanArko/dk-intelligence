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
ALL_REELS = SHIPPED.with_plan(format_rotation=[])  # the 150-posts scenario: three reels a day, no rotation


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
    for name in ("plan", "slots", "routes", "budgets", "craft", "schedule", "autonomy", "models"):
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
    days = slots.plan_days(ALL_REELS, TODAY)
    scores = slots.read_scores(slots.probes(ALL_REELS, days), [])  # no answer from the scorer: every slot may pass
    ideas, dropped = slots.assign(ALL_REELS, days, scores)
    assert len(ideas) == 30 and not dropped
    keys = [i.job_id for i in ideas]
    assert len(set(keys)) == 30 and all(re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$", k) for k in keys)
    assert keys[0] == "2026w42-tue13-i1"
    first_day = [i for i in ideas if i.date == dt.date(2026, 10, 13)]
    assert sorted(i.window for i in first_day) == ["evening", "lunch", "pre_dinner"]  # one idea per window
    assert all(set(i.slots) == set(ALL_REELS.plan.platforms) for i in ideas)
    assert slots.job_id(dt.date(2026, 10, 13), 1, "R-2") == "2026w42-tue13-i1-r2"  # a redo needs a new key


def test_the_scorer_decides_the_window_and_an_idea_that_passes_nowhere_is_reported_not_written():
    days = slots.plan_days(ALL_REELS.with_plan(horizon_days=2), TODAY)
    items = slots.probes(ALL_REELS, days)
    low = {
        slots.probe_ref(p) for p in items if p[0] == days[0] and p[2] == "pre_dinner"
    }  # day 1: the best window fails
    results = [{"ref": slots.probe_ref(p), "verdict": "rewrite" if slots.probe_ref(p) in low else "keep",
                "scores": {"content_scorer": {"probability": 0.4 if slots.probe_ref(p) in low else 0.6}}}
               for p in items]  # fmt: skip
    ideas, dropped = slots.assign(ALL_REELS, days, slots.read_scores(items, results))
    # three ideas need all three windows, so one of day 1's ideas must take the failing one and is reported
    assert [d.date for d in dropped] == [days[0]] and "40%" in dropped[0].why
    assert sorted(i.window for i in ideas if i.date == days[0]) == ["evening", "lunch"] and len(ideas) == 5
    everywhere_low = [{**r, "verdict": "rewrite", "scores": {"content_scorer": {"probability": 0.3}}} for r in results]
    ideas, dropped = slots.assign(ALL_REELS, days, slots.read_scores(items, everywhere_low))
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
    def __init__(self, tmp_path: Path, cfg: sc.StudioConfig = ALL_REELS, publisher: bool = True) -> None:
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
    assert by_agent == {"strategist": 3, "director": 10, "copywriter": 30, "adapter": 30, "designer": 30}
    assert s["tokens"]["total"] == 103 * 1200 and s["tokens"]["per_idea"] == 4120
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
    world.tools.vocab["template_registry"] = None  # an engine without a registry: the director has one video template
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
    w = World(tmp_path, SHIPPED.with_plan(horizon_days=2, format_rotation=[], format_mix=["reel", "carousel", "image"]))
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
        {**ALL_REELS.model_dump(), "budgets": {**ALL_REELS.budgets.model_dump(), "max_tokens_per_run": 30_000}}
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

    def dying(node: str, agent: str, task: str, files: Any = None) -> Any:
        count["n"] += 1
        if count["n"] > 40:
            raise Crash
        return original(node, agent, task, files)

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
        len(run["ideas"]) == 30 and run["tokens"] == 100 * 1200 + 200
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
        "director",
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
    assert "Run now" in c.get("/runs").text


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

    def flaky(node: str, agent: str, task: str, files: Any = None):
        seen[node] = seen.get(node, 0) + 1
        if node == "copywriter:d01i1" and seen[node] == 1:  # the first answer had no JSON in it
            from intel.studio.ports import AgentResult

            return AgentResult(None, 500, 20, error="no JSON in the answer")
        return original(node, agent, task, files)

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


# ---------- S5: the creative director ----------


def test_the_director_asked_for_one_look_everywhere_still_gets_a_varied_plan(world):
    world.go("render")
    s = world.summary()
    looks = s["looks"]
    assert len(looks["templates"]) >= 3 and max(looks["templates"].values()) <= 12  # no look over 40% of 30 ideas
    state = json.loads((world.folder / "state.json").read_text())
    seq = [state["ideas"][i]["treatment"] for i in sorted(state["ideas"])]
    assert all(
        a["template"] != b["template"] for a, b in zip(seq, seq[1:], strict=False)
    )  # never the same look twice running
    last: dict[str, str] = {}
    for t in seq:  # a template used again changes variant
        if t["template"] in last and t["variant"]:
            assert t["variant"] != last[t["template"]]
        last[t["template"]] = t["variant"]
    assert looks["assets_used"] > 0 and looks["assets_reused"] <= looks["assets_used"]
    calls = [c["task"] for c in world.runner.calls if c["agent"] == "director"]
    assert len(calls) == 10 and len(calls[0]["ideas"]) == 3  # one call per day, the day's ideas together
    first = calls[0]["ideas"][0]
    assert {t["template"] for t in first["templates"]} == {"beat-reel", "kinetic-text", "one-take", "photo-motion"}
    assert "instagram" in first["craft"] and first["craft"]["tiktok"]["how"].startswith(
        "9:16"
    )  # how each platform's post is made


def test_the_designer_gets_the_chosen_template_its_brief_and_the_directors_wishes(world):
    world.go("render")
    d = [c["task"] for c in world.runner.calls if c["agent"] == "designer"]
    state = json.loads((world.folder / "state.json").read_text())
    for task in d:
        idea = next(i for i, r in state["ideas"].items() if r["idea"]["job_id"] == task["job_id"])
        assert task["template"] == state["ideas"][idea]["treatment"]["template"]
    kinetic = next(t for t in d if t["template"] == "kinetic-text")
    assert kinetic["template_brief"] == "Give 2-6 short lines." and kinetic["music_hint"]["culture"] == "desi"
    specs = {p.name: json.loads(p.read_text()) for p in world.folder.glob("*.spec.json")}
    templates = {s["content"]["template"] for s in specs.values()}
    assert templates == {
        t for t in world.summary()["looks"]["templates"]
    }  # code put the director's template in the spec


def test_when_the_director_fails_code_still_makes_a_varied_valid_plan(world):
    world.runner.fail = {f"director:2026-10-{d}" for d in range(13, 23)}
    world.runner.fail |= {n + "-retry" for n in world.runner.fail}
    world.go("render")
    s = world.summary()
    assert s["ideas"]["completed"] == 30 and len(s["looks"]["templates"]) >= 3
    state = json.loads((world.folder / "state.json").read_text())
    assert not any(r["treatment"]["directed"] for r in state["ideas"].values())  # all of it was code's choice


def test_a_wish_that_cannot_make_the_format_or_repeats_the_last_look_is_settled_by_code():
    from collections import Counter

    from intel.studio import director
    from tests.studio_fakes import REGISTRY

    options = director.candidates(ALL_REELS.with_plan(), REGISTRY, "reel")
    assert options == ["beat-reel", "kinetic-text", "one-take", "photo-motion"]
    landscape = director.candidates(
        ALL_REELS.with_plan(format_rotation=[["reel", "reel", "landscape"]]), REGISTRY, "landscape"
    )
    assert landscape == ["kinetic-text", "one-take", "photo-motion"]  # beat-reel has no 16:9
    assert director.candidates(ALL_REELS, REGISTRY, "story") == ["collage", "photo"]
    out = director.settle(
        ALL_REELS, {"template": "collage", "variant": "grid"}, options, REGISTRY, Counter(), {}, None, "k", 10
    )
    assert out["template"] in options and "cannot make this format" in out["adjusted"][0]
    again = director.settle(
        ALL_REELS,
        {"template": "one-take", "variant": "slow"},
        options,
        REGISTRY,
        Counter(),
        {"one-take": "slow"},
        "one-take",
        "k2",
        10,
    )
    assert again["template"] != "one-take"  # the post before used it
    legacy = director.template_registry({"templates": {"slides": {"photo": "x", "card": "y"}}})
    assert set(legacy) == {"beat-reel", "photo", "card"} and director.candidates(ALL_REELS, legacy, "reel") == [
        "beat-reel"
    ]


# ---------- S5: more surfaces ----------


@pytest.fixture
def rotating(tmp_path: Path) -> World:
    return World(tmp_path, SHIPPED.with_plan(horizon_days=4))  # the shipped rotation: carousel, story, image, landscape


def test_the_days_rotate_surfaces_and_each_reaches_only_the_platforms_that_take_it(rotating):
    w = rotating
    assert w.go("drafts") == 0
    s = w.summary()
    assert s["looks"]["formats"] == {"reel": 8, "carousel": 1, "story": 1, "image": 1, "landscape": 1}
    assert s["variants"]["planned"] == 8 * 5 + 2 + 1 + 3 + 2 == s["variants"]["made"]
    posts = w.fake.posts
    story = next(
        p
        for k, p in posts.items()
        if k.endswith("i3") and len(p["variants"]) == 1 and p["variants"][0]["format"] == "story"
    )
    assert story["variants"][0]["platform"] == "instagram" and "caption" not in story["variants"][0]
    wide = next(p for p in posts.values() if {v["platform"] for v in p["variants"]} == {"youtube", "facebook"})
    assert {v["format"] for v in wide["variants"]} == {"video"}
    specs = {p.name: json.loads(p.read_text()) for p in w.folder.glob("*.spec.json")}
    assert sorted(s["content"].get("formats", [s["content"].get("size")])[0] for s in specs.values()).count("16:9") == 1
    assert sum(s["content"].get("size") == "story" for s in specs.values()) == 1


def test_a_story_has_no_caption_so_no_copywriter_and_no_adapter_is_paid_for(rotating):
    rotating.go("render")
    story_id = next(
        i
        for i, r in json.loads((rotating.folder / "state.json").read_text())["ideas"].items()
        if r["idea"]["format"] == "story"
    )
    calls = [c["node"] for c in rotating.runner.calls]
    assert not any(
        n.endswith(story_id) and n.split(":")[0] in ("copywriter", "adapter", "adapter-repair") for n in calls
    )
    assert f"designer:{story_id}" in calls  # the picture is still designed


def test_what_no_template_could_do_is_written_down_for_a_person_to_build(tmp_path):
    from intel.studio import requests as sr

    log = tmp_path / "requests.jsonl"
    sr.add("d01i1", "designer", "a countdown card for the Saturday order deadline", "reel", log)
    sr.add("d02i3", "director", "A countdown card for the Saturday order deadline please", "story", log)
    sr.add("d03i1", "director", "a split screen before and after", "reel", log)
    rows = sr.read(log)
    assert [r["by"] for r in rows] == ["designer", "director", "director"]
    assert sr.tally(rows)[0] == ("a countdown card for the saturday", 2)  # the same wish twice is the template to build


# ---------- S5: the footage curator ----------


class _CuratorTools:
    def __init__(self, sheet: Path) -> None:
        self.sheet, self.calls, self.refuse_first = sheet, [], True

    def offered(self, server: str) -> list[str]:
        return []

    def call(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        self.calls.append((server, tool, args))
        if tool == "list_unindexed":
            return {"total_waiting": 3, "enrichable": 80, "vocabulary": {"shot_type": ["pour"]}, "items": [
                {"id": "v-new1", "kind": "video", "sheet": str(self.sheet), "tech": {"duration": 12.0}},
                {"id": "m-song", "kind": "audio", "sheet": None, "tech": {}},
                {"id": "p-nosheet", "kind": "photo", "sheet": "/nonexistent.jpg", "tech": {}}]}  # fmt: skip
        if self.refuse_first:  # the engine refuses an unknown word once; the refusal text goes back to the agent
            self.refuse_first = False
            return {
                "ok": False,
                "problems": [{"path": "moments.0.shot_type", "message": "unknown shot_type 'pouring'"}],
            }
        return {"ok": True, "pending": "v-new1"}


def test_the_curator_looks_at_the_sheet_proposes_and_fixes_a_refused_word_once(tmp_path):
    from intel.studio import curator
    from intel.studio.ai import AI
    from intel.studio.budget import Budget
    from intel.studio.runlog import RunLog

    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"jpeg")
    seen: list[dict[str, Any]] = []

    class Runner(FakeRunner):
        def _curator(self, t: dict[str, Any]) -> dict[str, Any]:
            seen.append(t)
            word = "pouring" if "repair" not in t else "pour"
            return {"id": t["id"], "moments": [{"start": 1, "end": 4, "category": "Pouring", "shot_type": word}]}

    runner = Runner()
    files_seen: list[Any] = []
    original = runner.run
    runner.run = lambda node, agent, task, files=None: (files_seen.append(files), original(node, agent, task, files))[1]  # type: ignore[method-assign]
    log = RunLog(tmp_path / "log", "c1")
    tools = _CuratorTools(sheet)
    report = curator.curate(tools, AI(runner, Budget(10**6, 0.8, 99), log), "new", 5)  # type: ignore[arg-type]
    assert report["proposed"] == ["v-new1"] and report["refused"] == [] and report["waiting"] == 3
    assert [s["id"] for s in report["skipped"]] == [
        "m-song",
        "p-nosheet",
    ]  # music is code's; no sheet, nothing to look at
    assert "repair" in seen[1] and "unknown shot_type" in str(
        seen[1]["repair"]["problems"]
    )  # exactly what the engine said
    assert all(f == {"sheet.jpg": sheet} for f in files_seen)  # the agent is given the picture and nothing else
    assert [c[1] for c in tools.calls] == ["list_unindexed", "propose_asset_entry", "propose_asset_entry"]
    assert not any(c[1] in ("accept", "write_index") for c in tools.calls)  # it only ever proposes


def test_the_scorer_ranks_every_surface_but_only_gates_reels():
    cfg = SHIPPED.with_plan(horizon_days=2)  # day 1: reel, reel, carousel
    days = slots.plan_days(cfg, TODAY)
    items = slots.probes(cfg, days)
    low = [{"ref": slots.probe_ref(p), "verdict": "rewrite",
            "scores": {"content_scorer": {"probability": 0.3}}} for p in items]  # fmt: skip
    ideas, dropped = slots.assign(cfg, days, slots.read_scores(items, low))
    assert {d.format for d in dropped} == {"reel"}  # a weak reel slot is ruled out
    assert any(i.format in ("carousel", "story") for i in ideas)  # a weak carousel or story is kept: the owner wants it


# ---------- S6: unattended ----------

NOW6 = dt.datetime(2026, 10, 13, 3, 30, tzinfo=dt.UTC)


def test_a_run_is_due_only_when_the_calendar_needs_filling_and_the_owner_is_not_backed_up():
    from intel.studio import schedule

    cfg = SHIPPED.schedule
    far_end, near_end = "2026-10-20T15:00:00+00:00", "2026-10-15T15:00:00+00:00"
    far = {
        "plan_ends": "2026-12-01T15:00:00+00:00",
        "awaiting_approval": [],
    }  # the owner's own Sheet posts reach Dec: ignored
    near = {"plan_ends": "2026-12-01T15:00:00+00:00", "awaiting_approval": []}
    assert (
        schedule.due(cfg, far, None, NOW6, far_end)[0] is False
        and "planned 7.5 days ahead" in schedule.due(cfg, far, None, NOW6, far_end)[1]
    )
    ok, why = schedule.due(cfg, near, None, NOW6, near_end)
    assert ok and "only 2.5 days ahead" in why
    assert schedule.due(cfg, {"awaiting_approval": []}, None, NOW6) == (
        True,
        "the studio has nothing planned in the calendar",
    )
    recent = NOW6 - dt.timedelta(days=2)
    assert schedule.due(cfg, near, recent, NOW6, near_end)[0] is False  # nothing twice in six days
    backed_up = {**near, "awaiting_approval": [{}] * 40}
    ok, why = schedule.due(cfg, backed_up, None, NOW6, near_end)
    assert not ok and "approve first" in why  # a backlog is never added to
    off = sc.ScheduleCfg.model_validate({**cfg.model_dump(), "enabled": False})
    assert schedule.due(off, near, None, NOW6, near_end) == (
        False,
        "the schedule is switched off (schedule.yaml: enabled: false)",
    )


def test_the_last_scheduled_run_is_found_from_the_run_folders(tmp_path):
    from intel.studio import schedule

    for name, trigger in (
        ("20261001T033000Z", "schedule"),
        ("20261005T100000Z", "manual"),
        ("20261008T033000Z", "schedule"),
    ):
        d = tmp_path / name
        d.mkdir()
        (d / "events.jsonl").write_text(
            json.dumps({"node": "orchestrator", "kind": "start", "trigger": trigger}) + "\n"
        )
    assert schedule.last_scheduled_run(tmp_path) == dt.datetime(2026, 10, 8, 3, 30, tzinfo=dt.UTC)
    assert schedule.last_scheduled_run(tmp_path / "none") is None


def test_the_launchd_job_runs_the_daily_check_and_installing_it_is_explicit(tmp_path, monkeypatch):
    import plistlib

    from intel.studio import schedule

    plist = schedule.plist_for(SHIPPED.schedule, "/opt/homebrew/bin/uv", "/usr/bin")
    assert plist["StartCalendarInterval"] == {"Hour": 3, "Minute": 30} and plist["RunAtLoad"] is False
    assert plist["ProgramArguments"][-3:] == ["--if-due", "--trigger", "schedule"]  # the cheap check, not a blind run
    calls: list[list[str]] = []
    monkeypatch.setattr(schedule.subprocess, "run", lambda cmd, **kw: calls.append(cmd))  # no real launchctl in tests
    path = schedule.install(SHIPPED.schedule, "/opt/homebrew/bin/uv", "/usr/bin", tmp_path / "x.plist")
    assert plistlib.loads(path.read_bytes())["Label"] == "com.dhakakacchi.studio"
    assert schedule.uninstall(tmp_path / "x.plist") is True and schedule.uninstall(tmp_path / "x.plist") is False
    assert all(c[0] == "launchctl" for c in calls)


def test_a_scheduled_run_tells_the_owner_and_leaves_a_summary_and_a_failed_one_says_why(tmp_path):
    from intel.studio.notify import LocalNotifier

    w = World(tmp_path)
    notes: list[tuple[str, str]] = []

    class Rec(LocalNotifier):
        def notify(self, title: str, message: str) -> None:
            notes.append((title, message))

    w.studio.notifier = Rec(tmp_path)
    w.studio.run(Options(publish="render", trigger="schedule", today=TODAY))
    assert notes[-1][0] == "Studio run finished" and "30 of 30 ideas" in notes[-1][1]
    assert (w.data / "last-summary.md").read_text().startswith("# Studio run")
    w2 = World(tmp_path / "b", publisher=False)
    notes.clear()
    w2.studio.notifier = Rec(tmp_path)
    w2.studio.run(Options(publish="drafts", trigger="schedule", today=TODAY))  # no key: fails before any AI
    assert notes[-1][0] == "Studio run failed" and "DK_PUBLISH_URL" in notes[-1][1]
    assert "FAILED" in (w2.data / "last-summary.md").read_text()
    w3 = World(tmp_path / "c")
    w3.studio.notifier = Rec(tmp_path)
    notes.clear()
    w3.studio.run(Options(publish="render", trigger="manual", today=TODAY))
    assert not notes  # a run you started yourself does not interrupt you with a notification


def test_the_budget_alert_reaches_the_owner(tmp_path):
    from intel.studio.notify import NullNotifier

    cfg = sc.StudioConfig.model_validate(
        {**ALL_REELS.model_dump(), "budgets": {**ALL_REELS.budgets.model_dump(), "max_tokens_per_run": 100_000}}
    )
    w = World(tmp_path, cfg)
    alerts: list[tuple[str, str]] = []

    class Rec(NullNotifier):
        def notify(self, title: str, message: str) -> None:
            alerts.append((title, message))

    w.studio.notifier = Rec()
    w.studio.run(Options(publish="render", trigger="schedule", today=TODAY))
    assert alerts[0][0] == "Studio: token budget" and "80%" in alerts[0][1] or "%" in alerts[0][1]


def test_a_model_can_be_changed_per_agent_and_the_choice_is_checked(claude):
    runner, log, tmp_path = claude
    runner.models = {"copywriter": "haiku"}
    runner.run("copywriter:d01i1", "copywriter", "{}")
    a = _args(tmp_path)[0]["args"]
    assert a[a.index("--model") + 1] == "haiku"
    runner.run("strategist-1", "strategist", "{}")  # an agent with no override keeps its own file's model
    b = _args(tmp_path)[1]["args"]
    assert b[b.index("--model") + 1] == "sonnet"
    from intel.studio.cli import _models

    assert (
        _models(SHIPPED, ["designer=haiku"])["designer"] == "haiku" and _models(SHIPPED, None)["designer"] == "sonnet"
    )
    import typer

    with pytest.raises(typer.Exit):
        _models(SHIPPED, ["designer=gpt"])


def test_autonomy_is_measured_per_kind_and_never_acts():
    from intel.studio import autonomy

    cfg = SHIPPED.autonomy
    assert cfg.enabled is False  # the shipped state: measurement only
    kinds = {
        f"k{i}": {"pillar": "food" if i < 12 else "order", "format": "reel", "template": "one-take"} for i in range(16)
    }
    rows = [{"source": "api", "post_key": f"k{i}", "slot": "2026-10-10T15:00:00+00:00",
             "state": "cancelled" if i in (3, 7, 12) else "published"} for i in range(16)]  # fmt: skip
    rows.append(
        {"source": "sheet", "post_key": "k0", "slot": "2026-10-10T15:00:00+00:00", "state": "cancelled"}
    )  # not ours
    rows.append(
        {"source": "api", "post_key": "k1", "slot": "2026-09-01T15:00:00+00:00", "state": "cancelled"}
    )  # too old
    rows.append({"source": "api", "post_key": "k2", "slot": "2026-10-10T15:00:00+00:00", "state": "awaiting_approval"})
    t = {(r["kind"], r["value"]): r for r in autonomy.table(rows, kinds, cfg, NOW6)}
    assert t[("pillar", "food")]["decided"] == 12 and t[("pillar", "food")]["approved"] == 10
    assert t[("pillar", "food")]["clears_bar"] is True  # 83% of 12
    assert t[("pillar", "order")]["decided"] == 4 and t[("pillar", "order")]["clears_bar"] is False  # too few to say
    assert t[("template", "one-take")]["decided"] == 16


def test_only_the_studios_own_posts_say_whether_its_plan_runs_out():
    from intel.studio import schedule

    rows = [
        {"source": "sheet", "slot": "2026-12-01T10:00:00+00:00", "state": "scheduled"},  # the owner's own plan
        {"source": "api", "slot": "2026-10-16T10:00:00+00:00", "state": "scheduled"},
        {"source": "api", "slot": "2026-10-30T10:00:00+00:00", "state": "cancelled"},
    ]  # rejected: no longer planned
    assert schedule.agent_plan_ends(rows) == "2026-10-16T10:00:00+00:00" and schedule.agent_plan_ends([]) is None


# ---------- the Today page and the living picture ----------


def _readers(**over: Any):
    from intel.studio import today

    base = dict(
        calendar=lambda: [],
        pending_proposals=lambda: [],
        unlabelled_footage=lambda: 0,
        last_summary=lambda: None,
        running=lambda: None,
        schedule_installed=lambda: False,
        now=lambda: dt.datetime(2026, 10, 14, 9, 0, tzinfo=dt.UTC),
    )
    return today.Readers(**{**base, **over})


def test_today_lists_what_waits_for_the_owner_most_pressing_first():
    from intel.studio import today

    rows = [
        {"source": "api", "state": "awaiting_approval", "slot": f"2026-10-1{4 + i % 2}T15:00:00+00:00"}
        for i in range(5)
    ]
    rows += [{"source": "sheet", "state": "awaiting_approval", "slot": "2026-10-14T15:00:00+00:00"}]  # not the studio's
    pending = [{"id": "c0001", "moments": [{"description": "A spatula places meat onto a plate"}]}]
    r = _readers(calendar=lambda: rows, pending_proposals=lambda: pending, unlabelled_footage=lambda: 92,
                 last_summary=lambda: "# Studio run 1 (manual, drafts)\n\nIdeas: 26 of 26 made")  # fmt: skip
    data = today.build(r, SHIPPED.schedule, [{"id": "rotation", "title": "All reels, or a mix?", "detail": "d"}])
    by = {i["id"]: i for i in data["items"]}
    assert (
        by["drafts"]["title"] == "Approve 5 drafts"
        and "10-14: 3" in by["drafts"]["detail"]
        and "10-15: 2" in by["drafts"]["detail"]
    )
    assert [a["label"] for a in by["curator"]["actions"]] == ["Accept all", "Reject all"] and "spatula" in by[
        "curator"
    ]["detail"]
    assert (
        by["enrich"]["count"] == 92 and "160,000 tokens" in by["enrich"]["actions"][0]["confirm"]
    )  # the cost is said first
    assert by["schedule"]["level"] == "info" and by["decision-rotation"]["level"] == "todo"
    assert by["start"]["actions"][0]["body"] == {"publish": "drafts", "confirm": True}
    levels = [i["level"] for i in data["items"]]
    assert (
        levels == sorted(levels, key=["todo", "info", "ok"].index) and data["open"] == 3
    )  # drafts, proposals, a decision


def test_today_says_so_when_nothing_waits_or_the_publisher_cannot_be_reached():
    from intel.studio import today

    clear = today.build(_readers(schedule_installed=lambda: True), SHIPPED.schedule, [])
    assert clear["open"] == 0 and {i["id"]: i["level"] for i in clear["items"]}["drafts"] == "ok"
    assert today.reminder_text(clear) is None  # nothing waits: no reminder, no interruption
    down = today.build(_readers(calendar=lambda: None), SHIPPED.schedule, [])
    assert "Cannot reach the publisher" in {i["id"]: i for i in down["items"]}["drafts"]["detail"]
    busy = today.build(
        _readers(calendar=lambda: [{"source": "api", "state": "awaiting_approval", "slot": "2026-10-14"}] * 3),
        SHIPPED.schedule,
        [],
    )
    assert today.reminder_text(busy) == ("Studio: waiting for you", "Approve 3 drafts")
    sunday = today.build(_readers(now=lambda: dt.datetime(2026, 10, 18, 9, 0, tzinfo=dt.UTC)), SHIPPED.schedule, [])
    assert "analyst" in {i["id"] for i in sunday["items"]} and "analyst" not in {
        i["id"] for i in clear["items"]
    }  # Sundays only


def _bar_events() -> list[dict[str, Any]]:
    """A small real-shaped log: one strategist call, one director call, one idea through copy, adapt, design, render."""

    def ev(ms: int, node: str, kind: str, **kw: Any) -> dict[str, Any]:
        return {"t": "", "ms": ms, "node": node, "kind": kind, **kw}

    return [
        ev(1, "orchestrator", "start", publish="drafts"),
        ev(10, "strategist-1", "start", agent="strategist", model="sonnet"),
        ev(5000, "strategist-1", "end", status="done", ms_total=4990, tokens_in=100, tokens_out=50, turns=1),
        ev(5100, "director:2026-10-14", "start", agent="director", model="sonnet"),
        ev(6000, "director:2026-10-14", "end", status="done", ms_total=900, tokens_in=80, tokens_out=20, turns=1),
        ev(6001, "d01i1", "treatment", template="one-take", variant="", directed=True),
        ev(6100, "copywriter:d01i1", "start", agent="copywriter", model="sonnet"),
        ev(8000, "copywriter:d01i1", "end", status="done", ms_total=1900, tokens_in=300, tokens_out=30, turns=2),
        ev(8100, "adapter:d01i1", "start", agent="adapter", model="sonnet"),
    ]


def test_mission_control_numbers_come_from_the_events_and_a_working_agent_is_marked_working():
    from intel.studio import telemetry

    ideas = [
        {"id": "d01i1", "date": "2026-10-14", "format": "reel", "template": "one-take", "angle": "a", "holds": {},
         "outcome": None},
        {"id": "d01i2", "date": "2026-10-14", "format": "reel", "template": None, "angle": "b", "holds": {},
         "outcome": None},
    ]  # fmt: skip
    t = telemetry.telemetry({"id": "r", "status": "running", "publish": "drafts", "ideas": ideas, "cap": 1000},
                            _bar_events(), now_ms=9000)  # fmt: skip
    cells = {m["id"]: m["cells"] for m in t["matrix"]}
    assert cells["d01i1"] == {"plan": "done", "direct": "done", "write": "done", "adapt": "active",
                              "design": "pending", "render": "pending", "upload": "pending"}  # fmt: skip
    assert cells["d01i2"]["write"] == "pending" and cells["d01i2"]["direct"] == "pending"
    assert [(w["lane"], w["idea"], w["seconds"]) for w in t["working"]] == [("adapter", "d01i1", 1)]
    assert t["tokens"] == 580 and t["roles"]["copywriter"]["calls"] == 1 and t["running"] is True
    assert t["percent"] == 17.5 and t["eta_s"] == 42  # 9 s elapsed at 17.5 %: about 42 s left
    assert {s["id"]: s["done"] for s in t["stages"]}["write"] == 1
    assert any("copywriter finished d01i1" in line for line in t["log"])


def test_a_held_idea_is_shown_held_at_its_stage_and_the_rest_skipped_and_a_render_only_run_has_no_upload():
    from intel.studio import telemetry

    idea = {"id": "d01i1", "date": "2026-10-14", "format": "reel", "template": "one-take", "angle": "a",
            "holds": {"*": {"code": "text_failed"}}, "outcome": None}  # fmt: skip
    t = telemetry.telemetry({"id": "r", "status": "done", "publish": "render", "ideas": [idea]}, _bar_events())
    c = t["matrix"][0]["cells"]
    assert c["adapt"] == "held" and c["design"] == "skipped" and c["upload"] == "skipped" and t["ideas_held"] == 1
    assert t["running"] is False and t["percent"] == 100.0


def test_the_demo_is_a_run_of_events_through_the_same_function_and_it_finishes():
    from intel.studio import telemetry

    early, mid, end = (telemetry.demo(k) for k in (0, 40, 95))
    assert early["running"] and early["percent"] == 0.0 and mid["running"] and 0 < mid["percent"] < 100
    assert end["status"] == "done" and end["percent"] == 100.0 and end["ideas_done"] == 12
    assert telemetry.demo(40) == mid  # deterministic: the same moment always draws the same picture
    assert {b["lane"] for b in mid["bars"]} >= {"strategist", "director", "copywriter", "designer", "render"}


# ---------- how far ahead the calendar is already planned ----------


def _row(slot: str, state: str = "scheduled", source: str = "api") -> dict[str, Any]:
    return {"post_key": slot, "platform": "instagram", "slot": slot, "state": state, "source": source}


def test_coverage_says_how_many_days_are_scheduled_and_where_the_next_plan_starts():
    from intel.studio import coverage

    now = dt.datetime(2026, 10, 13, 10, 0, tzinfo=dt.UTC)
    rows = [_row("2026-10-14T17:00:00+02:00"), _row("2026-10-15T20:00:00+02:00", "awaiting_approval"),
            _row("2026-10-17T20:00:00+02:00"), _row("2026-10-23T23:30:00+02:00"),
            _row("2026-10-30T12:00:00+01:00", "cancelled"),
            _row("2026-10-29T12:00:00+01:00", source="sheet")]  # fmt: skip
    cov = coverage.coverage(SHIPPED, rows, now)
    assert cov["scheduled_through"] == "2026-10-23" and cov["days_ahead"] == 10
    assert cov["next_start"] == "2026-10-24"  # cancelled posts and the owner's Sheet posts do not count
    assert cov["gaps"] == ["2026-10-16", "2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
    assert [d["waiting"] for d in cov["days"]][:2] == [0, 1] and "Sat 24 Oct" in cov["sentence"]


def test_coverage_with_nothing_scheduled_or_no_publisher_starts_on_the_plans_own_first_day():
    from intel.studio import coverage

    now = dt.datetime(2026, 10, 13, 10, 0, tzinfo=dt.UTC)
    empty = coverage.coverage(SHIPPED, [], now)
    assert empty["scheduled_through"] is None and empty["next_start"] == "2026-10-14" and empty["known"]
    down = coverage.coverage(SHIPPED, None, now)
    assert down["known"] is False and down["next_start"] == "2026-10-14"
    late = [_row("2026-10-13T17:00:00+02:00")]  # today's own slot is history, not a plan
    assert coverage.coverage(SHIPPED, late, now)["scheduled_through"] is None


def test_the_owners_start_date_wins_but_never_lands_in_the_past():
    from intel.studio import coverage

    now = dt.datetime(2026, 10, 13, 10, 0, tzinfo=dt.UTC)
    cov = coverage.coverage(SHIPPED, [_row("2026-10-23T20:00:00+02:00")], now)
    assert coverage.resolve_start(SHIPPED, cov, None)[0] == dt.date(2026, 10, 24)
    assert coverage.resolve_start(SHIPPED, cov, dt.date(2026, 11, 2))[0] == dt.date(2026, 11, 2)
    day, why = coverage.resolve_start(SHIPPED, cov, dt.date(2026, 10, 1))
    assert day == dt.date(2026, 10, 14) and "first possible" in why
    days = slots.plan_days(SHIPPED, TODAY, dt.date(2026, 10, 24))
    assert days[0] == dt.date(2026, 10, 24) and len(days) == 10
    assert slots.plan_days(SHIPPED, TODAY, dt.date(2020, 1, 1))[0] == TODAY + dt.timedelta(days=1)


def test_the_calendar_is_read_a_week_at_a_time_so_the_page_size_never_hides_a_day():
    from intel.studio import coverage

    asked: list[tuple[str, str]] = []

    class Client:
        def calendar(self, **f: Any) -> dict[str, Any]:
            asked.append((f["from"], f["to"]))
            return {"posts": [_row("2026-10-14T17:00:00+02:00")] if len(asked) <= 2 else []}

    rows = coverage.fetch_rows(Client(), dt.datetime(2026, 10, 13, 10, 0, tzinfo=dt.UTC))
    assert rows is not None and len(rows) == 1  # the same post seen at a window edge is kept once
    assert len(asked) == 4 and asked[1][0] == asked[0][1]  # two weeks with posts, two empty weeks, then it stops

    class Down:
        def calendar(self, **f: Any) -> dict[str, Any]:
            raise OSError("unreachable")

    assert coverage.fetch_rows(Down(), dt.datetime(2026, 10, 13, tzinfo=dt.UTC)) is None


def test_today_tells_you_to_plan_when_the_calendar_runs_short_and_starts_the_run_after_what_exists():
    from intel.studio import coverage, today

    now = dt.datetime(2026, 10, 13, 10, 0, tzinfo=dt.UTC)
    short = coverage.coverage(SHIPPED, [_row("2026-10-15T20:00:00+02:00")], now)
    t = today.build(_readers(), SHIPPED.schedule, [], short)
    item = next(i for i in t["items"] if i["id"] == "coverage")
    assert item["level"] == "todo" and item["actions"] == [{"label": "Open the Runs page", "href": "/runs"}]
    start = next(i for i in t["items"] if i["id"] == "start")["actions"][0]
    assert start["body"]["start"] == "2026-10-16" and "2026-10-16" in start["confirm"]
    full = coverage.coverage(SHIPPED, [_row("2026-10-24T20:00:00+02:00")], now)
    ok = next(i for i in today.build(_readers(), SHIPPED.schedule, [], full)["items"] if i["id"] == "coverage")
    assert ok["level"] == "ok" and ok["actions"] == []
    no_info = today.build(_readers(), SHIPPED.schedule, [], coverage.coverage(SHIPPED, None, now))
    assert "coverage" not in {i["id"] for i in no_info["items"]}  # unreachable publisher: no claim either way


def test_the_run_command_plans_from_the_day_after_what_is_scheduled_unless_you_give_a_date():
    import typer

    from intel.studio import cli

    class Pub:
        def calendar(self, **f: Any) -> dict[str, Any]:
            return {"posts": [_row("2026-10-23T20:00:00+02:00")] if f["from"] < "2026-10-20" else []}

    class Fake:
        publisher = Pub()

    real_now = dt.datetime

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> dt.datetime:
            return real_now(2026, 10, 13, 10, 0, tzinfo=tz or dt.UTC)

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as m:
        m.setattr(cli.dt, "datetime", Frozen)
        assert cli._start_day(SHIPPED, Fake(), None, None) == dt.date(2026, 10, 24)  # noqa: SLF001
        assert cli._start_day(SHIPPED, Fake(), "2026-11-02", None) == dt.date(2026, 11, 2)  # noqa: SLF001
        assert cli._start_day(SHIPPED, Fake(), None, "20261013T101000Z") is None  # noqa: SLF001 - a resume keeps its days
        with pytest.raises(typer.Exit):
            cli._start_day(SHIPPED, Fake(), "next tuesday", None)  # noqa: SLF001

    class Offline:
        publisher = None

    assert cli._start_day(SHIPPED, Offline(), "2026-11-02", None) == dt.date(2026, 11, 2)  # noqa: SLF001
    assert cli._start_day(SHIPPED, Offline(), None, None) is None  # noqa: SLF001 - the plan's own default


def test_the_console_serves_every_page_with_one_top_bar_and_only_plain_ids_reach_the_curate_command(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from intel.studio import console

    reels = tmp_path / "reels"
    (reels / "index" / "curation" / "pending").mkdir(parents=True)
    (reels / "index" / "curation" / "pending" / "c1.json").write_text(json.dumps({"id": "c1", "moments": []}))
    (reels / "index" / "curation" / "pending" / "bad.json").write_text(json.dumps({"id": "x; rm -rf ~", "moments": []}))
    (reels / "index" / "assets.json").write_text(
        json.dumps({"assets": [{"type": "video"}, {"type": "photo", "mood": ["warm"]}]})
    )
    monkeypatch.setattr(console, "_reels", lambda: reels)
    monkeypatch.setattr(console, "DATA", tmp_path / "data")
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        console, "_run_npm", lambda *a: (seen.append(a), type("R", (), {"returncode": 0, "stderr": ""})())[1]
    )
    c = TestClient(console.app)
    pages = {u: c.get(u).text for u in ("/", "/live", "/runs", "/guide")}
    assert all("id=topbar" in html for html in pages.values())  # the same top bar on every page
    assert "Studio: Today" in pages["/"] and "Mission control" in pages["/live"] and "Run now" in pages["/runs"]
    assert c.get("/api/telemetry").json()["status"] == "idle" and c.get("/api/telemetry/demo?tick=5").json()["demo"]
    monkeypatch.setattr(console, "_running", lambda: None)
    assert c.post("/api/run", json={"publish": "render", "start": "not-a-date"}).status_code == 422
    assert today_unlabelled(reels) == 1
    assert c.post("/api/curate/reject-all").json()["message"] == "rejected 2 proposals"
    assert seen == [("--reject", "c1")]  # the odd id was never passed to a command line
    assert c.post("/api/curate/accept-all").status_code == 200 and seen[-1] == ("--accept-all",)


def today_unlabelled(reels: Path) -> int:
    from intel.studio import today

    return today.unlabelled_footage(reels)


def test_the_reminder_job_runs_the_remind_command_and_installing_adds_it_beside_the_check(tmp_path, monkeypatch):
    import plistlib

    from intel.studio import schedule

    plist = schedule.reminder_plist_for(SHIPPED.schedule, "/opt/homebrew/bin/uv", "/usr/bin")
    assert plist["ProgramArguments"][-3:] == ["intel", "studio", "remind"] and plist["StartCalendarInterval"] == {
        "Hour": 9,
        "Minute": 0,
    }
    monkeypatch.setattr(schedule.subprocess, "run", lambda cmd, **kw: None)
    schedule.install(SHIPPED.schedule, "/opt/homebrew/bin/uv", "/usr/bin", tmp_path / "check.plist")
    assert plistlib.loads((tmp_path / "com.dhakakacchi.studio.reminder.plist").read_bytes())["Label"].endswith(
        "reminder"
    )
    assert schedule.uninstall(tmp_path / "check.plist") and not list(tmp_path.glob("*.plist"))
