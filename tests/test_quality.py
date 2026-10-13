"""ADR 0017: data contracts, volume, freshness, links and data cards; and the gates in the Sunday run."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

from intel.quality.validate import validate_snapshot
from tests.quality_fixtures import SNAP, market_tables, replace_column, warehouse_tables, write_snapshot


def lines(rep, severity="error"):
    return [f.line() for f in rep.findings if f.severity == severity]


def test_generated_snapshots_pass_and_get_a_card(tmp_path):
    w = validate_snapshot(write_snapshot(tmp_path / "pulls", "s1", warehouse_tables()), "warehouse")
    m = validate_snapshot(write_snapshot(tmp_path / "market", "s1", market_tables()), "market")
    assert w.ok and m.ok and not w.warnings, (lines(w), lines(w, "warning"), lines(m))
    card = w.card["social_post"]
    assert card["rows"] == 60 and card["used_by"] == ["content_scorer", "reach_scorer", "funnel", "performance"]
    assert card["columns"]["content_type"]["null_pct"] == pytest.approx(33.33, abs=0.01)
    assert card["columns"]["platform"]["top"] == {"facebook": 30, "instagram": 30}
    assert card["age_hours"] > 0 and w.summary()["tables"] == 5


def test_structure_errors_stop_before_reading_values(tmp_path):
    def tweak(t):
        t["social_post"] = t["social_post"].drop_columns(["caption"])
        replace_column(t, "social_metrics_snapshot", "likes", [str(x) for x in range(600)], pa.string())

    rep = validate_snapshot(write_snapshot(tmp_path, "s", warehouse_tables(), tweak=tweak), "warehouse")
    assert "social_post: caption present: required column missing" in lines(rep)
    assert "social_metrics_snapshot: likes type: is string, expected int64" in lines(rep)
    assert "columns" not in rep.card["social_post"]  # not profiled: it can't be read the way models read it


def test_value_errors_name_the_rows(tmp_path):
    def tweak(t):
        replace_column(t, "social_post", "id", ["p000", *[f"p{i:03d}" for i in range(59)]])  # one duplicate
        replace_column(t, "social_metrics_snapshot", "likes", [-1, *range(1, 600)])

    rep = validate_snapshot(write_snapshot(tmp_path, "s", warehouse_tables(), tweak=tweak), "warehouse")
    errs = lines(rep)
    assert any(e.startswith("social_post: id") and "uniqueness" in e and "p000" in e for e in errs), errs
    assert any("likes greater_than_or_equal_to(0)" in e and "-1" in e for e in errs), errs


def test_orphan_metrics_and_future_timestamps(tmp_path):
    def tweak(t):
        replace_column(t, "social_metrics_snapshot", "social_post_id", ["ghost", *(["p001"] * 599)])
        replace_column(
            t,
            "social_post",
            "posted_at",
            [SNAP + dt.timedelta(days=2)] + [SNAP - dt.timedelta(days=i) for i in range(1, 60)],
        )

    rep = validate_snapshot(write_snapshot(tmp_path, "s", warehouse_tables(), tweak=tweak), "warehouse")
    errs = lines(rep)
    assert any("social_post_id -> social_post.id" in e and "ghost" in e for e in errs), errs
    assert "social_post: posted_at not in the future: 1 row(s) after the snapshot time" in errs


@pytest.mark.parametrize(
    ("lag", "severity"),
    [(dt.timedelta(hours=3), None), (dt.timedelta(days=3), "warning"), (dt.timedelta(days=8), "error")],
)
def test_metric_freshness_warns_then_blocks(tmp_path, lag, severity):
    t = warehouse_tables()
    newest = t["social_metrics_snapshot"].column("captured_at").to_pylist()

    def tweak(tables):
        shift = max(newest) - (SNAP - lag)
        replace_column(tables, "social_metrics_snapshot", "captured_at", [c - shift for c in newest])

    rep = validate_snapshot(write_snapshot(tmp_path, "s", t, tweak=tweak), "warehouse")
    found = {f.severity for f in rep.findings if f.check == "freshness" and f.table == "social_metrics_snapshot"}
    assert found == ({severity} if severity else set())


def test_volume_drop_against_the_previous_snapshot(tmp_path):
    prev = write_snapshot(tmp_path, "s1", warehouse_tables(n_posts=120))
    cur = write_snapshot(tmp_path, "s2", warehouse_tables(n_posts=60))  # half the posts vanished
    rep = validate_snapshot(cur, "warehouse", prev)
    assert any(e.startswith("social_post: volume: 60 rows vs 120") for e in lines(rep))
    assert rep.card["social_post"]["rows_previous"] == 120 and rep.previous_id == "s1"


def test_market_calendar_ratings_and_country_names(tmp_path):
    def tweak(t):
        t["occasion_day"] = t["occasion_day"].slice(0, 3 * 210)  # ends ~10 days after the snapshot
        replace_column(t, "biryani_reviews", "rating_norm", ["n/a", *["0.5"] * 1199])
        names = [f"Land {i}" for i in range(59)] + ["Land 0", "Asien zusammen", "Zusammen", "Zusammen", "Insgesamt"]
        replace_column(t, "afs_citizenship", "citizenship", names)

    rep = validate_snapshot(write_snapshot(tmp_path, "m", market_tables(), tweak=tweak), "market")
    errs = lines(rep)
    assert any(e.startswith("occasion_day: horizon: day ends") for e in errs), errs
    assert any("rating_norm parses_as_0_to_1" in e and "n/a" in e for e in errs), errs
    country = [e for e in errs if "country_names_unique" in e]
    assert len(country) == 1 and "Land 0" in country[0] and "Zusammen" not in country[0]  # subtotals are fine


# ---------- the gates in the Sunday run ----------


def _weekly(tmp_path, monkeypatch, warehouse_tweak=None, market_snaps=1, market_tweak=None):
    import intel.market
    import intel.pull
    import intel.tunnel
    import intel.weekly as w
    from intel.settings import Settings
    from tests.test_plugins import Dummy

    data = tmp_path / "data"
    pull = write_snapshot(data / "pulls", "20261008T070000Z", warehouse_tables(), tweak=warehouse_tweak)
    for i in range(market_snaps - 1):  # older, valid market snapshots
        write_snapshot(data / "market", f"2026100{1 + i}T070000Z", market_tables())
    market = write_snapshot(data / "market", "20261008T070100Z", market_tables(), tweak=market_tweak)
    monkeypatch.setattr(w, "mip_refresh_running", lambda: False)
    monkeypatch.setattr(intel.tunnel, "warehouse_tunnel", lambda *a, **k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(intel.pull, "run_pull", lambda *a, **k: pull)
    monkeypatch.setattr(intel.pull, "verify_pull", lambda p: [])
    monkeypatch.setattr(intel.market, "run_market_pull", lambda *a, **k: market)
    s = Settings(
        _env_file=None,
        intel_data_dir=data,
        dk_warehouse_dsn="postgresql://u:p@127.0.0.1:1/x",
        mip_reader_dsn="postgresql://r@x/mip",
    )
    dummy = Dummy()
    return w.run_weekly(s, upload=False, log=lambda *_: None, plugins=[dummy]), dummy, data


def test_bad_warehouse_data_stops_the_run_before_training(tmp_path, monkeypatch):
    def tweak(t):
        replace_column(t, "social_metrics_snapshot", "likes", [-5, *range(1, 600)])

    r, dummy, data = _weekly(tmp_path, monkeypatch, warehouse_tweak=tweak)
    assert r["status"] == "error" and r["failed_step"] == "pull"
    assert "data checks failed for warehouse 20261008T070000Z" in r["error"] and "likes" in r["error"]
    assert dummy.seen == []  # no model ever saw the data
    assert r["data_quality"]["warehouse"]["errors"] and (data / "quality" / "warehouse-20261008T070000Z.json").exists()


def test_bad_market_snapshot_falls_back_to_the_newest_good_one(tmp_path, monkeypatch):
    def tweak(t):
        t["creators"] = t["creators"].slice(0, 10)  # far below the floor

    r, dummy, _ = _weekly(tmp_path, monkeypatch, market_snaps=3, market_tweak=tweak)
    assert r["status"] == "fail" and r["market_fallback"] is True and r["market_id"] == "20261002T070000Z"
    assert dummy.seen[0].market.name == "20261002T070000Z"
    assert r["data_quality"]["market"]["snapshot"] == "20261002T070000Z"


def test_bad_market_snapshot_without_a_good_one_stops_the_run(tmp_path, monkeypatch):
    def tweak(t):
        t["creators"] = t["creators"].slice(0, 10)

    r, _, _ = _weekly(tmp_path, monkeypatch, market_snaps=1, market_tweak=tweak)
    assert r["status"] == "error" and r["failed_step"] == "market" and "creators: min_rows" in r["error"]


def test_run_report_carries_data_quality_to_the_vps():
    from intel.serve.runs import RunReport, run_message

    rep = RunReport.model_validate(
        {
            "kind": "weekly_run",
            "run_id": "20261011T100000Z",
            "status": "fail",
            "started_at": "2026-10-11T10:00:00+00:00",
            "finished_at": "2026-10-11T10:20:00+00:00",
            "data_quality": {
                "warehouse": {
                    "snapshot": "20261011T100100Z",
                    "tables": 5,
                    "errors": [],
                    "warnings": ["social_post: freshness: newest posted_at is 23.0 days old"],
                }
            },
        }
    )
    assert rep.data_quality["warehouse"].tables == 5
    assert "🧪 Data checks: 1 warning(s), e.g. social_post: freshness" in run_message(rep)
