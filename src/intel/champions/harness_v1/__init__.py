"""The current champion: the harness engagement predictor (`2026-09-23-logreg-v1`).

Two ways to use it, both needed for a fair judge:
- `recipe()`: the exact training recipe (same preprocessing, logistic regression C, threshold), so it can be
  retrained on any pull and compared with a challenger on the SAME split.
- `load_production()`: the artifact actually serving on the VPS, hash-checked before loading (joblib executes
  pickle code), to measure it on posts it has never seen.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .features import ALL_FEATURE_COLUMNS, NOMINAL_COLUMNS, NUMERIC_COLUMNS, build_features

NAME = "harness_v1"
MODEL_VERSION = "2026-09-23-logreg-v1"
C = 2.636650898730358
THRESHOLD = 0.49
SEED = 0
FEATURES_SHA256 = "121f23319846ad337c19d3579e9e34acedc1b8990211a18607f4967d7c44f1aa"
ARTIFACT_DIR = "ml/05-production/artifacts"

__all__ = ["ALL_FEATURE_COLUMNS", "build_features", "recipe", "load_production", "features_frame"]


def recipe() -> Pipeline:
    """Copied from ml/03-modeling/train.py (make_preprocessor + logreg_pipe) with the tuned C."""
    nominal = Pipeline(
        [("impute", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore"))]
    )
    numeric = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    pre = ColumnTransformer([("nominal", nominal, NOMINAL_COLUMNS), ("numeric", numeric, NUMERIC_COLUMNS)])
    clf = LogisticRegression(C=C, max_iter=2000, class_weight="balanced", random_state=SEED)
    return Pipeline([("pre", pre), ("clf", clf)])


def features_frame(df: pd.DataFrame) -> pd.DataFrame:
    return build_features(df[["platform", "content_type", "posted_at", "caption"]])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_production(harness_dir: Path):
    """Load the deployed artifact after checking model.sha256 (same check as predictor/app/model.py)."""
    d = harness_dir / ARTIFACT_DIR
    expected = (d / "model.sha256").read_text().strip()
    actual = _sha256(d / "model.joblib")
    if actual != expected:
        raise RuntimeError(f"model.joblib hash mismatch ({actual} != {expected}); refusing to load")
    meta = json.loads((d / "model_metadata.json").read_text())
    return joblib.load(d / "model.joblib"), meta
