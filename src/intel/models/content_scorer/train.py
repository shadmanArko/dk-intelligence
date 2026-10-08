"""Train content scorer candidates on the TRAIN part only; pick by time-ordered CV; never look at the test part.

Candidates: logistic regression on (champion features + market/occasion) and on all features, and a small
gradient-boosted tree on all features. The winner must beat the next one by > TIE on CV, otherwise the simpler
model wins (logreg < tree; fewer features < more).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from . import features as F

SEED = 0
CV = TimeSeriesSplit(n_splits=5)
TIE = 0.01
CHAMPION_COLS = [
    "platform",
    "content_type_filled",
    "is_video",
    "day_of_week",
    "hour_bucket",
    "caption_length",
    "hashtag_count",
]
FEATURE_SETS = {
    # today's 7 features (for the reach question this is a real challenger: same inputs, the right label; it scored
    # best in training-only CV on 2026-10-07, before any test look)
    "champion": CHAMPION_COLS,
    # chosen 2026-10-07 from a single-feature ablation on TRAIN-fold CV only (docs/results/M3_content_scorer.md)
    "champion_plus_text": CHAMPION_COLS + ["first_line_length", "script_bengali"],
    "champion_plus_market": CHAMPION_COLS + ["market_cue_lift", "n_cues", "days_to_feast", "days_since_feast"],
    "all": F.ALL,
}


def preprocessor(cols: list[str]) -> ColumnTransformer:
    nominal = [c for c in cols if c in F.NOMINAL]
    numeric = [c for c in cols if c in F.NUMERIC]
    binary = [c for c in cols if c in F.BINARY]
    parts = [
        (
            "nominal",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="most_frequent")),
                    ("onehot", OneHotEncoder(handle_unknown="ignore")),
                ]
            ),
            nominal,
        ),
        ("numeric", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
    ]
    if binary:
        parts.append(("binary", "passthrough", binary))
    return ColumnTransformer(parts)


def candidates() -> list[dict]:
    out = []
    for fs, cols in FEATURE_SETS.items():
        for c in np.logspace(-3, 1.5, 10):
            out.append(
                {
                    "name": f"logreg_{fs}",
                    "family": "logreg",
                    "features": fs,
                    "params": {"C": float(c)},
                    "simplicity": (0, len(cols)),
                    "pipe": Pipeline(
                        [
                            ("pre", preprocessor(cols)),
                            ("clf", LogisticRegression(C=c, max_iter=3000, class_weight="balanced", random_state=SEED)),
                        ]
                    ),
                }
            )
    for depth in (2, 3):
        for lr in (0.03, 0.1):
            out.append(
                {
                    "name": "hgb_all",
                    "family": "hgb",
                    "features": "all",
                    "params": {"max_depth": depth, "learning_rate": lr},
                    "simplicity": (1, len(F.ALL)),
                    "pipe": Pipeline(
                        [
                            ("pre", preprocessor(F.ALL)),
                            (
                                "clf",
                                HistGradientBoostingClassifier(
                                    max_depth=depth,
                                    learning_rate=lr,
                                    max_iter=150,
                                    min_samples_leaf=15,
                                    l2_regularization=1.0,
                                    class_weight="balanced",
                                    random_state=SEED,
                                ),
                            ),
                        ]
                    ),
                }
            )
    return out


def oof_proba(pipe: Pipeline, X: pd.DataFrame, y: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Expanding-window out-of-fold probabilities (same protocol as the champion's stage 03)."""
    idx, prob = [], []
    for tr, te in CV.split(X):
        est = clone(pipe).fit(X.iloc[tr], y.iloc[tr])
        idx.extend(te.tolist())
        prob.extend(est.predict_proba(X.iloc[te])[:, 1].tolist())
    return np.array(idx), np.array(prob)


def best_threshold(y: np.ndarray, p: np.ndarray) -> float:
    grid = np.round(np.arange(0.30, 0.71, 0.01), 2)
    scores = [balanced_accuracy_score(y, (p >= t).astype(int)) for t in grid]
    return float(grid[int(np.argmax(scores))])


@dataclass
class Trained:
    name: str
    params: dict
    features: str
    columns: list[str]
    pipeline: Pipeline
    threshold: float
    cv_balanced_accuracy: float
    leaderboard: list[dict]


def train(X: pd.DataFrame, y: pd.Series, log=lambda **_: None) -> Trained:
    board = []
    for cand in candidates():
        cols = FEATURE_SETS[cand["features"]]
        idx, p = oof_proba(cand["pipe"], X[cols], y)
        thr = best_threshold(y.iloc[idx].to_numpy(), p)
        ba = balanced_accuracy_score(y.iloc[idx], (p >= 0.5).astype(int))
        row = {
            "name": cand["name"],
            "params": cand["params"],
            "features": cand["features"],
            "cv_balanced_accuracy@0.5": round(float(ba), 4),
            "cv_threshold": thr,
            "simplicity": cand["simplicity"],
            "_cand": cand,
        }
        board.append(row)
        log(candidate=cand["name"], params=cand["params"], cv_balanced_accuracy=ba)
    # best config per model name, then the simplest within TIE of the best
    top_per: dict[str, dict[str, Any]] = {}
    for r in board:
        if r["name"] not in top_per or r["cv_balanced_accuracy@0.5"] > top_per[r["name"]]["cv_balanced_accuracy@0.5"]:
            top_per[r["name"]] = r
    best_score = max(r["cv_balanced_accuracy@0.5"] for r in top_per.values())
    winner = min(
        (r for r in top_per.values() if r["cv_balanced_accuracy@0.5"] >= best_score - TIE),
        key=lambda r: r["simplicity"],
    )
    cand = winner["_cand"]
    cols = FEATURE_SETS[cand["features"]]
    idx, p = oof_proba(cand["pipe"], X[cols], y)
    thr = best_threshold(y.iloc[idx].to_numpy(), p)
    cv_ba = balanced_accuracy_score(y.iloc[idx], (p >= thr).astype(int))
    final = clone(cand["pipe"]).fit(X[cols], y)
    leaderboard = sorted(
        ({k: v for k, v in r.items() if not k.startswith("_") and k != "simplicity"} for r in top_per.values()),
        key=lambda r: -r["cv_balanced_accuracy@0.5"],
    )
    return Trained(
        cand["name"], cand["params"], cand["features"], cols, final, thr, round(float(cv_ba), 4), leaderboard
    )
