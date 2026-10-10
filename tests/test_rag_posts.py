"""P4.3: our own posts and creator posts in the knowledge pack, search_posts, trend_signals, the activation gate and
the hidden-pattern join. Postgres tests need INTEL_TEST_PG_DSN on a pgvector server (see tests/test_rag.py)."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from intel.adapters.pack import PackTrendSource
from intel.capabilities import evidence, patterns
from intel.capabilities.evidence import EvidenceQuery, EvidenceResult
from intel.capabilities.types import ToolProblem
from intel.rag import gate, store
from intel.rag import pack as kp
from intel.rag.build import make_indexer
from intel.rag.chunk import creator_post_chunks, own_post_chunks
from intel.rag.embed import HashEmbedder
from tests.test_rag import _snapshot, pg  # noqa: F401 - pg is a fixture

UTC = dt.UTC
POSTS = pd.DataFrame(
    {
        "id": ["p1", "p2", "p3", "p4"],
        "platform": ["instagram", "instagram", "facebook", "instagram"],
        "external_id": ["e1", "e2", "e3", "e4"],
        "campaign_variant_id": [None] * 4,
        "posted_at": pd.to_datetime(["2026-09-01", "2026-09-05", "2026-09-07", "2026-09-09"], utc=True),
        "permalink": ["https://instagram.com/p/1", None, "https://facebook.com/x", None],
        "content_type": ["reel", "image", None, "image"],
        "caption": [
            "Frisch gekocht, große Portion Hammel. Thanks @hungry_hans!",
            "Kacchi Biryani mit zartem Fleisch und Basmati-Reis @dhakakacchi",
            "Order now for Saturday",
            "Mutton Kacchi from the pot (test)  Test upload from DK Publishing.",
        ],
        "created_at": pd.to_datetime(["2026-09-01"] * 4, utc=True),
        "updated_at": pd.to_datetime(["2026-09-01"] * 4, utc=True),
    }
)
METRICS = pd.DataFrame(
    {
        "id": ["m1", "m2", "m3", "m4"],
        "social_post_id": ["p1", "p1", "p2", "p3"],
        "captured_at": pd.to_datetime(["2026-09-02", "2026-09-03", "2026-09-06", "2026-09-08"], utc=True),
        "impressions": [0.0] * 4,
        "reach": [100.0, 200.0, 100.0, 20.0],
        "likes": [5, 18, 2, 1],
        "comments": [0, 2, 0, 0],
        "shares": [0.0, 0.0, 0.0, None],
        "saves": [0.0, 0.0, 0.0, 0.0],
        "clicks": [0.0] * 4,
        "created_at": pd.to_datetime(["2026-09-02"] * 4, utc=True),
        "views": [None] * 4,
        "watch_seconds": [None] * 4,
        "subscribers_gained": [None] * 4,
        "impression_ctr": [None] * 4,
    }
)
CREATORS = pd.DataFrame(
    {"account_id": ["a1", "a2"], "handle": ["berlinfoodie", "kiezkoch"], "median_engagement_rate": [0.05, 0.1]}
)
CREATOR_POSTS = pd.DataFrame(
    {
        "account_id": ["a1", "a1", "a1", "a2"],
        "permalink": ["https://www.tiktok.com/@berlinfoodie/video/1", None, None, None],
        "caption": [
            "Bester Biryani-Spot in Neukölln? Ich teste! #biryani",
            "Biryani Tour Teil 2 mit @private_person",
            "Biryani Tour Teil 3, die Portion war riesig",
            "Döner vs Biryani, wer gewinnt?",
        ],
        "posted_at": pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"], utc=True),
        "views": [1000, 2000, 500, 100],
        "likes": [100, 50, 10, 30],
    }
)
ALLOWED = frozenset({"dhakakacchi", "berlinfoodie", "kiezkoch"})
TRENDS = pd.DataFrame(
    {
        "platform": ["tiktok", "tiktok", "youtube", "reddit", "tiktok"],
        "tag_type": ["rice_dish", "occasion", "rice_dish", "dish", "community"],
        "tag_id": ["biryani", "football", "pilaf", "kebab", "somegroup"],
        "posts": [500, 120, 90, 300, 900],
        "peers": [10] * 5,
        "median_lift": [0.5, 0.1, 0.9, -0.2, 0.3],
        "mean_lift": [0.6, 0.2, 1.0, -0.1, 0.4],
        "mean_lift_vs_platform": [0.4, 0.2, 0.9, -0.3, 0.5],
        "mean_pct_in_peer": [0.5] * 5,
        "viral_share": ["0.2", "0.1", "0.3", "0.05", "0.2"],
        "lift_std_error": [0.01] * 5,
        "platform_posts": [1000] * 5,
    }
)


# ---------- chunking ----------


def test_own_posts_carry_latest_metrics_relative_to_our_median_and_mask_private_handles():
    chunks = {c.source_id: c for c in own_post_chunks(POSTS, METRICS, ALLOWED)}
    assert set(chunks) == {"p1", "p2", "p3"}  # p4 is a publishing test
    p1, p2 = chunks["p1"], chunks["p2"]
    assert p1.metrics is not None and p1.metrics["reach"] == 200.0  # the latest snapshot, not the first
    assert p1.metrics["engagement_rate"] == 0.1 and p2.metrics is not None and p2.metrics["engagement_rate"] == 0.02
    assert p1.metrics["vs_our_median"] == round(0.1 / 0.06, 2)  # median of our instagram posts = 0.06
    assert "@[user]" in p1.text and "hungry_hans" not in p1.text and "@dhakakacchi" in p2.text
    assert p1.collection == "own_posts" and p1.cite.startswith("P-") and p1.account == "dhakakacchi"
    assert {"meat", "portion"} <= set(p1.aspects) and p1.url == "https://instagram.com/p/1"
    assert json.loads(p1.row()["metrics"])["reach"] == 200.0  # stored as JSON (jsonb)
    assert chunks["p3"].metrics == {"reach": 20.0, "likes": 1.0, "comments": 0.0, "saves": 0.0}  # too small to rate


def test_creator_posts_are_public_accounts_with_relative_performance():
    chunks = creator_post_chunks(CREATOR_POSTS, CREATORS, ALLOWED)
    c = chunks[0]
    assert c.cite.startswith("C-") and c.account == "berlinfoodie" and c.platform == "tiktok"
    assert c.metrics == {"views": 1000.0, "likes": 100.0, "like_rate": 0.1, "vs_account_median": 2.0}
    assert "@[user]" in chunks[1].text and "private_person" not in chunks[1].text
    assert len({x.cite for x in chunks}) == 4


def test_full_pack_has_posts_and_trends_without_origin_tags(tmp_path):
    mip, market = _snapshot(tmp_path)
    snap = market / "20261009T000000Z"
    CREATORS.to_parquet(snap / "creators.parquet")
    CREATOR_POSTS.to_parquet(snap / "creator_posts.parquet")
    TRENDS.to_parquet(snap / "content_feature_performance.parquet")
    wikidata = mip / "data" / "bulk" / "wikidata"
    wikidata.mkdir(parents=True)
    (wikidata / "rice_dishes_2026-10-03.json").write_text(
        json.dumps({"entities": {"pilaf": {"labels": {"de": {"value": "Pilaw"}, "en": {"value": "Pilaf"}}}}})
    )
    pull = tmp_path / "pulls" / "20261009T130432Z"
    pull.mkdir(parents=True)
    POSTS.to_parquet(pull / "social_post.parquet")
    METRICS.to_parquet(pull / "social_metrics_snapshot.parquet")
    root = tmp_path / "knowledge"
    pid = kp.build_brand_pack(
        mip, market, root, now=dt.datetime(2026, 10, 10, tzinfo=UTC),
        indexer=make_indexer(HashEmbedder(), tmp_path / "cache", pull),
    )  # fmt: skip
    m = kp.verify(root / pid)
    assert m["collections"] == {"brand": 1, "rules": 1, "reviews": 4, "creator_posts": 4, "own_posts": 3}
    assert m["pull"] == "20261009T130432Z" and m["trends"]["rows"] == 4 and m["residual_pii"] == {}
    kp.activate(root, pid)
    trend_pack, rows = PackTrendSource(root).load()
    assert trend_pack == pid and "community" not in {r["tag_type"] for r in rows}
    labels = {r["tag_id"]: r["tag_label"] for r in rows}
    assert labels["pilaf"] == "Pilaf" and labels["football"] == "football"  # Wikidata name; other tags readable
    assert m["trends"]["rice_dish_labels_sha256"]


def test_a_pack_without_trends_fails_closed(tmp_path):
    from tests.test_mcp_brand import _sources

    mip, market = _sources(tmp_path)
    root = tmp_path / "knowledge"
    kp.activate(root, kp.build_brand_pack(mip, market, root))
    with pytest.raises(ToolProblem, match="UPSTREAM_UNAVAILABLE"):
        PackTrendSource(root).load()


# ---------- search_posts and trend_signals (capability) ----------


class FakeIndex:
    def __init__(self, rows):  # type: ignore[no-untyped-def]
        self.rows = rows

    def search(self, q):  # type: ignore[no-untyped-def]
        cols = (q.collection,) if isinstance(q.collection, str) else q.collection
        return EvidenceResult("kp-1", "dense", [r for r in self.rows if r["collection"] in cols][: q.limit])

    def sources(self, cites):  # type: ignore[no-untyped-def]
        return "kp-1", []

    def support(self, pairs):  # type: ignore[no-untyped-def]
        return None


POST_ROWS = [
    {"cite": "P-AAAAAAAA", "collection": "own_posts", "text": "Kacchi am Samstag", "rerank": 0.9,
     "account": "dhakakacchi", "platform": "instagram", "metrics": '{"reach": 100, "vs_our_median": 0.8}'},
    {"cite": "C-BBBBBBBB", "collection": "creator_posts", "text": "Biryani Tour", "rerank": 0.7,
     "account": "berlinfoodie", "platform": "tiktok", "metrics": {"views": 1000, "vs_account_median": 2.5}},
    {"cite": "P-CCCCCCCC", "collection": "own_posts", "text": "Hammel, frisch", "rerank": 0.6,
     "account": "dhakakacchi", "platform": "instagram", "metrics": '{"vs_our_median": 1.6, "secret": 1}'},
    {"cite": "P-DDDDDDDD", "collection": "own_posts", "text": "noise", "rerank": 0.001},
]  # fmt: skip


def test_search_posts_by_source_and_by_performance():
    q = EvidenceQuery(evidence.POST_COLLECTIONS["all"], "kacchi", limit=3)
    rel = evidence.search_posts(FakeIndex(POST_ROWS), q, 0.003)
    assert [h["cite"] for h in rel["hits"]] == ["P-AAAAAAAA", "C-BBBBBBBB", "P-CCCCCCCC"]
    assert rel["hits"][0]["trust"] == "own" and rel["hits"][1]["trust"] == "untrusted"
    assert rel["hits"][1]["source_kind"] == "creator" and rel["hits"][1]["perf"]["vs_account_median"] == 2.5
    assert rel["hits"][2]["perf"] == {"vs_our_median": 1.6}  # only known performance keys leave
    perf = evidence.search_posts(
        FakeIndex(POST_ROWS), EvidenceQuery(q.collection, "kacchi", limit=2), 0.003, sort="performance"
    )
    assert [h["cite"] for h in perf["hits"]] == ["C-BBBBBBBB", "P-CCCCCCCC"] and perf["sort"] == "performance"
    own = evidence.search_posts(FakeIndex(POST_ROWS), EvidenceQuery(evidence.POST_COLLECTIONS["own"], "kacchi"), 0.003)
    assert {h["source_kind"] for h in own["hits"]} == {"own"} and "P-DDDDDDDD" not in str(own["hits"])


class Trends:
    def load(self):  # type: ignore[no-untyped-def]
        rows = TRENDS[TRENDS.tag_type != "community"].to_dict("records")
        return "kp-1", rows


def test_trend_signals_filters_sorts_and_says_what_it_is_not():
    top = evidence.trend_signals(Trends())
    assert [s["tag"] for s in top["signals"]] == ["biryani", "football", "kebab"]  # posts >= 100, by lift
    assert "NOT a time trend" in top["caveat"] and top["matched"] == 3
    low = evidence.trend_signals(Trends(), direction="bottom", min_posts=1, limit=1)
    assert [s["tag"] for s in low["signals"]] == ["kebab"]
    assert [s["tag"] for s in evidence.trend_signals(Trends(), platform="youtube", min_posts=50)["signals"]] == [
        "pilaf"
    ]
    with pytest.raises(ToolProblem, match="community"):
        evidence.trend_signals(Trends(), tag_type="community")  # not in the pack, so not a valid choice
    with pytest.raises(ToolProblem):
        evidence.trend_signals(Trends(), platform="instagram")


# ---------- the store with posts ----------


def test_posts_search_caps_creators_not_us_and_filters(pg):  # noqa: F811
    import psycopg

    emb = HashEmbedder()
    chunks = own_post_chunks(POSTS, METRICS, ALLOWED) + creator_post_chunks(CREATOR_POSTS, CREATORS, ALLOWED)
    rows = [{**c.row(), "posted_at": c.posted_at.to_pydatetime()} for c in chunks]
    with psycopg.connect(pg, autocommit=True) as c:
        store.load_pack(c, "kp-p", {}, rows, emb.documents([ch.embed_text for ch in chunks]))
        store.activate(c, "kp-p")
        q = "Biryani"
        creators = store.search(c, "kp-p", "creator_posts", q, emb.query(q), limit=10)
        assert sum(h.row["account"] == "berlinfoodie" for h in creators) == 2  # at most 2 per account
        both = store.search(c, "kp-p", ("own_posts", "creator_posts"), q, emb.query(q), limit=20)
        assert sum(h.row["collection"] == "own_posts" for h in both) == 3  # our own posts are never capped
        ig = store.search(c, "kp-p", "own_posts", q, emb.query(q), store.Filters(platform="facebook"))
        assert [h.row["source_id"] for h in ig] == ["p3"]
        reel = store.search(c, "kp-p", "own_posts", q, emb.query(q), store.Filters(content_type="reel"))
        assert [h.row["source_id"] for h in reel] == ["p1"] and reel[0].row["metrics"]["reach"] == 200.0


def test_cross_posted_captions_collapse_into_one_hit():
    rows = [
        {"cite": "P-1", "collection": "own_posts", "text": "Borhani ist da! @dhakakacchi", "account": "dhakakacchi"},
        {"cite": "P-2", "collection": "own_posts", "text": "Borhani ist da!   Dhaka Kacchi", "account": "dhakakacchi"},
        {"cite": "P-3", "collection": "own_posts", "text": "Kacchi am Samstag", "account": "dhakakacchi"},
    ]
    out = store.diversify([store.Hit(r["cite"], 1.0, r) for r in rows])
    assert [h.cite for h in out] == ["P-1", "P-3"] and out[0].duplicates == 2


# ---------- the activation gate ----------

QUESTIONS = [
    {"id": "q1", "collection": "reviews", "question": "kalt", "split": "dev", "kind": "answerable"},
    {"id": "q2", "collection": "reviews", "question": "Fleisch", "split": "dev", "kind": "answerable"},
    {"id": "q3", "collection": "reviews", "question": "lecker", "split": "dev", "kind": "answerable"},
    {"id": "q4", "collection": "reviews", "question": "x", "split": "test", "kind": "answerable"},
]
QRELS = {"q1": {"R-1"}, "q2": {"R-2"}, "q3": {"R-3"}, "q4": {"R-4"}}
GOOD = {"q1": ["R-1"], "q2": ["R-9", "R-2"], "q3": ["R-3"], "q4": ["R-4"]}
FULL = {"kind": "full", "residual_pii": {}}


def _search(results):  # type: ignore[no-untyped-def]
    return lambda pack, q: results[pack][q["id"]]


def test_the_gate_judges_per_question_on_dev_only():
    first = gate.evaluate(_search({"new": GOOD}), "new", None, FULL, QUESTIONS, QRELS)
    assert first.passed and first.hit_rate == {"candidate": 1.0}
    one_lost = {**GOOD, "q1": ["R-8"], "q4": []}  # one dev loss is tolerated; test is never looked at
    assert gate.evaluate(_search({"new": one_lost, "old": GOOD}), "new", "old", FULL, QUESTIONS, QRELS).passed
    worse = {"q1": ["R-8"], "q2": ["R-8"], "q3": ["R-3"], "q4": ["R-4"]}
    res = gate.evaluate(_search({"new": worse, "old": GOOD}), "new", "old", FULL, QUESTIONS, QRELS)
    assert not res.passed and res.lost == ["q1", "q2"] and "lost their relevant hit" in res.reasons[0]
    leaky = gate.evaluate(_search({"new": GOOD}), "new", None, {**FULL, "residual_pii": {"phone": 1}}, QUESTIONS, QRELS)
    assert not leaky.passed
    assert not gate.evaluate(_search({"new": GOOD}), "new", None, FULL, QUESTIONS, {}).passed  # unlabelled: no verdict


def test_the_gate_refuses_a_deliberately_worse_pack(pg):  # noqa: F811
    """Two real packs in Postgres: the same chunks, but the second has its vectors shuffled (a broken embedder)."""
    import psycopg

    from intel.rag.chunk import review_chunks
    from tests.test_rag import LISTINGS, REVIEWS

    emb = HashEmbedder()
    chunks = review_chunks(REVIEWS, LISTINGS)
    rows = [{**c.row(), "posted_at": c.posted_at.to_pydatetime()} for c in chunks]
    vecs = emb.documents([c.embed_text for c in chunks])
    qs = [
        {"id": f"d{i}", "collection": "reviews", "question": c.text, "split": "dev", "kind": "answerable"}
        for i, c in enumerate(chunks)
    ]
    qrels = {f"d{i}": {c.cite} for i, c in enumerate(chunks)}
    with psycopg.connect(pg, autocommit=True) as conn:
        store.load_pack(conn, "kp-good", FULL, rows, vecs)
        store.load_pack(conn, "kp-bad", FULL, rows, np.roll(vecs, 1, axis=0))
        store.activate(conn, "kp-good")

        def search(pack, q):  # type: ignore[no-untyped-def]
            hits = store.search(conn, pack, "reviews", q["question"], emb.query(q["question"]), mode="dense", limit=1)
            return [h.cite for h in hits]

        good = gate.evaluate(search, "kp-good", None, FULL, qs, qrels)
        assert good.passed and good.hit_rate["candidate"] == 1.0
        bad = gate.evaluate(search, "kp-bad", "kp-good", FULL, qs, qrels)
        assert not bad.passed and len(bad.lost) >= 2
        assert store.active_pack(conn) == "kp-good"


def test_qrels_are_cites_and_grades_only():
    rows = gate.read_jsonl(gate.QRELS)
    ids = {q["id"] for q in gate.read_jsonl(gate.GOLDEN)}
    assert rows and all(set(r) <= {"qid", "grades", "by"} for r in rows) and {r["qid"] for r in rows} <= ids
    assert all(c[:2] in ("R-", "B-", "U-", "P-", "C-") for r in rows for c in r["grades"])


# ---------- hidden patterns ----------


def test_opportunities_join_competitor_weak_spots_with_our_winning_posts():
    complaints = [
        {"aspect": "delivery_time", "label": "Delivery time", "mentions": 900, "negative_rate": 0.6},
        {"aspect": "portion", "label": "Portion", "mentions": 400, "negative_rate": 0.5},
        {"aspect": "rice", "label": "Rice", "mentions": 200, "negative_rate": 0.7},
        {"aspect": "smell", "label": "Smell", "mentions": 5, "negative_rate": 0.9},  # too few mentions
    ]
    posts = [
        {"cite": "P-1", "text": "große Portion " * 20, "aspects": ["portion"], "perf": {"vs_our_median": 1.8}},
        {"cite": "P-2", "text": "Portion", "aspects": ["portion"], "perf": {"vs_our_median": 0.7}},
        {"cite": "P-3", "text": "pünktlich", "aspects": ["delivery_time"], "perf": {"vs_our_median": 0.5}},
    ]
    out = patterns.opportunities(complaints, posts, gap_areas=[{"name": "Leopoldplatz", "district": "Mitte"}])
    assert [(o["aspect"], o["status"]) for o in out] == [("portion", "proven"), ("rice", "untested"),
                                                          ("delivery_time", "weak")]  # fmt: skip
    assert [p["cite"] for p in out[0]["our_posts"]] == ["P-1"] and len(out[0]["our_posts"][0]["text"]) == 140
    assert out[0]["gap_areas"] == [{"name": "Leopoldplatz", "district": "Mitte"}] and "not a fact" in out[0]["note"]
    assert "gap_areas" not in patterns.opportunities(complaints, posts)[0]
