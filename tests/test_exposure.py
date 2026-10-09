"""What insights data may leave intel-api (intel.exposure) and the read-only insights payload (ADR 0025).

The synthetic bundles mirror the real bundle shapes of 2026-10-09, including every restricted field, so a leak shows
up here. When real local bundles exist (data/packages, Mac only), they are checked too.
"""

import glob
import json
import tarfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from intel.exposure import RESTRICTED, SECTIONS, leaked_keys, project, section_data, suppress_small_counts
from intel.serve.read_api import MAX_BYTES, insights_payload

REPO = Path(__file__).resolve().parents[1]

AREA = {
    "area_id": "0101",
    "name": "Wedding Zentrum",
    "district": "Mitte",
    "score": 0.81,
    "reach_band": "≤ 5 km · €5",
    "distance_km": 1.2,
    "gap": True,
    "delivery_listings": 12,
    "biryani_listings": 0,
    "audience": 0.9,
    "south_asian": 120,
    "meat_rice_cultures": 900,
    "internationals": 3000,
    "young_small_households": 400,
    "residents": 9000,
    "orders": 2,
    "lat": 52.55,
    "lon": 13.36,
}
BUNDLES = {
    "community_map": {
        "areas": [AREA, {**AREA, "area_id": "0102", "district": "Neukölln", "gap": False}],
        "districts": [
            {
                "district": "Mitte",
                "areas": 10,
                "score": 0.5,
                "biryani_listings": 3,
                "gaps": 2,
                "distance_km": 2.0,
                "south_asian": 5000,
                "meat_rice_cultures": 9,
                "internationals": 9,
                "orders": 4,
            }
        ],
        "weights": {"south_asian": 0.3},
        "top_audience": ["0101"],
        "orders_located": 5,
        "located": 0.8,
        "n_areas": 542,
        "origin": {"name": "Leopoldplatz", "lat": 52.5, "lon": 13.3},
        "reach": [{"label": "≤ 5 km · €5", "max_km": 5, "factor": 1.0}],
        "top20": ["0101"],
    },
    "occasion_planner": {
        "from": "2026-10-10",
        "to": "2026-11-09",
        "days_covered": 30,
        "calendar_until": "2027-12-31",
        "picks": [
            {
                "public_id": "diwali",
                "label": "Diwali",
                "names": ["Diwali"],
                "greeting": "Happy Diwali",
                "greeting_translit": None,
                "greeting_needs_review": False,
                "first_day": "2026-11-08",
                "last_day": "2026-11-08",
                "start_posting": "2026-11-01",
                "food_role": "feast",
                "tone": "festive",
                "bridge_dishes": ["biryani"],
                "hashtags": ["#diwali"],
                "estimated_date": False,
                "market_lift": None,
                "community": "Indian",
                "community_id": "in",
                "people": 30000,
                "districts": ["Mitte"],
                "score": 0.7,
            }
        ],
        "respectful_notes": [
            {"day": "2026-10-20", "label": "x", "advice": "no sales posts", "community": "c", "names": ["n"]}
        ],
        "berlin_events": [{"event": "Festival of Lights", "days": ["2026-10-10"]}],
        "timing": {
            "posts": 40,
            "weekday": [{"platform": "instagram", "weekday": "Fri", "above_recent": 0.6, "posts": 9}],
            "band": [{"platform": "instagram", "band": "18-21", "above_recent": 0.5, "posts": 7}],
        },
    },
    "funnel": {
        "period": ["2026-06-01", "2026-10-08"],
        "sessions": 900,
        "unknown_share": 0.4,
        "tracked_links": 1,
        "totals": {"visit": 900, "menu": 300, "cart": 40, "checkout": 20, "submitted": 12, "ordered": 3},
        "advice": ["Use tracked links."],
        "channels": [
            {"channel": "instagram", "visit": 90, "menu": 9, "cart": 2, "checkout": 1, "submitted": 1, "ordered": 1}
        ],
        "posts": [
            {
                "platform": "instagram",
                "posted_at": "2026-10-01T10:00:00",
                "format": None,
                "caption": "Kacchi",
                "sessions": 5,
                "carts": 1,
                "orders": 0,
            }
        ],
        "checkout_friction": {
            "reached_checkout": 20,
            "submitted": 12,
            "stuck_sessions": 8,
            "with": {"login failed": 2},
        },
        "orders_by_channel": {"direct / unknown": 3},
        "weekly": [{"channel": "x", "sessions": 1, "week": "2026-W40"}],
        "utm_content_sessions": {"bio_link": 1},
    },
    "creator_matcher": {
        "candidates": 865,
        "eligible": 30,
        "business_like": 25,
        "by_size": {"nano (1k-10k views)": 10},
        "top": [
            {
                "handle": "foodie",
                "url": "https://www.tiktok.com/@foodie",
                "size": "nano",
                "score": 0.8,
                "berlin_share": 0.9,
                "median_views": 3000,
                "engagement_rate": 0.05,
                "posts_90d": 12,
                "last_post": "2026-10-01",
                "topics": ["food"],
                "examples": [{"url": "https://x", "caption": "yum", "views": 4000}],
                "name": "A Person",
                "followers": None,
            }
        ],
        "south_asian_specialists": [],
        "businesses": [{"handle": "shop", "url": "u", "median_views": 1, "posts_90d": 1, "topics": []}],
        "weights": {"activity": 0.2},
        "size_factor": [],
    },
    "weakness_miner": {
        "period": ["2025-01-01", "2026-10-01"],
        "reviews": 13698,
        "listings": 60,
        "clauses": 40000,
        "clauses_with_aspect": 0.4,
        "overall": [
            {
                "aspect": "delivery_time",
                "label": "Delivery time",
                "mentions": 900,
                "negative": 500,
                "negative_rate": 0.55,
                "positive_rate": 0.3,
                "share_of_reviews": 0.1,
                "complaints": ["took 2 hours"],
                "praise": ["fast"],
                "our_angle": "We cook to order",
            }
        ],
        "biryani_mentions": [],
        "districts": [
            {
                "district": "Mitte",
                "listings": 9,
                "reviews": 900,
                "top_complaints": [{"aspect": "a", "label": "A", "mentions": 3, "negative_rate": 0.5}],
            }
        ],
        "competitors": [
            {
                "listing_id": "l1",
                "name": "Place",
                "district": "Mitte",
                "reviews": 90,
                "rating": 4.1,
                "biryani_items": 3,
                "weak": [{"aspect": "a", "label": "A", "mentions": 3, "negative_rate": 0.5}],
                "strong": [],
            }
        ],
    },
}


def _model(name: str, insights: dict, kind: str = "insights") -> dict:
    return {"version": f"{name[:2]}-v1", "package_id": "pkg-1", "bundle": {"kind": kind, "insights": insights}}


def _all_sections():
    return [(name, sec) for name, secs in SECTIONS.items() for sec in secs]


@pytest.mark.parametrize(("name", "section"), _all_sections())
def test_no_restricted_field_leaves_any_section(name, section):
    data = section_data(name, section, BUNDLES[name])
    assert leaked_keys(data) == set()
    assert "lat" not in json.dumps(data) or name != "community_map"


def test_every_bundle_fixture_really_contains_restricted_fields():
    """Guards the guard: if the fixtures lost their restricted fields, the test above would prove nothing."""
    assert leaked_keys(BUNDLES["community_map"]) >= {"south_asian", "audience", "weights", "top_audience"}
    assert leaked_keys(BUNDLES["occasion_planner"]) >= {"community", "community_id", "people"}
    assert leaked_keys(BUNDLES["creator_matcher"]) >= {"followers"}


def test_community_areas_keep_score_and_gap_but_not_orders_or_coordinates():
    (area, _) = section_data("community_map", "areas", BUNDLES["community_map"])
    assert area["score"] == 0.81 and area["gap"] is True
    assert {"orders", "lat", "lon", "residents", "young_small_households"}.isdisjoint(area)


def test_funnel_order_counts_below_five_are_suppressed():
    summary = section_data("funnel", "summary", BUNDLES["funnel"])
    assert summary["totals"]["ordered"] == "<5" and summary["totals"]["visit"] == 900
    (ch,) = section_data("funnel", "channels", BUNDLES["funnel"])
    assert ch["ordered"] == "<5"
    (post,) = section_data("funnel", "posts", BUNDLES["funnel"])
    assert post["orders"] == 0  # zero is not identifying
    assert suppress_small_counts({"orders": 5}) == {"orders": 5}


def test_project_is_an_allowlist_recursively():
    assert project({"a": 1, "b": {"c": 2, "d": 3}, "e": 4}, {"a": True, "b": {"c": True}}) == {"a": 1, "b": {"c": 2}}
    assert project([{"a": 1, "z": 2}], {"a": True}) == [{"a": 1}]


def test_payload_sections_paging_and_district_filter():
    m = _model("community_map", BUNDLES["community_map"])
    index = insights_payload("community_map", m, None)
    assert set(index["sections"]) == {"summary", "areas", "districts"}
    page = insights_payload("community_map", m, "areas", limit=1, offset=1)
    assert page["total"] == 2 and len(page["items"]) == 1 and page["items"][0]["area_id"] == "0102"
    mitte = insights_payload("community_map", m, "areas", district="mitte")
    assert [i["area_id"] for i in mitte["items"]] == ["0101"]
    summary = insights_payload("community_map", m, "summary")
    assert summary["data"] == {"n_areas": 542, "reach": [{"label": "≤ 5 km · €5", "max_km": 5, "factor": 1.0}]}


def test_payload_refuses_unknown_inactive_and_scorer_models():
    with pytest.raises(HTTPException) as e:
        insights_payload("nope", None, None)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        insights_payload("funnel", None, "summary")
    assert e.value.status_code == 404 and e.value.detail == "not_active"
    with pytest.raises(HTTPException) as e:
        insights_payload("funnel", _model("funnel", {}, kind="scorer"), "summary")
    assert e.value.detail == "not_active"
    with pytest.raises(HTTPException) as e:
        insights_payload("funnel", _model("funnel", BUNDLES["funnel"]), "bogus")
    assert e.value.status_code == 422


def test_payload_is_capped_at_64_kb():
    big = {**BUNDLES["community_map"], "areas": [{**AREA, "name": "x" * 2000, "area_id": str(i)} for i in range(200)]}
    body = insights_payload("community_map", _model("community_map", big), "areas", limit=200)
    assert len(json.dumps(body)) <= MAX_BYTES and body["truncated"] and body["items"] and body["total"] == 200


def test_restricted_set_names_every_rule_10_field():
    assert {"south_asian", "meat_rice_cultures", "internationals", "audience", "community", "people"} <= RESTRICTED


def _local_bundles():
    joblib = pytest.importorskip("joblib")
    found = {}
    for name in SECTIONS:
        packages = sorted(glob.glob(str(REPO / "data" / "packages" / f"{name}-*.tar.gz")))
        if not packages:
            continue
        with tarfile.open(packages[-1]) as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("model.joblib"))
            fh = tar.extractfile(member)
            assert fh is not None
            found[name] = joblib.load(fh)
    return found


def test_real_local_bundles_leak_nothing():
    bundles = _local_bundles()
    if not bundles:
        pytest.skip("no local packages (CI); runs on the Mac")
    for name, bundle in bundles.items():
        for section in SECTIONS[name]:
            data = section_data(name, section, bundle["insights"])
            assert leaked_keys(data) == set(), (name, section)
            assert data not in ([], {}), f"{name}.{section} is empty: the bundle shape changed?"


def test_routes_without_a_database(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from intel.serve import read_api

    models = {"funnel": _model("funnel", BUNDLES["funnel"])}
    app = FastAPI()
    read_api.register_read_routes(app, models, "dsn-unused", "on", lambda: [{"action": "none"}])
    store: dict = {}
    monkeypatch.setattr(read_api, "kv_get", lambda dsn, key: store.get(key))
    c = TestClient(app)

    first = c.get("/v1/monitoring/latest").json()
    assert first == {"status": "not_computed_yet", "guardrail": {"mode": "on", "decisions": [{"action": "none"}]}}
    store["monitoring"] = {"at": "2026-10-09T10:00:00+00:00", "models": {}}
    latest = c.get("/v1/monitoring/latest").json()
    assert latest["status"] == "ok" and latest["at"] == "2026-10-09T10:00:00+00:00"

    r = c.get("/v1/insights/funnel", params={"section": "channels"})
    assert r.status_code == 200 and r.headers["etag"] == '"pkg-1"' and r.json()["items"][0]["ordered"] == "<5"
    assert c.get("/v1/insights/weakness_miner", params={"section": "overall"}).status_code == 404
    assert c.get("/v1/insights/funnel", params={"section": "channels", "limit": 500}).status_code == 422
