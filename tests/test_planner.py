"""Occasion planner (content rules, audience counting) and auto-refresh of approved insights."""

import datetime as dt
import json
import shutil
from pathlib import Path

import joblib
import pandas as pd
import pytest

from intel.models.occasion_planner.run import all_strings, plan, present

RULES = {
    "blocked_words": ["puja", "durga", "bijoya", "পূজা"],
    "occasion_presentation": {
        "durga_puja": {
            "id": "indian_festive_season",
            "label": "Indian festive season",
            "community": "indian",
            "greeting": {"text": "Festive wishes"},
            "hashtags": ["festiveseason"],
        }
    },
    "occasion_audience": {"durga_puja": {"share": {"indian": 1.0, "bangladeshi": 0.0}}},
    "home_community": {"id": "bangladeshi", "weight": 1.0},
}
TODAY = dt.date(2026, 10, 8)


def occ(**kw):
    base = {
        "day": TODAY + dt.timedelta(days=10),
        "community_id": "indian",
        "community": "Indian",
        "occasion_type": "durga_puja",
        "holiday_names": ["Durga Puja"],
        "greeting": "শুভ বিজয়া",
        "greeting_translit": "Shubho Bijoya",
        "greeting_needs_review": False,
        "food_role": "feast",
        "tone": "celebratory",
        "content_window_start": TODAY + dt.timedelta(days=1),
        "berlin_citizens": 49504,
        "countries": None,
        "bridge_dishes": ["Kolkata biryani — x"],
        "top_districts_mh": None,
        "is_estimated": False,
        "berlin_events": None,
    }
    return base | kw


def test_religious_occasion_is_presented_neutrally():
    t = present(occ(), RULES)
    assert t["label"] == "Indian festive season" and t["public_id"] == "indian_festive_season"
    assert t["greeting"] == "Festive wishes" and t["names"] == ["Indian festive season"]
    assert not any(w in s.lower() for s in all_strings(t) for w in RULES["blocked_words"])


def test_blocked_greeting_dropped_and_nan_is_none():
    t = present(occ(occasion_type="other_feast", holiday_names=["Bijoya day"], greeting="Bijoya!"), RULES)
    assert t["greeting"] is None and t["names"] == ["other feast"]
    assert (
        present(occ(occasion_type="x", greeting=float("nan"), greeting_translit=float("nan")), RULES)["greeting"]
        is None
    )


def test_plan_counts_only_the_countries_that_celebrate():
    rows = [
        occ(),
        occ(
            community_id="anglo",
            community="Anglo",
            occasion_type="thanksgiving",
            holiday_names=["Thanksgiving"],
            greeting="Happy Thanksgiving!",
            countries=["CA"],
            berlin_citizens=47572,
        ),
        occ(
            community_id="thai",
            occasion_type="remembrance_day",
            food_role="remembrance",
            tone="solemn",
            holiday_names=["Memorial"],
            greeting=None,
        ),
        occ(day=TODAY + dt.timedelta(days=45)),
    ]  # outside the 30 days
    p = plan(pd.DataFrame(rows), RULES, TODAY, {}, {"anglo": {"US": 20000, "GB": 25000, "CA": 1800}})
    labels = [x["label"] for x in p["picks"]]
    assert labels == ["Indian festive season"]  # 1,800 Canadians < 5,000
    assert p["respectful_notes"][0]["advice"].startswith("respectful")
    assert p["picks"][0]["start_posting"] == (TODAY + dt.timedelta(days=1)).isoformat()


def test_leak_detector_finds_hidden_words():
    assert any("puja" in s for s in all_strings({"picks": [{"public_id": "durga_puja"}]}))


def test_coming_up_lines():
    from intel.serve.runs import coming_up_lines

    ins = {
        "from": "2026-10-08",
        "picks": [
            {
                "label": "Indian festive season",
                "community_id": "indian",
                "people": 49504,
                "first_day": "2026-10-18",
                "start_posting": "2026-10-08",
            },
            {
                "label": "later",
                "community_id": "x",
                "people": 1,
                "first_day": "2026-11-30",
                "start_posting": "2026-11-27",
            },
        ],
        "respectful_notes": [{"day": "2026-10-13", "label": "remembrance day", "community": "thai"}],
    }
    lines = coming_up_lines({"insights": ins})
    assert len(lines) == 2 and "49,504" in lines[0] and "respectful" in lines[1]


# ---------- auto-refresh: insights only, and only after a HUMAN approved that insight once ----------


def insights_run(tmp: Path, version: str) -> Path:
    run = tmp / f"run_{version}"
    run.mkdir()
    joblib.dump({"kind": "insights", "version": version, "insights": {"areas": []}}, run / "model.joblib")
    gates = [{"gate": "data", "status": "pass", "detail": "ok"}]
    s = {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": 1}
    (run / "report.json").write_text(
        json.dumps(
            {
                "kind": "community_map_run",
                "version": version,
                "pull_id": "P",
                "market_id": "M",
                "split": {"train": 0, "test": 1},
                "judge": {"verdict": "pass", "gates": gates},
                "challenger": {"model": "m", "params": {}, "features": "f", "threshold": None, "test": s},
                "champion": {"model": "none", "test": s},
                "top_complaints": ["Area A"],
            }
        )
    )
    return run


def test_auto_refresh_after_first_human_approval(db, tmp_path):
    from intel.package import build_package
    from intel.serve.registry import Registry

    repo = Path(__file__).resolve().parents[1]
    reg = Registry(db, tmp_path / "inbox", tmp_path / "store")
    reg.inbox.mkdir()

    def drop(v):
        tgz = build_package(insights_run(tmp_path, v), tmp_path / "pkg", repo)
        shutil.copy(tgz, reg.inbox)
        shutil.copy(tgz.with_name(tgz.name + ".sha256"), reg.inbox)

    drop("cm-1")
    (first,) = reg.scan_inbox()
    assert first.status == "pending_approval"  # first version: the owner decides
    reg.approve(first.approval_id, "owner", "telegram")
    drop("cm-2")
    (second,) = reg.scan_inbox()
    assert second.status == "auto_approved" and reg.active()["community_map"]["version"] == "cm-2"
    assert reg.rollback("community_map", "owner", "telegram")["version"] == "cm-1"


def test_scorers_are_never_auto_approved(db, tmp_path):
    from intel.serve.registry import Registry
    from tests.test_serve import drop

    reg = Registry(db, tmp_path / "inbox", tmp_path / "store")
    reg.inbox.mkdir()
    drop(tmp_path, reg.inbox, "cs-a")
    (a,) = reg.scan_inbox()
    reg.approve(a.approval_id, "owner", "telegram")
    drop(tmp_path, reg.inbox, "cs-b")
    (b,) = reg.scan_inbox()
    assert b.status == "pending_approval"


@pytest.fixture
def db():
    from tests.test_serve import db as _db

    yield from _db.__wrapped__()
