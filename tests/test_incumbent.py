"""A new scorer is offered for approval only if it clearly beats the ACTIVE version (intel.incumbent), on posts the
active version never trained on."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from intel.incumbent import compare
from intel.models.content_scorer.predict import predict
from intel.weekly import hold_if_not_better
from tests.test_measure import Models

N = 60


def _pull(tmp: Path) -> tuple[Path, pd.DataFrame]:
    rng = np.random.default_rng(1)
    t0 = pd.Timestamp("2026-09-01 10:00", tz="UTC")
    posts = pd.DataFrame(
        {
            "id": [f"p{i:03d}" for i in range(N)],
            "platform": rng.choice(["facebook", "instagram"], N),
            "content_type": rng.choice(["reel", "image"], N),
            "posted_at": [t0 + pd.Timedelta(hours=12 * i) for i in range(N)],
            "caption": [("halal kacchi " * int(k)) for k in rng.integers(0, 6, N)],
        }
    )
    d = tmp / "pull"
    d.mkdir()
    posts.to_parquet(d / "social_post.parquet")
    return d, posts


def _active(tmp: Path, cutoff: str) -> tuple[Path, np.ndarray, pd.DataFrame]:
    bundle = Models().m["bundle"]
    d = tmp / "20261001T000000Z_content_scorer"
    d.mkdir()
    joblib.dump(bundle, d / "model.joblib")
    (d / "report.json").write_text(
        json.dumps({"kind": "content_scorer_run", "version": "cs-active", "split": {"cutoff": cutoff}})
    )
    pull, posts = _pull(tmp)
    preds = np.array([r["label"] for r in predict(bundle, posts)])
    return d, preds, posts


def _new(tmp: Path, posts: pd.DataFrame, labels: np.ndarray, challenger_p: np.ndarray) -> Path:
    d = tmp / "20261011T000000Z_content_scorer"
    d.mkdir()
    (d / "report.json").write_text(
        json.dumps({"kind": "content_scorer_run", "version": "cs-new", "challenger": {"threshold": 0.5}})
    )
    pd.DataFrame(
        {
            "id": posts["id"],
            "platform": posts["platform"],
            "posted_at": posts["posted_at"],
            "label": labels,
            "champion_p": 0.5,
            "challenger_p": challenger_p,
        }
    ).to_csv(d / "test_predictions.csv", index=False)
    return d


def test_a_clearly_better_challenger_is_offered(tmp_path):
    active, preds, posts = _active(tmp_path, "2026-08-01T00:00:00+00:00")
    labels = 1 - preds  # the active version is wrong on every post; the challenger right on every post
    labels[:2] = [0, 1]  # both classes present whatever the active version predicts
    c = compare(_new(tmp_path, posts, labels, labels.astype(float)), active, tmp_path / "pull", 0.02)
    assert c.clearly_better and c.posts == N and c.gain is not None and c.gain > 0.5
    assert "beats the active cs-active" in c.reason


def test_a_challenger_no_better_than_the_active_version_is_held(tmp_path):
    active, preds, posts = _active(tmp_path, "2026-08-01T00:00:00+00:00")
    rng = np.random.default_rng(2)
    labels = rng.integers(0, 2, N)
    same = preds.astype(float)  # the challenger predicts exactly what the active version predicts: gain 0
    c = compare(_new(tmp_path, posts, labels, same), active, tmp_path / "pull", 0.02)
    assert not c.clearly_better and c.gain == 0.0 and "does not clearly beat" in c.reason


def test_posts_the_active_version_trained_on_are_not_evidence(tmp_path):
    # the active version's training data reaches past every test post: nothing new to compare on
    active, preds, posts = _active(tmp_path, "2026-12-01T00:00:00+00:00")
    labels = 1 - preds
    c = compare(_new(tmp_path, posts, labels, labels.astype(float)), active, tmp_path / "pull", 0.02)
    assert not c.clearly_better and c.posts == 0 and "no new evidence" in c.reason


def test_the_weekly_hold_keeps_unknown_and_drops_held(tmp_path):
    active, preds, posts = _active(tmp_path, "2026-08-01T00:00:00+00:00")
    held = _new(tmp_path, posts, np.random.default_rng(3).integers(0, 2, N), preds.astype(float))
    held_rr = json.loads((held / "report.json").read_text())
    insight = (tmp_path / "x", {"kind": "funnel_run", "version": "fn-1"})
    keep, comparisons = hold_if_not_better(
        [(held, held_rr), insight], {"content_scorer": "cs-active"}, tmp_path, tmp_path / "pull", lambda *_: None
    )
    assert keep == [insight] and not comparisons["content_scorer"]["clearly_better"]
    # no active version known (VPS unreachable): the owner is asked as before
    keep, comparisons = hold_if_not_better([(held, held_rr)], {}, tmp_path, tmp_path / "pull", lambda *_: None)
    assert keep == [(held, held_rr)] and comparisons == {}
