"""Community map: distance bands, scoring, gaps, order location (counted only), page."""

import json

import pandas as pd
import pytest

from intel.models.community_map.run import build, km, reach_band, summarise


def test_distance_and_bands():
    assert km(52.5465, 13.3592, 52.5465, 13.3592) == 0
    assert 3.8 < km(52.5465, 13.3592, 52.5200, 13.4050) < 4.4  # Leopoldplatz -> Alexanderplatz ~4.1 km
    assert reach_band(4.9)["factor"] == 1.0 and reach_band(12)["factor"] == 0.6 and reach_band(40)["factor"] == 0.3


def test_religion_never_requested():
    from intel.market import LOR_STATS_SQL

    assert "religion" not in LOR_STATS_SQL


@pytest.fixture
def market(tmp_path):
    areas = ["01000001", "01000002", "12000003"]  # near, near, far
    comm = [
        {"area_code": a, "community_id": c, "residents": n, "reference_date": "2026-06-30"}
        for a, ns in zip(areas, [(900, 1500, 600), (50, 100, 40), (500, 900, 300)], strict=True)
        for c, n in zip(["indian", "turkish", "anglo"], ns, strict=True)
    ]
    stats = [
        {"area_id": a, "variable": v, "value": val, "reference_date": "2026-06-30"}
        for a in areas
        for v, val in [
            ("population.einwohner", 8000),
            ("age_10y.a20bis29", 1500),
            ("age_10y.a30bis39", 1500),
            ("household_size.1_person", 2000),
            ("household_size.2_personen", 1000),
            ("household_size.insgesamt_haushalte", 4000),
        ]
    ]
    places = pd.DataFrame(
        {
            "area_id": areas,
            "name": ["Near A", "Near B", "Far C"],
            "district": ["Mitte", "Mitte", "Spandau"],
            "lat": [52.55, 52.54, 52.53],
            "lon": [13.36, 13.37, 13.20],
            "listings": [10, 10, 10],
            "delivery_listings": [5, 5, 5],
            "biryani_listings": [0, 2, 0],
        }
    )
    pd.DataFrame(comm).to_parquet(tmp_path / "lor_community.parquet")
    pd.DataFrame(stats).to_parquet(tmp_path / "lor_stats.parquet")
    places.to_parquet(tmp_path / "lor_places.parquet")
    pd.DataFrame(
        {"postcode": ["13353", "13581"], "area_id": ["01000001", "12000003"], "district": ["Mitte", "Spandau"]}
    ).to_parquet(tmp_path / "postcode_area.parquet")
    pull = tmp_path / "pull"
    pull.mkdir()
    pd.DataFrame(
        {
            "payload": [
                json.dumps({"address_postal_code": "13353", "address_city": "Berlin"}),
                json.dumps({"address_postal_code": "13353"}),
                json.dumps({}),
            ]
        }
    ).to_parquet(pull / "raw_orders_direct.parquet")
    return tmp_path, pull


def test_scores_gaps_and_orders(market):
    m, pull = market
    df = build(m, pull).set_index("area_id")
    assert df.loc["12000003", "reach_band"].startswith("≤ 15") or df.loc["12000003", "distance_km"] > 10
    assert df.loc["01000001", "score"] > df.loc["01000002", "score"]  # bigger audience, same distance
    assert bool(df.loc["01000001", "gap"]) and not bool(df.loc["01000002", "gap"])  # B has biryani sellers
    assert df.loc["01000001", "orders"] == 2 and df["orders"].sum() == 2  # empty payload ignored
    ins = summarise(df.reset_index())
    assert ins["areas"][0]["area_id"] == "01000001" and ins["orders_located"] == 2
    assert ins["top_audience"][:2] == ["01000001", "12000003"]  # far C: 2nd audience, low reach
    json.dumps(ins)


def test_page_renders(market):
    from intel.serve.views import community_html

    m, pull = market
    ins = summarise(build(m, pull))
    page = community_html({"version": "cm-1", "insights": ins})
    assert "<svg" in page and "Near A" in page and "never to target or exclude people" in page
    assert "Not approved yet" in community_html(None)
