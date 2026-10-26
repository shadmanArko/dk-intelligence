"""The run report the Mac uploads must fit what the VPS validates, or the VPS drops it silently and decides the Mac
stopped training (2026-10-09 to 2026-10-26: `package` was capped at 160 characters; six models joined are longer)."""

import datetime as dt
import json
from pathlib import Path

from intel.serve.runs import RunReport, worth_telling

NOW = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.UTC)
MODELS = ["community_map", "content_scorer", "creator_matcher", "funnel", "occasion_planner", "weakness_miner"]
SIX_PACKAGES = ", ".join(f"{name}-20261026T0710{i:02d}-{'ab12cd' * 2}.tar.gz" for i, name in enumerate(MODELS))


def report(**kw):  # type: ignore[no-untyped-def]
    base = {"kind": "weekly_run", "format_version": 1, "run_id": "20261026T070917Z", "status": "pass",
            "started_at": "2026-10-26T07:09:17+00:00", "finished_at": "2026-10-26T07:11:30+00:00"}  # fmt: skip
    return base | kw


def test_a_run_with_six_packages_is_accepted():
    assert len(SIX_PACKAGES) > 160  # the length that used to be refused
    assert RunReport.model_validate(report(package=SIX_PACKAGES)).package == SIX_PACKAGES


def test_the_real_report_that_was_refused_now_validates():
    real = sorted(Path("data/weekly").glob("weekly-20261026T070917Z.run.json"))
    if real:  # the owner's Mac only: the run whose report the VPS dropped
        RunReport.model_validate(json.loads(real[0].read_text()))


def test_a_report_is_announced_only_while_it_is_news():
    fresh = RunReport.model_validate(report(finished_at="2026-10-26T07:11:30+00:00"))
    old = RunReport.model_validate(report(finished_at="2026-10-09T07:11:30+00:00"))
    assert worth_telling(fresh, NOW) and not worth_telling(old, NOW)
