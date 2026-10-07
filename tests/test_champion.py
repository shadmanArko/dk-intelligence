import datetime as dt
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from intel.champions import harness_v1
from intel.datasets.content import build_content_dataset, expanding_median_label, time_split
from intel.evaluate import score

HARNESS = Path(__file__).resolve().parents[2] / "dhaka_kacchi_ai_harness"


def test_vendored_features_unchanged():
    src = Path(harness_v1.__file__).with_name("features.py").read_text().splitlines(keepends=True)
    body = "".join(src[3:])  # 3-line VENDORED header
    assert hashlib.sha256(body.encode()).hexdigest() == harness_v1.FEATURES_SHA256


def test_expanding_median_matches_sql_semantics():
    t = pd.Timestamp("2026-05-01", tz="UTC")
    df = pd.DataFrame(
        {
            "id": list("abcde"),
            "platform": ["facebook"] * 4 + ["instagram"],
            "posted_at": [t + pd.Timedelta(hours=h) for h in range(5)],
            "engagement": [2.0, np.nan, 6.0, 1.0, 3.0],
        }
    )
    out = expanding_median_label(df).set_index("id")
    assert out.loc["a", "expanding_median_engagement"] == 2.0 and out.loc["a", "label"] == 0  # includes itself
    assert out.loc["b", "label"] == 0  # NULL -> 0
    assert out.loc["c", "expanding_median_engagement"] == 4.0 and out.loc["c", "label"] == 1
    assert out.loc["d", "expanding_median_engagement"] == 2.0 and out.loc["d", "label"] == 0
    assert out.loc["e", "label"] == 0  # own platform only


def test_dataset_uses_latest_snapshot_only(tmp_path):
    t0 = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    pq.write_table(
        pa.table(
            {
                "id": ["p1", "p2", "p3"],
                "platform": ["facebook", "facebook", "threads"],
                "posted_at": [t0, t0 + dt.timedelta(days=1), t0],
                "content_type": ["reel", None, "text"],
                "caption": ["Kacchi #berlin", None, "x"],
            }
        ),
        tmp_path / "social_post.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "social_post_id": ["p1", "p1", "p2", "p3"],
                "captured_at": [t0 + dt.timedelta(days=1), t0 + dt.timedelta(days=10), t0 + dt.timedelta(days=3), t0],
                "likes": [1, 5, 2, 0],
                "comments": [0, 1, 0, 0],
                "shares": [0, 0, 0, 0],
                "impressions": [10, 50, 20, 0],
                "reach": [9, 40, 15, 0],
            }
        ),
        tmp_path / "social_metrics_snapshot.parquet",
    )
    ds = build_content_dataset(tmp_path).set_index("id")
    assert list(ds.index) == ["p1", "p2"]  # threads excluded, one row per post
    assert ds.loc["p1", "engagement"] == 6 and bool(ds.loc["p1", "matured"])
    assert not bool(ds.loc["p2", "matured"])  # snapshot only 2 days after posting


def test_time_split_is_strictly_ordered():
    t = pd.Timestamp("2026-05-01", tz="UTC")
    df = pd.DataFrame({"posted_at": [t + pd.Timedelta(days=i) for i in range(10)], "id": range(10)})
    train, test, cutoff = time_split(df, 0.2)
    assert len(test) == 2 and train["posted_at"].max() < test["posted_at"].min() == cutoff


def test_score_shape():
    s = score([0, 1, 0, 1], [0.2, 0.8, 0.6, 0.4], 0.5, pd.Series(["facebook", "facebook", "instagram", "instagram"]))
    assert s["balanced_accuracy"] == 0.5 and s["roc_auc"] == 0.75 and s["confusion_matrix"] == [[1, 1], [1, 1]]


@pytest.mark.skipif(
    not (HARNESS / "ml/01-data/artifacts/dataset.csv").exists(), reason="harness repo not next to this one"
)
def test_recipe_reproduces_published_score():
    from intel.champions.reproduce import PUBLISHED_TEST_BALANCED_ACCURACY, recipe_check

    assert recipe_check(HARNESS)["balanced_accuracy"] == PUBLISHED_TEST_BALANCED_ACCURACY


def test_reach_label_uses_only_earlier_posts():
    from intel.datasets.content import REACH_MIN_PRIOR, reach_label

    t = pd.Timestamp("2026-05-01", tz="UTC")
    reach = [10, 20, 30, 40, 50, 25, 100, None]
    df = pd.DataFrame(
        {
            "id": list("abcdefgh"),
            "platform": "facebook",
            "posted_at": [t + pd.Timedelta(days=i) for i in range(8)],
            "reach": reach,
        }
    )
    out = reach_label(df).set_index("id")
    assert out.loc[["a", "b", "c", "d", "e"], "y_reach"].isna().all()  # fewer than 5 earlier posts
    assert REACH_MIN_PRIOR == 5
    assert out.loc["f", "reach_median_prior"] == 30 and out.loc["f", "y_reach"] == 0  # 25 vs median(10..50)
    assert out.loc["g", "y_reach"] == 1  # 100 vs median incl. f
    assert pd.isna(out.loc["h", "y_reach"])  # no reach -> no label
