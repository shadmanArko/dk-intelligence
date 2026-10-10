"""The dk_intel gateway as a client sees it (in-memory, no LLM, no network): tools, profiles, errors, audit, copy guard,
plus the audit chain and scoring limits across processes, and a smoke test of the committed .mcp.json command."""

import asyncio
import datetime as dt
import json
import multiprocessing as mp
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from intel.adapters.intel_api import ScoringLimits  # noqa: E402
from intel.adapters.pack import PackBrandSource  # noqa: E402
from intel.capabilities.types import ToolProblem  # noqa: E402
from intel.mcp_server.deps import Deps  # noqa: E402
from intel.mcp_server.gateway import SERVERS, build_gateway, check_unique_names, instructions, profiles  # noqa: E402
from intel.mcp_server.kit.audit import AuditLog  # noqa: E402
from intel.rag import pack as kp  # noqa: E402
from tests.test_exposure import BUNDLES  # noqa: E402
from tests.test_mcp_brand import _sources  # noqa: E402
from tests.test_mcp_capabilities import FakeApi, _model  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BUILT = [
    "brand_rules",
    "check_drafts",
    "community_areas",
    "competitor_complaints",
    "evaluate_drafts",
    "find_creators",
    "funnel_status",
    "get_facts",
    "get_sources",
    "grounding_pack",
    "model_status",
    "score_drafts",
    "search_brand",
    "search_posts",
    "search_reviews",
    "trend_signals",
    "upcoming_occasions",
    "verify_citations",
]
OWNER_ONLY = {"score_drafts", "model_status"}


@pytest.fixture
def deps(tmp_path) -> Deps:
    mip, market = _sources(tmp_path)
    packs = tmp_path / "knowledge"
    kp.activate(packs, kp.build_brand_pack(mip, market, packs, now=dt.datetime(2026, 10, 9, tzinfo=dt.UTC)))
    return Deps(
        api=FakeApi(),
        brand=PackBrandSource(packs),
        audit=AuditLog(tmp_path / "audit"),
        principal="stdio:test",
        kinds={"content_scorer": "scorer"},
    )


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


async def _list(gw):  # type: ignore[no-untyped-def]
    async with Client(gw) as c:
        return await c.list_tools()


def test_owner_sees_every_built_tool_sorted_read_only_and_labelled(deps):
    tools = _run(_list(build_gateway("owner", deps)))
    assert [t.name for t in tools] == BUILT
    for t in tools:
        assert t.annotations and t.annotations.readOnlyHint and not t.annotations.destructiveHint
        assert t.meta and t.meta["dk/sensitivity"] in {"S0", "S1", "S2"} and t.meta["dk/contract"] == "1.0"
        assert t.description.startswith("Use when") and len(t.description) <= 600
        assert len(f"mcp__dk_intel_http__{t.name}") <= 64


def test_tool_names_are_unique_across_servers(deps):
    _run(check_unique_names([build(deps) for build in SERVERS.values()]))


def test_profiles_cover_every_tool_and_only_known_tools():
    planned = set(profiles()["owner"]["tools"])
    assert set(BUILT) <= planned
    roles = set().union(*(set(p["tools"]) for name, p in profiles().items() if name != "owner"))
    assert set(BUILT) - OWNER_ONLY <= roles
    for p in profiles().values():
        assert set(p["tools"]) <= planned  # owner is the union


@pytest.mark.parametrize(
    ("profile", "visible"),
    [
        (
            "writer",
            [
                "brand_rules",
                "evaluate_drafts",
                "get_facts",
                "grounding_pack",
                "search_brand",
                "search_posts",
                "verify_citations",
            ],
        ),
        (
            "checker",
            ["check_drafts", "evaluate_drafts", "get_facts", "get_sources", "search_brand", "verify_citations"],
        ),
        ("producer", ["brand_rules", "search_brand", "search_posts", "upcoming_occasions"]),
        (
            "analyst",
            [
                "community_areas",
                "competitor_complaints",
                "find_creators",
                "funnel_status",
                "get_facts",
                "get_sources",
                "search_brand",
                "search_posts",
                "search_reviews",
                "trend_signals",
                "verify_citations",
            ],
        ),
    ],
)
def test_roles_see_and_may_call_only_their_tools(deps, profile, visible):
    async def go():
        async with Client(build_gateway(profile, deps)) as c:
            assert [t.name for t in await c.list_tools()] == visible
            with pytest.raises(ToolError, match="NOT_ALLOWED_FOR_PROFILE"):
                await c.call_tool("score_drafts", {"drafts": [{"platform": "instagram", "caption": "x"}]})

    _run(go())


def test_scoring_through_the_gateway_and_the_audit_chain(deps):
    async def go():
        async with Client(build_gateway("owner", deps)) as c:
            r = await c.call_tool(
                "score_drafts",
                {
                    "drafts": [
                        {"platform": "instagram", "caption": "Kacchi", "planned_posted_at": "2026-10-14T17:00:00+02:00"}
                    ]
                },
            )
            assert r.structured_content["ranked"][0]["rank"] == 1
            with pytest.raises(ToolError, match="needs a timezone"):
                await c.call_tool(
                    "score_drafts",
                    {"drafts": [{"platform": "instagram", "caption": "x", "planned_posted_at": "2026-10-14T17:00:00"}]},
                )

    _run(go())
    entries = list(deps.audit.entries())
    assert [(e["name"], e["status"]) for e in entries] == [("score_drafts", "ok"), ("score_drafts", "error")]
    assert all(len(e["args"]) == 16 and "Kacchi" not in json.dumps(e) for e in entries)  # arguments: hash only
    assert deps.audit.verify() == {"ok": True, "entries": 2, "first_bad": None}


def test_denials_are_audited_too(deps):
    async def go():
        async with Client(build_gateway("writer", deps)) as c:
            with pytest.raises(ToolError):
                await c.call_tool("model_status", {})

    _run(go())
    (e,) = deps.audit.entries()
    assert e["status"] == "denied" and e["profile"] == "writer"


def test_expected_failures_reach_the_client_as_codes(deps):
    deps.api = FakeApi(models={})

    async def go():
        async with Client(build_gateway("owner", deps)) as c:
            with pytest.raises(ToolError, match=r"\[MODEL_NOT_ACTIVE\]"):
                await c.call_tool("funnel_status", {})

    _run(go())


def test_kill_switch_hides_and_refuses_a_tool(deps):
    deps.disabled_tools = frozenset({"score_drafts"})

    async def go():
        async with Client(build_gateway("owner", deps)) as c:
            assert "score_drafts" not in [t.name for t in await c.list_tools()]
            with pytest.raises(ToolError, match="FEATURE_OFF"):
                await c.call_tool("score_drafts", {"drafts": [{"platform": "instagram", "caption": "x"}]})

    _run(go())


def test_copy_guard_withholds_our_copy_but_never_untrusted_quotes(deps):
    planner = json.loads(json.dumps(BUNDLES["occasion_planner"]))
    soon = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    planner["picks"][0].update(label="Diwali forbiddenword", first_day=soon, last_day=soon, start_posting=soon)
    miner = json.loads(json.dumps(BUNDLES["weakness_miner"]))
    miner["overall"][0]["complaints"] = ["a forbiddenword quote"]
    models = {n: _model(n) for n in BUNDLES}
    models["occasion_planner"]["bundle"]["insights"] = planner
    models["weakness_miner"]["bundle"]["insights"] = miner
    deps.api = FakeApi(models=models)

    async def go():
        async with Client(build_gateway("owner", deps)) as c:
            occ = (await c.call_tool("upcoming_occasions", {"days": 30})).structured_content
            comp = (await c.call_tool("competitor_complaints", {})).structured_content
            return occ, comp

    occ, comp = _run(go())
    assert [o["label"] for o in occ["occasions"]] == [None]
    assert "copy_guard withheld" in occ["warnings"][0] and occ["occasions"][0]["greeting"]["text"] == "Happy Diwali"
    assert comp["aspects"][0]["examples"][0]["text"] == "a forbiddenword quote"  # evidence, marked untrusted
    assert any(e.get("kind") == "copy_guard_hit" for e in deps.audit.entries())


def test_brand_resources_are_readable(deps):
    async def go():
        async with Client(build_gateway("owner", deps)) as c:
            uris = sorted(str(r.uri) for r in await c.list_resources())
            book = await c.read_resource("dk://brand/book")
            return uris, book

    uris, book = _run(go())
    assert uris == ["dk://brand/book", "dk://brand/facts", "dk://brand/voice"]
    assert "Brand book" in book[0].text


def test_instructions_fit_and_contain_the_key_rules():
    text = instructions()
    assert len(text) <= 2048 and "untrusted" in text and "allergen" in text


# ---------- across processes ----------


def _append_many(directory: str, n: int, tag: str) -> None:
    log = AuditLog(Path(directory))
    for i in range(n):
        log.append(kind="tool", name=f"{tag}{i}", status="ok")


def test_the_audit_chain_survives_two_processes_appending_at_once(tmp_path):
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_append_many, args=(str(tmp_path), 150, tag)) for tag in "ab"]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    log = AuditLog(tmp_path)
    assert log.verify() == {"ok": True, "entries": 300, "first_bad": None}
    lines = log.path.read_text().splitlines()
    lines[10] = lines[10].replace('"ok"', '"denied"')
    log.path.write_text("\n".join(lines) + "\n")
    assert log.verify()["first_bad"] == 11


def _hold_slot(directory: str, seconds: float, ready) -> None:  # type: ignore[no-untyped-def]
    import time

    with ScoringLimits(Path(directory)).slot():
        ready.set()
        time.sleep(seconds)


def test_two_scoring_slots_are_shared_across_processes(tmp_path):
    ctx = mp.get_context("spawn")
    events = [ctx.Event() for _ in range(2)]
    procs = [ctx.Process(target=_hold_slot, args=(str(tmp_path), 3.0, e)) for e in events]
    for p in procs:
        p.start()
    for e in events:
        assert e.wait(30)
    with pytest.raises(ToolProblem) as err, ScoringLimits(tmp_path, wait_s=0.3).slot():
        pass
    assert err.value.code == "RATE_LIMITED"
    for p in procs:
        p.join(30)
    with ScoringLimits(tmp_path, wait_s=0.3).slot() as i:
        assert i in (0, 1)


def test_the_draft_budget_is_per_principal_and_per_minute(tmp_path):
    limits = ScoringLimits(tmp_path)
    limits.spend("a", 590)
    with pytest.raises(ToolProblem, match="590 of 600"):
        limits.spend("a", 20)
    limits.spend("b", 600)


# ---------- the committed .mcp.json ----------


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_the_committed_mcp_json_command_starts_and_lists_tools(tmp_path):
    from fastmcp.client.transports import StdioTransport

    entry = json.loads((REPO / ".mcp.json").read_text())["mcpServers"]["dk_intel"]
    args = [a.replace("${CLAUDE_PROJECT_DIR:-.}", str(REPO)) for a in entry["args"]]
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO), "INTEL_DATA_DIR": str(tmp_path)}
    transport = StdioTransport(command=entry["command"], args=args, env=env, cwd=str(tmp_path))

    async def go():
        async with Client(transport, timeout=60) as c:
            return [t.name for t in await c.list_tools()]

    assert _run(go()) == BUILT
    assert subprocess.run(["git", "check-ignore", "-q", ".mcp.json"], cwd=REPO).returncode == 1  # committed


def test_the_checker_can_never_ask_for_review_text(deps):
    async def go():
        async with Client(build_gateway("checker", deps)) as c:
            tool = next(t for t in await c.list_tools() if t.name == "get_sources")
            assert "include_text" not in tool.inputSchema.get("properties", {})
            with pytest.raises(ToolError, match="get_sources"):  # refused (details masked), never forwarded
                await c.call_tool("get_sources", {"cites": ["R-AAAAAAAA"], "include_text": True})

    _run(go())


def test_evidence_tools_say_unavailable_without_a_knowledge_database(deps):
    async def go():
        async with Client(build_gateway("owner", deps)) as c:
            with pytest.raises(ToolError, match=r"\[UPSTREAM_UNAVAILABLE\].*knowledge database"):
                await c.call_tool("search_reviews", {"query": "kaltes Essen"})
            prompt = await c.get_prompt("cited_answer", {"scope": "reviews"})
            assert "verify_citations" in prompt.messages[0].content.text

    _run(go())
