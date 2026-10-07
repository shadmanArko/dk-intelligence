"""Competitor weakness miner: reading rules, owner check, sample hygiene, pages."""

import json

import pandas as pd
import pytest
import yaml

from intel.models.weakness_miner.mine import ASPECTS_FILE, analyse_text, is_design, mine
from intel.models.weakness_miner.run import check_metrics, check_sample, summarise


def first(text, rating=0.6):
    c = analyse_text(text, rating)
    return c[0]["aspects"], c[0]["polarity"]


def test_yaml_has_only_strings():
    d = yaml.safe_load(ASPECTS_FILE.read_text())
    words = [w for a in d["aspects"].values() for w in a["phrases"]] + [w for v in d["polarity"].values() for w in v]
    assert all(isinstance(w, str) for w in words)  # YAML turns bare no/yes/on into booleans


@pytest.mark.parametrize(
    "text,aspects,pol",
    [
        ("Kaltes Essen und trockener Reis", ["rice", "temperature"], "negative"),  # German endings
        ("Wässrige Salsa-Soße viel zu wenig", ["portion", "sauce_oil"], "negative"),  # hyphen + "zu wenig"
        ("Das Fleisch war nicht trocken", ["meat"], "positive"),  # negation flips
        ("Taste is not really good", ["taste"], "negative"),
        ("Lieferung kam nicht an", ["order_accuracy"], "negative"),  # never arrived = missing order
        ("die Verpackung jedes Mal eine Katastrophe", ["packaging"], "negative"),
        ("Wir waren zu Hause", [], "positive"),  # rating fallback (0.6 -> no)
    ],
)
def test_reading_rules(text, aspects, pol):
    a, p = first(text, rating=1.0 if text.startswith("Wir") else 0.6)
    assert a == aspects and p == pol


def test_but_splits_clauses():
    c = analyse_text("war lecker, aber schlecht verpackt und lauwarm angekommen", 0.6)
    assert [(x["aspects"], x["polarity"]) for x in c] == [
        (["taste"], "positive"),
        (["packaging", "temperature"], "negative"),
    ]


def _reviews(n=40):
    return pd.DataFrame(
        {
            "review_id": [f"r{i}" for i in range(n)],
            "listing_id": ["L1"] * (n // 2) + ["L2"] * (n // 2),
            "posted_at": pd.Timestamp("2026-09-01", tz="UTC"),
            "rating_norm": [0.2, 1.0] * (n // 2),
            "text": ["Reis trocken und kalt. Biryani war fad", "Super lecker und schnell geliefert"] * (n // 2),
            "lang": "de",
        }
    )


def test_sample_excludes_design_slice_and_is_fixed(tmp_path):
    clauses = mine(_reviews(400))
    s1 = check_sample(clauses, tmp_path / "s.json")
    assert s1 and not any(is_design(x["key"].rsplit(":", 1)[0]) for x in s1)
    assert check_sample(clauses.head(3), tmp_path / "s.json") == s1  # created once, then re-used


def test_check_metrics():
    clauses = mine(_reviews(4))
    labels = [
        {"key": "r0:0", "aspects": ["rice", "temperature"], "polarity": "negative"},
        {"key": "r1:0", "aspects": ["taste"], "polarity": "positive"},
        {"key": "r2:0", "skip": True},
    ]
    m = check_metrics(clauses, labels)
    assert m["checked"] == 2 and m["polarity_accuracy"] == 1.0
    assert m["aspect_precision"] == 0.75 and m["aspect_recall"] == 1.0  # predicted delivery_time on r1 is extra


def test_summarise_shapes():
    r = _reviews(60)
    listings = pd.DataFrame(
        {"listing_id": ["L1", "L2"], "name": ["A", "B"], "district": ["Mitte", None], "biryani_items": [3, 1]}
    )
    ins = summarise(mine(r), r, listings)
    top = {a["aspect"]: a for a in ins["overall"]}
    assert top["rice"]["negative_rate"] == 1.0 and top["rice"]["our_angle"]
    assert ins["biryani_mentions"] and {c["name"] for c in ins["competitors"]} == {"A", "B"}
    assert json.dumps(ins, default=str)  # serialisable (no NaN objects)


def test_control_check_page_flow(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from intel import control as ctl
    from intel.settings import Settings

    checks = tmp_path / "data" / "checks"
    checks.mkdir(parents=True)
    (checks / "weakness_miner_sample.json").write_text(
        json.dumps(
            [
                {"key": "r1:0", "clause": "Reis war trocken", "rating": 0.2},
                {"key": "r2:0", "clause": "Super lecker", "rating": 1.0},
            ]
        )
    )
    c = TestClient(
        ctl.create_control_app(Settings(_env_file=None, intel_data_dir=tmp_path / "data"), tmp_path, 8765),
        base_url="http://127.0.0.1:8765",
    )
    page = c.get("/check").text
    token = page.split("name=token value='")[1].split("'")[0]
    assert "Review check 1 of 2" in page and "Reis war trocken" in page and "checked>" not in page  # no hints
    assert c.post("/check", data={"token": "x", "key": "r1:0", "polarity": "negative"}).status_code == 403
    assert c.post("/check", data={"token": token, "key": "evil", "polarity": "negative"}).status_code == 400
    r = c.post(
        "/check",
        data={"token": token, "key": "r1:0", "aspects": ["rice", "bogus"], "polarity": "negative"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "Review check 2 of 2" in c.get("/check").text
    saved = json.loads((checks / "weakness_miner_labels.jsonl").read_text().splitlines()[0])
    assert saved["aspects"] == ["rice"] and saved["polarity"] == "negative"


def test_insights_page_renders():
    from intel.serve.views import insights_html

    r = _reviews(60)
    listings = pd.DataFrame(
        {"listing_id": ["L1", "L2"], "name": ["A<b>", "B"], "district": ["Mitte", "Pankow"], "biryani_items": [3, 1]}
    )
    ins = summarise(mine(r), r, listings)
    page = insights_html({"version": "wm-1", "insights": ins, "check": {"checked": 150}})
    assert "what Berlin biryani customers complain about" in page and "A&lt;b&gt;" in page
    assert "No insights approved yet" in insights_html(None)
