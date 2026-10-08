"""Distribution profiles and drift (ADR 0018). numpy/pandas only: the VPS runs the same math as the Mac.

A *profile* summarises one column of the training data so that live values can be compared to it later without
the training data itself:
- numeric: decile bin edges from training plus the share of training rows in each bin (+ a bin for missing);
- categorical / boolean: the share of each value (the 20 most common, the rest pooled as `__other__`).

Drift is the Population Stability Index, PSI = sum((live - ref) * ln(live / ref)) over the bins, with a small floor
so empty bins don't explode. Conventional reading: < 0.10 stable, 0.10-0.25 moderate, > 0.25 significant shift.

PSI has a noise floor. For two samples drawn from the SAME distribution, n_eff x PSI is roughly chi-square with
(bins - 1) degrees of freedom (n_eff = 1 / (1/n_live + 1/n_ref)), so its expected value from chance alone is about
(bins - 1) / n_eff: ~0.25 for 40 posts over 11 bins, the "significant" threshold itself. A shift therefore counts
only if it is both LARGE (PSI > 0.25) and REAL (chi-square p < 0.01 for these sample sizes): `is_drift`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

EPS = 1e-4
MISSING = "__missing__"
OTHER = "__other__"
TOP_K = 20
SCORE_EDGES = [round(x * 0.1, 1) for x in range(11)]  # probabilities: fixed 10 bins on [0, 1]
STABLE, SIGNIFICANT = 0.10, 0.25


P_VALUE = 0.01


def drift_p_value(value: float, bins: int, n_live: int, n_ref: int) -> float:
    """Chance of a PSI at least this large if live and reference came from the same distribution."""
    from scipy.stats import chi2

    if n_live <= 0 or n_ref <= 0 or bins < 2:
        return 1.0
    n_eff = 1 / (1 / n_live + 1 / n_ref)
    return float(chi2.sf(n_eff * value, bins - 1))


def is_drift(value: float, bins: int, n_live: int, n_ref: int) -> bool:
    return value > SIGNIFICANT and drift_p_value(value, bins, n_live, n_ref) < P_VALUE


def psi(ref: Sequence[float], live: Sequence[float]) -> float:
    total = 0.0
    for r, a in zip(ref, live, strict=True):
        r, a = max(r, EPS), max(a, EPS)
        total += (a - r) * math.log(a / r)
    return round(total, 4)


def level(value: float | None) -> str:
    if value is None:
        return "unknown"
    return "stable" if value < STABLE else "moderate" if value < SIGNIFICANT else "significant"


def _shares(counts: Sequence[int]) -> list[float]:
    n = sum(counts)
    return [c / n if n else 0.0 for c in counts]


def numeric_edges(values: pd.Series, bins: int = 10) -> list[float]:
    """Inner bin edges at training quantiles; duplicates collapse (a column with few distinct values gets few bins)."""
    v = pd.to_numeric(values, errors="coerce").dropna().astype(float)
    if v.empty:
        return []
    qs = np.unique(np.quantile(v, np.linspace(0, 1, bins + 1)[1:-1]))
    return [round(float(x), 6) for x in qs]


def numeric_counts(values: pd.Series, edges: Sequence[float]) -> list[int]:
    """Counts per bin: (-inf, e0], (e0, e1], ..., (e_last, inf), then missing."""
    v = pd.to_numeric(values, errors="coerce")
    missing = int(v.isna().sum())
    idx = np.searchsorted(np.asarray(edges, dtype=float), v.dropna().astype(float).to_numpy(), side="left")
    counts = np.bincount(idx, minlength=len(edges) + 1).tolist()
    return [int(c) for c in counts] + [missing]


def categories(values: pd.Series) -> list[str]:
    s = values.map(lambda x: MISSING if pd.isna(x) else str(x))
    return [str(k) for k in s.value_counts().head(TOP_K).index]


def categorical_counts(values: pd.Series, cats: Sequence[str]) -> list[int]:
    s = values.map(lambda x: MISSING if pd.isna(x) else str(x))
    known = set(cats)
    counts = [int((s == c).sum()) for c in cats]
    return [*counts, int((~s.isin(known)).sum())]  # last: unseen in training -> OTHER


def is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def profile(values: pd.Series) -> dict[str, Any]:
    if is_numeric(values):
        edges = numeric_edges(values)
        return {
            "kind": "numeric",
            "edges": edges,
            "shares": _shares(numeric_counts(values, edges)),
            "n": int(len(values)),
        }
    cats = categories(values)
    return {
        "kind": "categorical",
        "categories": cats,
        "shares": _shares(categorical_counts(values, cats)),
        "n": int(len(values)),
    }


def live_shares(ref: dict[str, Any], values: pd.Series) -> list[float]:
    if ref["kind"] == "numeric":
        return _shares(numeric_counts(values, ref["edges"]))
    return _shares(categorical_counts(values, ref["categories"]))


def score_profile(scores: Sequence[float]) -> dict[str, Any]:
    s = pd.Series(list(scores), dtype=float)
    return {
        "edges": SCORE_EDGES,
        "shares": _shares(score_counts(s)),
        "mean": round(float(s.mean()), 4) if len(s) else None,
        "n": int(len(s)),
    }


def score_counts(scores: pd.Series) -> list[int]:
    idx = np.clip(np.floor(scores.astype(float).to_numpy() * 10).astype(int), 0, 9)
    return [int(c) for c in np.bincount(idx, minlength=10)]


def feature_drift(reference: dict[str, Any], live: pd.DataFrame) -> dict[str, float]:
    """PSI per feature present in both the reference and the live frame."""
    out = {}
    for name, ref in reference.items():
        if name in live:
            out[name] = psi(ref["shares"], live_shares(ref, live[name]))
    return out


def score_drift(reference: dict[str, Any], scores: Sequence[float]) -> float:
    return psi(reference["shares"], _shares(score_counts(pd.Series(list(scores), dtype=float))))
