"""Reference profiles for model monitoring, built on the Mac from a saved training run (ADR 0018).

One code path for every model version: rebuild the training features from what the run recorded (pull id, market
context frozen in the bundle, time-split cutoff), and take the test-set scores from `test_predictions.csv`. So a
brand-new run and an older, already-active model get identical treatment, and the profile matches what serving uses.

    reference = reference_from_run(run_dir, pull_dir, market_dir)
    -> {"model_name", "model_version", "features": {name: profile}, "scores": profile of test probabilities,
        "base_rate", "test": {balanced_accuracy, roc_auc, brier, n}, "threshold", "built_at", ...}
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from intel.monitoring.drift import profile, score_profile

FORMAT = 1


def build_reference(
    *,
    model_name: str,
    model_version: str,
    X_train: pd.DataFrame,
    y_train: pd.Series,
    test_scores: pd.Series,
    test_labels: pd.Series,
    threshold: float,
    test_metrics: dict[str, Any],
    data: dict[str, str],
) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "model_name": model_name,
        "model_version": model_version,
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "data": data,
        "features": {c: profile(X_train[c]) for c in X_train.columns},
        "scores": score_profile(test_scores.astype(float).tolist()),
        "base_rate": round(float(pd.Series(y_train).astype(float).mean()), 4),
        "test_base_rate": round(float(pd.Series(test_labels).astype(float).mean()), 4),
        "threshold": float(threshold),
        "test": {k: test_metrics.get(k) for k in ("balanced_accuracy", "roc_auc", "brier", "n")},
        "n_train": int(len(X_train)),
    }


def reference_from_run(run_dir: Path, pull_dir: Path, market_dir: Path) -> dict[str, Any]:
    """Rebuild the training features of a scorer run and profile them (content_scorer / reach_scorer runs)."""
    import joblib

    from intel.datasets.content import build_content_dataset
    from intel.models.content_scorer.features import MarketContext, build_features
    from intel.models.content_scorer.run import INPUTS, TARGETS

    report = json.loads((run_dir / "report.json").read_text())
    if report["pull_id"] != pull_dir.name or report["market_id"] != market_dir.name:
        raise ValueError(f"run {run_dir.name} was trained on {report['pull_id']} / {report['market_id']}")
    bundle = joblib.load(run_dir / "model.joblib")
    t = TARGETS[report.get("target", "engagement")]
    y = t["label"]
    ds = build_content_dataset(pull_dir)
    matured = ds[ds["matured"] & ds[y].notna()]
    cutoff = pd.Timestamp(report["split"]["cutoff"])
    train = matured[matured["posted_at"] < cutoff]
    X = build_features(train[INPUTS], MarketContext.from_dict(bundle["context"]))[bundle["columns"]]
    tp = pd.read_csv(run_dir / "test_predictions.csv")
    return build_reference(
        model_name=t["name"],
        model_version=report["version"],
        X_train=X,
        y_train=train[y].astype(int),
        test_scores=tp["challenger_p"],
        test_labels=tp["label"],
        threshold=float(bundle["threshold"]),
        test_metrics=report["challenger"]["test"],
        data={"pull_id": report["pull_id"], "market_id": report["market_id"], "run": run_dir.name},
    )


def write_reference(run_dir: Path, pull_dir: Path, market_dir: Path) -> Path:
    ref = reference_from_run(run_dir, pull_dir, market_dir)
    path = run_dir / "reference.json"
    path.write_text(json.dumps(ref, indent=1) + "\n")
    return path
