"""M6 ledger: shadow scoring, outcomes at horizons, idempotency, a predictor outage + backfill."""

import datetime as dt
import uuid

import httpx
import psycopg
import pytest
from psycopg import sql

from intel.serve.db import connect
from intel.serve.measure import Measurer, live_summary
from tests.test_serve import CHAMPION_COLS, CTX, DSN, _posts, build_features, champ

pytestmark = pytest.mark.skipif(not DSN, reason="INTEL_TEST_PG_DSN not set")
NOW = dt.datetime.now(dt.UTC).replace(microsecond=0)
START = NOW - dt.timedelta(days=10)


def _newdb() -> str:
    name = f"intel_wh_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    return name


def _dropdb(name: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


@pytest.fixture
def warehouse():
    name = _newdb()
    dsn = psycopg.conninfo.make_conninfo(DSN, dbname=name)
    a, b, c_ = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(dsn, autocommit=True) as w:
        w.execute("""create table social_post (id uuid primary key, platform text, posted_at timestamptz,
                     content_type text, caption text, permalink text)""")
        w.execute("""create table social_metrics_snapshot (social_post_id uuid, captured_at timestamptz,
                     impressions bigint, reach bigint, likes bigint, comments bigint, shares bigint, saves bigint,
                     clicks bigint)""")
        posts = [
            (a, "facebook", START - dt.timedelta(days=5), "image", "old post"),
            (b, "facebook", NOW - dt.timedelta(days=8), "reel", "Halal kacchi Saturday #berlin"),
            (c_, "instagram", NOW - dt.timedelta(hours=2), "reel", None),
        ]
        w.cursor().executemany(
            "insert into social_post (id, platform, posted_at, content_type, caption) values (%s,%s,%s,%s,%s)", posts
        )
        snaps = [
            (a, START, 100, 90, 8, 1, 1),
            (b, posts[1][2] + dt.timedelta(hours=25), 50, 40, 10, 1, 1),
            (b, posts[1][2] + dt.timedelta(hours=73), 80, 70, 13, 1, 1),
            (b, posts[1][2] + dt.timedelta(days=7, hours=2), 99, 88, 18, 1, 1),
        ]
        w.cursor().executemany("insert into social_metrics_snapshot values (%s,%s,%s,%s,%s,%s,%s,0,0)", snaps)
    yield dsn, {"old": a, "fb": b, "ig": c_}
    _dropdb(name)


class Models:
    def __init__(self):
        df = _posts()
        pipe = champ.recipe().fit(build_features(df, CTX)[CHAMPION_COLS], [0, 1] * (len(df) // 2))
        self.m = {
            "package_id": 1,
            "version": "cs-t",
            "bundle": {
                "version": "cs-t",
                "pipeline": pipe,
                "columns": CHAMPION_COLS,
                "threshold": 0.49,
                "context": CTX.to_dict(),
            },
        }

    def get(self, name):
        return self.m if name == "content_scorer" else None


def harness(status: int = 200):
    def handler(req: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"detail": "down"})
        return httpx.Response(
            200,
            json={
                "label": "likely_below_typical",
                "probability": 0.3,
                "threshold": 0.49,
                "model_version": "2026-09-23-logreg-v1",
                "top_reasons": [],
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def intel(tmp_path):
    from intel.serve.db import migrate

    name = _newdb()
    dsn = psycopg.conninfo.make_conninfo(DSN, dbname=name)
    migrate(dsn)
    yield dsn
    _dropdb(name)


def test_shadow_scoring_and_outcomes(intel, warehouse):
    wh, ids = warehouse
    m = Measurer(intel, wh, START, Models(), "http://predictor:8000", http=harness())
    r = m.run_once()
    assert (r.new_posts, r.predictions, r.outcomes, r.errors) == (2, 4, 3, 0)  # old post ignored; ig too young
    with connect(intel) as c:
        o = {x["horizon"]: x for x in c.execute("select * from post_outcome where post_id=%s", (ids["fb"],))}
        api = c.execute("select count(*) n from ledger_post where caption_length = 0").fetchone()["n"]
    assert o["24h"]["engagement"] == 12 and o["24h"]["platform_median"] == 10 and o["24h"]["label"] == 1
    assert float(o["7d"]["age_hours"]) == 170.0 and o["7d"]["engagement"] == 20
    assert api == 1  # NULL caption stored as length 0
    again = m.run_once()
    assert (again.new_posts, again.predictions, again.outcomes) == (0, 0, 0)  # idempotent
    s = live_summary(intel, "7d")
    assert s["models"]["content_scorer"]["n"] == 1 and s["models"]["harness_predictor"]["n"] == 1
    assert s["models"]["harness_predictor"]["accuracy"] == 0.0  # predicted 0, real 1


def test_predictor_outage_is_backfilled(intel, warehouse):
    wh, ids = warehouse
    down = Measurer(intel, wh, START, Models(), "http://predictor:8000", http=harness(500))
    r = down.run_once()
    assert r.new_posts == 2 and r.predictions == 2 and r.errors >= 2  # only content_scorer stored
    up = Measurer(intel, wh, START, Models(), "http://predictor:8000", http=harness())
    r2 = up.run_once()
    with connect(intel) as c:
        n = c.execute("select count(*) n from prediction where model_name='harness_predictor'").fetchone()["n"]
    # the fb post already has outcomes after run 1 -> not backfilled (prediction would be after the fact)
    assert n == 1 and r2.errors == 0


def test_too_late_snapshot_is_not_used(intel, warehouse):
    wh, ids = warehouse
    with psycopg.connect(wh, autocommit=True) as w:  # only snapshot of a new post is 5 days after posting
        pid = uuid.uuid4()
        posted = NOW - dt.timedelta(days=6)
        w.execute(
            "insert into social_post (id, platform, posted_at, content_type, caption) "
            "values (%s,'facebook',%s,'reel','x')",
            (pid, posted),
        )
        w.execute(
            "insert into social_metrics_snapshot values (%s,%s,1,1,50,0,0,0,0)", (pid, posted + dt.timedelta(days=5))
        )
    Measurer(intel, wh, START, Models(), None).run_once()
    with connect(intel) as c:
        got = c.execute("select horizon from post_outcome where post_id=%s", (pid,)).fetchall()
    assert got == []  # neither the 24h nor the 72h outcome may use a 5-day-old number


def test_posts_page_and_try_a_draft(intel, warehouse, tmp_path):
    from fastapi.testclient import TestClient

    from intel.serve.app import create_app
    from intel.serve.views import reason_text
    from intel.settings import Settings

    wh, ids = warehouse
    Measurer(intel, wh, START, Models(), "http://predictor:8000", http=harness()).run_once()
    (tmp_path / "inbox").mkdir()
    s = Settings(
        _env_file=None,
        intel_db_dsn=intel,
        intel_warehouse_dsn=wh,
        intel_inbox_dir=tmp_path / "inbox",
        intel_store_dir=tmp_path / "store",
    )
    with TestClient(create_app(s, start_workers=False)) as c:
        page = c.get("/posts").text
        assert "Halal kacchi Saturday" in page  # caption read live from the warehouse
        assert "Current predictor" in page and "✗ wrong" in page and "▲ above" in page
        assert "after 7d" in page and "<b>0%</b> right of 1" in page
        c.app.state.intel["models"].loaded["content_scorer"] = Models().m
        form = c.get("/try")
        token = c.cookies.get("intel_csrf")
        r = c.post(
            "/try",
            data={
                "csrf": token,
                "platform": "instagram",
                "content_type": "reel",
                "when": "2026-10-10T18:00",
                "caption": "Kacchi #berlin",
            },
        )
        assert "chance to beat your typical" in r.text and form.status_code == 200
        assert c.post("/try", data={"csrf": "x", "when": "2026-10-10T18:00"}).status_code == 403
        bad = c.post("/try", data={"csrf": token, "when": "not a date"})
        assert "Please check the inputs" in bad.text
    assert reason_text("nominal__day_of_week_Saturday") == "Day: Saturday"
    assert reason_text("numeric__caption_length") == "Caption length"


class TwoModels(Models):
    def get(self, name):
        if name == "reach_scorer":
            return {**self.m, "version": "rs-t", "bundle": {**self.m["bundle"], "version": "rs-t"}}
        return super().get(name)

    def names(self):
        return ["content_scorer", "reach_scorer"]


def test_reach_outcome_and_every_active_model_scored(intel, warehouse):
    wh, ids = warehouse
    with psycopg.connect(wh, autocommit=True) as w:  # 6 earlier facebook posts with reach 10..60
        for i in range(6):
            pid = uuid.uuid4()
            at = NOW - dt.timedelta(days=30 - i)
            w.execute(
                "insert into social_post (id, platform, posted_at, content_type, caption) "
                "values (%s,'facebook',%s,'image','x')",
                (pid, at),
            )
            w.execute(
                "insert into social_metrics_snapshot values (%s,%s,1,%s,1,0,0,0,0)",
                (pid, at + dt.timedelta(days=9), 10 * (i + 1)),
            )
    Measurer(intel, wh, START, TwoModels(), "http://predictor:8000", http=harness()).run_once()
    with connect(intel) as c:
        models = {
            r["model_name"] for r in c.execute("select model_name from prediction where post_id=%s", (ids["fb"],))
        }
        o = c.execute(
            "select reach, reach_median, label_reach from post_outcome where post_id=%s and horizon='7d'", (ids["fb"],)
        ).fetchone()
    assert models == {"content_scorer", "reach_scorer", "harness_predictor"}
    assert o["reach_median"] == 40.0 and o["reach"] == 88 and o["label_reach"] == 1  # 7 prior: 10..60 + old 90
    s = live_summary(intel, "7d")
    assert s["models"]["reach_scorer"]["n"] == 1 and set(s["reach"]) == {
        "content_scorer",
        "reach_scorer",
        "harness_predictor",
    }
