"""dk_performance: the three read-only tools over the playbook and our own posts' results."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastmcp import Client

from intel.adapters.performance import FilePerformanceSource
from intel.capabilities import performance
from intel.capabilities.types import ToolProblem
from intel.mcp_server.gateway import build_gateway
from intel.performance import playbook
from tests.test_mcp_gateway import deps  # noqa: F401 - deps is a fixture
from tests.test_performance import _frame


@pytest.fixture
def source(tmp_path: Path) -> FilePerformanceSource:
    rng = np.random.default_rng(3)
    data = pd.concat([_frame("instagram", 80, rng), _frame("youtube", 8, rng)])
    folder = playbook.write(tmp_path, playbook.build(data, "pull9"), data)
    pd.DataFrame([{"post_key": "2026w42-tue13-i1", "platform": "instagram", "live_url": "https://x", "collected": True,
                   "matured": True, "age_days": 9.0, "engagement": 4.0, "audience": 210.0, "lift_audience": 2.2,
                   "pillar": "food", "angle": "lid off", "hook_length": 12},
                  {"post_key": "2026w42-tue13-i1", "platform": "facebook", "live_url": "https://y", "collected": False}
                  ]).to_parquet(folder / "agent_posts.parquet")  # fmt: skip
    return FilePerformanceSource(tmp_path)


def test_the_playbook_tool_gives_lines_with_evidence_and_says_when_there_is_not_enough(source):
    ig = performance.platform_playbook(source, "instagram")
    assert ig["enough"] and ig["works"] and "median reach" in ig["works"][0] and ig["caveats"]
    assert ig["prov"]["version"] == "pull9" and "comparisons" in ig["status"]
    yt = performance.platform_playbook(source, "youtube")
    assert not yt["enough"] and "nothing is claimed" in yt["status"] and "works" not in yt
    assert "analytics" in performance.platform_playbook(source, "tiktok")["status"]


def test_by_feature_lists_every_value_with_its_interval_and_refuses_an_unknown_feature(source):
    out = performance.performance_by_feature(source, "instagram", "format")
    rows = {r["value"]: r for r in out["rows"]}
    assert rows["reel"]["verdict"] == "works" and rows["image"]["verdict"] == "hurts" and rows["reel"]["n"] == 40
    assert len(rows["reel"]["diff_ci"]) == 2 and "usual post" in out["reading"]
    with pytest.raises(ToolProblem, match="no feature"):
        performance.performance_by_feature(source, "instagram", "colour")
    assert performance.performance_by_feature(source, "youtube", "format")["rows"] == []


def test_a_published_post_shows_each_platform_and_what_we_knew_before_posting(source):
    out = performance.post_performance(source, "2026w42-tue13-i1")
    by = {v["platform"]: v for v in out["variants"]}
    assert by["instagram"]["lift_audience"] == 2.2 and by["instagram"]["pillar"] == "food"
    assert by["facebook"]["collected"] is False and "7 days" in out["note"]
    with pytest.raises(ToolProblem, match="no published post"):
        performance.post_performance(source, "nope")


def test_nothing_built_yet_is_a_clear_message_not_a_crash(tmp_path):
    empty = FilePerformanceSource(tmp_path)
    with pytest.raises(ToolProblem, match="intel performance build"):
        performance.platform_playbook(empty, "instagram")
    assert empty.agent_posts() == []


def test_the_planner_gets_the_playbook_and_the_checker_never_does(deps, source):  # noqa: F811
    d = dataclasses.replace(deps, performance=source)

    async def names(profile: str) -> list[str]:
        async with Client(build_gateway(profile, d)) as c:
            return [t.name for t in await c.list_tools()]

    async def call() -> dict[str, object]:
        async with Client(build_gateway("planner", d)) as c:
            r = await c.call_tool("platform_playbook", {"platform": "instagram"})
            return dict(r.structured_content or {})

    assert "platform_playbook" in asyncio.run(names("planner"))
    assert "performance_by_feature" not in asyncio.run(names("planner"))
    assert not {"platform_playbook", "post_performance"} & set(asyncio.run(names("checker")))
    assert asyncio.run(call())["platform"] == "instagram"
