"""Serving-side prediction: same feature function, frozen market context, explanation for linear models."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import MarketContext, build_features


def predict(bundle: dict, posts: pd.DataFrame, top_k: int = 3) -> list[dict]:
    ctx = MarketContext.from_dict(bundle["context"])
    X = build_features(posts, ctx)[bundle["columns"]]
    pipe = bundle["pipeline"]
    proba = pipe.predict_proba(X)[:, 1]
    reasons: list[list[dict]] = [[] for _ in range(len(X))]
    clf = pipe.named_steps["clf"]
    if hasattr(clf, "coef_"):
        pre = pipe.named_steps["pre"]
        Z = pre.transform(X)
        Z = Z.toarray() if hasattr(Z, "toarray") else np.asarray(Z, dtype=float)
        contrib = Z * clf.coef_[0]
        names = pre.get_feature_names_out()
        for i, row in enumerate(contrib):
            order = np.argsort(-np.abs(row))[:top_k]
            reasons[i] = [{"feature": str(names[j]), "contribution": round(float(row[j]), 4)} for j in order]
    return [
        {
            "probability": round(float(p), 4),
            "label": int(p >= bundle["threshold"]),
            "threshold": bundle["threshold"],
            "model_version": bundle["version"],
            "top_reasons": r,
        }
        for p, r in zip(proba, reasons, strict=True)
    ]
