"""Shared evaluation: every model (champion or challenger) is scored by exactly this code."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, confusion_matrix, roc_auc_score


def _safe_auc(y, p) -> float | None:
    return float(roc_auc_score(y, p)) if len(set(y)) == 2 else None


def bootstrap_ci(y: np.ndarray, pred: np.ndarray, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% bootstrap interval of balanced accuracy (small samples: read the width, not just the point)."""
    rng = np.random.default_rng(seed)
    scores = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) == 2:
            scores.append(balanced_accuracy_score(y[i], pred[i]))
    lo, hi = np.percentile(scores, [2.5, 97.5])
    return float(lo), float(hi)


def score(y, proba, threshold: float, platform: pd.Series | None = None) -> dict:
    y = np.asarray(y, dtype=int)
    proba = np.asarray(proba, dtype=float)
    pred = (proba >= threshold).astype(int)
    out = {
        "n": int(len(y)),
        "positive_rate": round(float(y.mean()), 4) if len(y) else None,
        "balanced_accuracy": round(float(balanced_accuracy_score(y, pred)), 4) if len(set(y)) == 2 else None,
        "balanced_accuracy_ci95": [round(v, 4) for v in bootstrap_ci(y, pred)] if len(set(y)) == 2 else None,
        "roc_auc": None if (a := _safe_auc(y, proba)) is None else round(a, 4),
        "brier": round(float(brier_score_loss(y, proba)), 4) if len(y) else None,
        "confusion_matrix": confusion_matrix(y, pred, labels=[0, 1]).tolist(),
        "threshold": threshold,
    }
    if platform is not None:
        plat = np.asarray(platform)
        out["per_platform"] = {
            p: {
                "n": int((plat == p).sum()),
                "balanced_accuracy": round(float(balanced_accuracy_score(y[plat == p], pred[plat == p])), 4)
                if len(set(y[plat == p])) == 2
                else None,
            }
            for p in sorted(set(plat))
        }
    return out
