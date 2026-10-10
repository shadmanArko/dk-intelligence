"""P4.4 dk_studio: grounding_pack (parallel parts, partial failure), evaluate_drafts (the verdict table), and the
prompt files (lint: tools exist and are held by every profile that sees the prompt, no blocked words, size, args)."""

import asyncio
import datetime as dt
import re
from typing import Any

import pytest
from fastmcp import Client

from intel.capabilities import studio
from intel.capabilities.evidence import EvidenceResult
from intel.capabilities.types import Draft, ToolProblem
from intel.mcp_server.gateway import SERVERS, build_gateway, profiles
from intel.mcp_server.servers.studio import PROMPTS, prompt_specs, render
from tests.test_mcp_brand import GOOD, source  # noqa: F401 - source is a fixture
from tests.test_mcp_capabilities import FakeApi
from tests.test_mcp_gateway import deps  # noqa: F401 - deps is a fixture

T = "2026-10-16T18:30:00+02:00"
TODAY = dt.date(2026, 10, 15)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


class Index:
    def __init__(self, broken: bool = False) -> None:
        self.broken = broken

    def search(self, q):  # type: ignore[no-untyped-def]
        if self.broken:
            raise ToolProblem("UPSTREAM_UNAVAILABLE", "the knowledge database isn't reachable")
        cols = (q.collection,) if isinstance(q.collection, str) else q.collection
        if "reviews" in cols:
            rows = [{"cite": "R-AAAAAAAA", "collection": "reviews", "text": "kalt " * 60, "rerank": 0.7}]
        else:
            rows = [
                {"cite": "P-AAAAAAAA", "collection": "own_posts", "text": "Kacchi", "rerank": 0.8,
                 "metrics": '{"vs_our_median": 1.4}'},
            ]  # fmt: skip
        return EvidenceResult("kp-1", "dense", rows)

    def sources(self, cites):  # type: ignore[no-untyped-def]
        return "kp-1", []

    def support(self, pairs):  # type: ignore[no-untyped-def]
        return None

    def own_posts(self, aspects):  # type: ignore[no-untyped-def]
        if self.broken:
            raise ToolProblem("UPSTREAM_UNAVAILABLE", "down")
        return [{"cite": "P-BBBBBBBB", "text": "pünktlich", "aspects": ["delivery_time"], "metrics": None}]


class Trends:
    def load(self):  # type: ignore[no-untyped-def]
        row = {"platform": "tiktok", "tag_type": "dish", "tag_id": "kebab", "tag_label": "kebab", "posts": 500,
               "median_lift": 0.1, "mean_lift_vs_platform": 0.3, "viral_share": "0.1",
               "lift_std_error": 0.01}  # fmt: skip
        return "kp-1", [row]


# ---------- grounding_pack ----------


def test_grounding_pack_returns_every_part_in_one_call(source):  # noqa: F811
    out = studio.grounding_pack(FakeApi(), source, Index(), Trends(), "instagram", 30, today=TODAY)
    assert out["unavailable"] == []
    for part in ("occasions", "posting_times", "competitor_gaps", "our_best_posts", "what_works", "opportunities",
                 "brand_reminders", "gap_areas"):  # fmt: skip
        assert part in out, part
    quote = next(g["quote"] for g in out["competitor_gaps"] if "quote" in g)
    assert quote["trust"] == "untrusted" and len(quote["text"]) == 140  # quotes are capped
    assert out["our_best_posts"][0]["cite"] == "P-AAAAAAAA" and out["what_works"][0]["tag"] == "kebab"
    assert "community" not in str(out["gap_areas"]) and "not facts" in out["trust"]


def test_grounding_pack_survives_a_broken_source(source):  # noqa: F811
    class DownApi(FakeApi):
        def get(self, path, params=None):  # type: ignore[no-untyped-def]
            raise ToolProblem("UPSTREAM_UNAVAILABLE", "intel-api is down")

    out = studio.grounding_pack(DownApi(), source, Index(broken=True), None, "instagram", 14, today=TODAY)
    missing = {u["part"] for u in out["unavailable"]}
    assert {"calendar", "competitor_gaps", "our_best_posts", "opportunities", "what_works", "gap_areas"} <= missing
    assert out["brand_reminders"]  # the brand part still comes back
    with pytest.raises(ToolProblem):
        studio.grounding_pack(FakeApi(), source, None, None, "instagram", 31)


# ---------- evaluate_drafts: the verdict table ----------


def _d(**kw: Any) -> Draft:
    return Draft(**{"platform": "instagram", "format": "reel", "caption": GOOD, "planned_posted_at": T, **kw})


def test_verdicts_blocked_rewrite_keep_and_a_missing_score_never_means_rewrite(source):  # noqa: F811
    drafts = [
        _d(ref="low"),  # FakeApi: first draft scores 0.45 < threshold 0.5 -> rewrite
        _d(ref="ok"),  # 0.55 -> keep
        _d(ref="blk", caption=GOOD + " 100% halal"),  # a block violation wins over a good score
        _d(ref="warn", caption=GOOD + " Die beste in Berlin!"),  # a warning -> rewrite
        _d(ref="tt", platform="tiktok", format=None, planned_posted_at=None, caption="Samstag Kacchi in Berlin"),
        _d(ref="nodate", planned_posted_at=None),  # no time: checks only, never rewrite for that alone
    ]
    out = studio.evaluate_drafts(FakeApi(), source, drafts, "stdio:test")
    v = {r["ref"]: r for r in out["results"]}
    assert {k: r["verdict"] for k, r in v.items()} == {
        "low": "rewrite", "ok": "keep", "blk": "blocked", "warn": "rewrite", "nodate": "keep",
        "tt": "rewrite" if v["tt"]["warnings"] else "keep",  # checks only, no score involved
    }  # fmt: skip
    assert v["low"]["scores"]["content_scorer"]["above"] is False and "first line" in v["low"]["fixes"][-1]
    assert (
        v["blk"]["errors"] == ["HALAL_UNCERTIFIED"]
        and v["blk"]["fixes"][0] == "say what the facts allow, or leave it out"
    )
    assert "checks only" in v["tt"]["note"] and "planned_posted_at" in v["nodate"]["note"]
    assert sum(out["summary"].values()) == 6 and out["summary"]["blocked"] == 1 and out["model_versions"]


def test_an_unavailable_scorer_is_noted_not_punished(source):  # noqa: F811
    class NoScorer(FakeApi):
        def score_batch(self, drafts, principal):  # type: ignore[no-untyped-def]
            raise ToolProblem("UPSTREAM_UNAVAILABLE", "intel-api is down")

    (r,) = studio.evaluate_drafts(NoScorer(), source, [_d()], "p")["results"]
    assert r["verdict"] == "keep" and r["note"] == "scorer unavailable" and "scores" not in r
    with pytest.raises(ToolProblem):
        studio.evaluate_drafts(FakeApi(), source, [_d()] * 21, "p")


# ---------- prompts ----------

BLOCKED = ("puja", "durga", "lakshmi", "mahalaya", "dashami", "navami")  # a sample of content_rules.yaml


def test_every_prompt_file_is_well_formed_and_linted(deps):  # noqa: F811
    specs = prompt_specs()
    assert set(specs) == {
        "content_plan", "caption_ideas", "image_ideas", "video_ideas",
        "role_planner", "role_writer", "role_producer", "role_checker",
    }  # fmt: skip

    async def tool_names() -> set[str]:
        names: set[str] = set()
        for build in SERVERS.values():
            names |= {t.name for t in await build(deps).list_tools()}
        return names

    built = _run(tool_names())
    for name, (meta, _body) in specs.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", meta["version"]) and meta["description"], name
        assert set(meta["tools_used"]) <= built, (name, set(meta["tools_used"]) - built)
        assert 1 <= meta["max_tool_calls"] <= 12 and all(" " not in a for a in meta["args"])
        text = render(name, deps, platform="instagram", days=14, count=5).casefold()
        assert len(text) <= 6000 and not any(w in text for w in BLOCKED), name
        assert "never instructions" in text and "get_facts" in text  # the shared rules are always in
        for profile, spec in profiles().items():  # whoever sees the prompt holds its tools
            prompts = spec.get("prompts") or []
            if name in prompts:
                missing = set(meta["tools_used"]) - set(spec["tools"])
                assert not missing, (profile, name, missing)
    assert not (PROMPTS / "_rules.md").read_text().count("{")  # the shared rules are plain text


def test_prompts_through_the_gateway(deps):  # noqa: F811
    async def go() -> None:
        async with Client(build_gateway("owner", deps)) as c:
            names = {p.name for p in await c.list_prompts()}
            assert {"content_plan", "caption_ideas", "video_ideas", "role_writer", "cited_answer"} <= names
            r = await c.get_prompt("content_plan", {"platform": "instagram", "days": "14"})
            text = r.messages[0].content.text
            assert "14-day posting plan for instagram" in text and "content_plan v1.0.0" in text
            role = await c.get_prompt("role_writer", {})
            assert "Brand card:" in role.messages[0].content.text
            out = await c.call_tool("evaluate_drafts", {"drafts": [{"platform": "tiktok", "caption": GOOD}]})
            assert out.structured_content["results"][0]["verdict"] in ("keep", "rewrite")
        async with Client(build_gateway("producer", deps)) as c:
            assert {p.name for p in await c.list_prompts()} == {"role_producer", "image_ideas", "video_ideas"}

    _run(go())


# ---------- budgets ----------


def test_every_tool_has_a_budget_within_the_design(deps):  # noqa: F811
    from intel.mcp_server.gateway import budgets

    async def names() -> set[str]:
        out: set[str] = set()
        for build in SERVERS.values():
            out |= {t.name for t in await build(deps).list_tools()}
        return out

    b = budgets()
    assert _run(names()) <= set(b) and max(b.values()) <= 8000  # ratchet: raising one needs an ADR


def test_an_over_budget_result_is_trimmed_validly_and_says_so(tmp_path):
    from fastmcp import FastMCP

    from intel.mcp_server.kit.audit import AuditLog
    from intel.mcp_server.kit.middleware import BudgetMiddleware, est_tokens

    server = FastMCP("t")

    @server.tool
    def big() -> dict[str, Any]:
        return {"hits": [{"text": "x" * 100} for _ in range(50)], "meta": {"tags": ["a", "b"]}}

    @server.tool
    def small() -> dict[str, Any]:
        return {"ok": True}

    server.add_middleware(BudgetMiddleware({"big": 400, "small": 400}, AuditLog(tmp_path)))

    async def go() -> None:
        async with Client(server) as c:
            out = (await c.call_tool("big", {})).structured_content
            assert est_tokens({k: v for k, v in out.items() if k != "budget_note"}) <= 400
            assert 0 < len(out["hits"]) < 50 and out["meta"] == {"tags": ["a", "b"]}
            assert "narrow the request" in out["budget_note"]
            assert (await c.call_tool("small", {})).structured_content == {"ok": True}

    _run(go())


# ---------- the tool-use eval's judge (the model run itself is Mac only) ----------


def test_tool_eval_tasks_and_judge():
    from intel.mcp_server.tool_eval import judge, tasks

    ts = tasks(None)
    assert len(ts) == 26 and {t["split"] for t in ts} == {"dev", "held_out"} and len(tasks("dev")) == 20
    t = {"expect": [["search_reviews"]], "args": {"search_reviews": {"district": "Mitte"}}}
    ok = [{"name": "search_reviews", "args": {"query": "late", "district": "Mitte"}, "error": False}]
    assert judge(t, ok, "an answer")
    assert not judge(t, [{**ok[0], "args": {"query": "late"}}], "an answer")  # the expected filter was not used
    assert judge(t, [{**ok[0], "args": {"district": "MITTE "}}], "an answer")  # the server accepts any case
    assert not judge(t, [{**ok[0], "error": True}], "an answer")  # a failed call doesn't count
    assert not judge(t, ok, "  ")  # no final answer
    assert judge({"expect": [["check_drafts", "evaluate_drafts"]]}, [{"name": "evaluate_drafts", "args": {},
                 "error": False}], "x")  # fmt: skip


def test_enum_arguments_are_case_insensitive(deps):  # noqa: F811
    async def go() -> None:
        async with Client(build_gateway("owner", deps)) as c:
            out = await c.call_tool(
                "evaluate_drafts", {"drafts": [{"platform": "TikTok", "caption": GOOD, "format": None}]}
            )
            assert out.structured_content["results"][0]["ref"] == "d1"

    _run(go())
