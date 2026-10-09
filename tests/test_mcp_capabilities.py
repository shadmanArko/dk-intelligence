"""The capability core with fakes: scoring, status and the five insights tools (no network, no MCP)."""

import datetime as dt
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from intel.capabilities import insights, scoring, status
from intel.capabilities.types import Draft, ToolProblem
from intel.serve.read_api import insights_payload
from tests.test_exposure import BUNDLES

T = "2026-10-14T17:00:00+02:00"


class FakeApi:
    """Answers like intel-api: insights through the real route function, scoring from a canned table."""

    def __init__(self, models: dict[str, Any] | None = None, deployed: bool = True) -> None:
        self.models = models if models is not None else {n: _model(n) for n in BUNDLES}
        self.deployed = deployed
        self.calls: list[tuple[str, Any]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, params))
        params = params or {}
        if path == "/ready":
            return {"version": "abc", "models": {"content_scorer": "cs-1", "funnel": "fn-1"}}
        if path == "/v1/monitoring/latest":
            if not self.deployed:
                raise ToolProblem("UPSTREAM_UNAVAILABLE", "/v1/monitoring/latest is not deployed on intel-api yet")
            return {"status": "ok", "at": "2026-10-09T10:00:00+00:00", "retrain_requested": False, "reasons": []}
        if path == "/v1/ledger/summary":
            return {"posts": 6, "outcomes": 1, "models": {"content_scorer": {"n": 1, "accuracy": 1.0}}}
        if path == "/api/training-state":
            return {"history": [{"run_id": "r2", "status": "pass"}, {"run_id": "r1", "status": "fail"}]}
        if path == "/v1/audit":
            return {
                "chain": {"ok": True},
                "packages": [{"name": "funnel", "version": "fn-2", "stage": "candidate", "received_at": "x"}],
                "entries": [
                    {"at": "t", "actor": "secret@login", "channel": "telegram", "action": "approve", "subject": "s"}
                ],
            }
        if path.startswith("/v1/insights/"):
            name = path.rsplit("/", 1)[-1]
            try:
                return insights_payload(
                    name,
                    self.models.get(name),
                    params.get("section"),
                    params.get("district"),
                    params.get("limit", 50),
                    params.get("offset", 0),
                )
            except HTTPException as e:
                if e.detail == "not_active":
                    raise ToolProblem("MODEL_NOT_ACTIVE", f"{name} isn't approved and active yet") from e
                raise
        raise AssertionError(path)

    def score_batch(self, drafts: list[dict[str, Any]], principal: str) -> dict[str, Any]:
        self.calls.append(("score", drafts))
        preds = [
            {
                "probability": 0.45 + 0.1 * i,
                "label": int(0.45 + 0.1 * i >= 0.5),
                "threshold": 0.5,
                "model_version": "cs-1",
                "top_reasons": [{"feature": "nominal__platform_instagram", "contribution": 0.2}],
            }
            for i, _ in enumerate(drafts)
        ]
        return {"drafts": len(drafts), "models": {"content_scorer": preds}}


def _model(name: str) -> dict[str, Any]:
    return {"version": f"{name}-v1", "package_id": "p1", "bundle": {"kind": "insights", "insights": BUNDLES[name]}}


def _draft(**kw: Any) -> Draft:
    return Draft(**{"platform": "instagram", "caption": "Kacchi Berlin", "planned_posted_at": T, **kw})


# ---------- scoring ----------


def test_score_ranks_scorable_drafts_and_explains_the_rest():
    api = FakeApi()
    out = scoring.score_drafts(
        api,
        [_draft(ref="a"), _draft(ref="b"), _draft(ref="t", platform="tiktok"), _draft(ref="n", planned_posted_at=None)],
        "stdio:test",
    )
    assert [r["ref"] for r in out["ranked"]] == ["b", "a"] and [r["rank"] for r in out["ranked"]] == [1, 2]
    assert out["ranked"][0]["scores"]["content_scorer"]["label"] == "likely_above_typical"
    assert out["ranked"][0]["reasons"] == ["+ Platform: instagram"]
    assert {n["ref"]: n["reason"] for n in out["not_scored"]} == {
        "t": "no scorer for tiktok; use check_drafts",
        "n": "needs planned_posted_at with a timezone",
    }
    (_, sent) = api.calls[-1]
    assert len(sent) == 2  # tiktok and the undated draft are never forwarded (they'd get a 422)
    assert out["model_versions"] == {"content_scorer": "cs-1"}


def test_detailed_reasons_carry_feature_and_contribution():
    out = scoring.score_drafts(FakeApi(), [_draft()], "p", "detailed")
    (reason,) = out["ranked"][0]["reasons"]
    assert reason == {"reason": "Platform: instagram", "feature": "nominal__platform_instagram", "contribution": 0.2}


def test_a_time_without_timezone_is_refused_with_an_example():
    with pytest.raises(ValidationError, match="needs a timezone, e.g. 2026-10-14T16:30:00"):
        Draft(platform="instagram", caption="x", planned_posted_at="2026-10-14T17:00:00")


def test_score_needs_1_to_50_drafts():
    with pytest.raises(ToolProblem) as e:
        scoring.score_drafts(FakeApi(), [], "p")
    assert e.value.code == "INVALID_ARGUMENT"


# ---------- status ----------


def test_status_without_the_new_monitoring_route_still_answers():
    out = status.model_status(FakeApi(deployed=False), {"content_scorer": "scorer", "funnel": "insights"})
    assert out["monitoring"] == {"status": "route_not_deployed"}
    assert out["models"][0] == {"name": "content_scorer", "kind": "scorer", "version": "cs-1", "health": "active"}
    assert out["last_training"] == {"run_id": "r2", "status": "pass"}
    assert out["pending_approval"] == [{"name": "funnel", "version": "fn-2", "received_at": "x"}]


def test_detailed_status_never_passes_on_the_actor():
    out = status.model_status(FakeApi(), {}, "detailed")
    assert out["audit_chain_ok"] is True and out["monitoring"]["status"] == "ok"
    assert out["recent_decisions"] == [{"at": "t", "action": "approve", "subject": "s", "actor_role": "owner"}]
    assert "secret@login" not in str(out)


# ---------- insights ----------


def test_upcoming_occasions_window_tone_and_timing():
    out = insights.upcoming_occasions(FakeApi(), days=30, platform="instagram", today=dt.date(2026, 10, 15))
    (occ,) = out["occasions"]
    assert occ["id"] == "diwali" and occ["sales_ok"] is True and occ["greeting"]["text"] == "Happy Diwali"
    assert "community" not in str(out) and "people" not in occ
    assert out["respectful_notes"] == [{"day": "2026-10-20", "label": "x", "advice": "no sales posts"}]
    assert out["timing"]["best_weekdays"][0]["weekday"] == "Fri"
    later = insights.upcoming_occasions(FakeApi(), days=3, today=dt.date(2026, 12, 1))
    assert later["occasions"] == [] and later["berlin_events"] == []


def test_upcoming_occasions_has_no_timing_for_other_platforms():
    out = insights.upcoming_occasions(FakeApi(), platform="tiktok", today=dt.date(2026, 10, 15))
    assert out["timing"]["reason"] == "no_timing_for_platform"


def test_competitor_complaints_marks_quotes_untrusted_and_filters():
    out = insights.competitor_complaints(FakeApi(), district="Mitte", competitor="Plce")
    (a,) = out["aspects"]
    assert (
        a["examples"] == [{"text": "took 2 hours", "trust": "untrusted"}] and a["angle_status"] == "suggestion, verify"
    )
    assert out["competitors"][0]["name"] == "Place" and out["competitors"][0]["weak"] == ["A"]
    with pytest.raises(ToolProblem) as e:
        insights.competitor_complaints(FakeApi(), aspect="smell")
    assert "delivery_time" in e.value.hint


def test_complaints_say_not_active_until_approved():
    api = FakeApi(models={})
    with pytest.raises(ToolProblem) as e:
        insights.competitor_complaints(api)
    assert e.value.code == "MODEL_NOT_ACTIVE"


def test_community_areas_sorting_gaps_and_method_note():
    out = insights.community_areas(FakeApi(), gaps_only=True)
    assert [a["name"] for a in out["areas"]] == ["Wedding Zentrum"] and out["districts"]
    assert "never to target or exclude people" in out["method_note"]
    for area in out["areas"]:
        assert {"audience", "south_asian", "orders", "lat"}.isdisjoint(area)
    with pytest.raises(ToolProblem) as e:
        insights.community_areas(FakeApi(), district="Atlantis")
    assert "Mitte" in e.value.hint


def test_funnel_sections_keep_small_counts_hidden():
    over = insights.funnel_status(FakeApi())
    assert over["totals"]["ordered"] == "<5"
    channels = insights.funnel_status(FakeApi(), "channels")
    assert channels["top_channels"][0]["ordered"] == "<5"
    posts = insights.funnel_status(FakeApi(), "posts")
    assert posts["top_posts"][0]["caption_excerpt"] == "Kacchi"


def test_find_creators_never_returns_names_or_followers():
    out = insights.find_creators(FakeApi(), include_examples=True)
    (c,) = out["creators"]
    assert c["handle"] == "foodie" and "name" not in c and "followers" not in c
    assert c["example"]["caption"] == {"text": "yum", "trust": "untrusted"}
    assert insights.find_creators(FakeApi(), topic="cars")["creators"] == []


def test_a_leaking_upstream_is_refused():
    class Leaky(FakeApi):
        def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
            body = super().get(path, params)
            if "items" in body:
                body["items"] = [{**i, "south_asian": 1} for i in body["items"]]
            return body

    with pytest.raises(ToolProblem, match="restricted fields"):
        insights.community_areas(Leaky())
