"""Plain statistics with honest intervals: no model, nothing to tune.

For each (platform, feature, value) the question is: is the median lift of posts with this value different from the
median lift of all the other posts? A bootstrap (2,000 resamples, a fixed seed) gives a 95% interval for that
difference. The finding is "works" only when the whole interval is above zero and both groups have at least MIN_N posts;
"hurts" when it is below zero. Many values are tested at once, so about 1 in 20 findings is a false alarm by chance
alone: the playbook says how many were tested, and treats findings as things to try, not as laws.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

MIN_N = 15  # posts with the value, and posts without it, before any statement is made
BOOTSTRAP = 2000
SEED = 7


def _median_ci(x: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    draws = np.median(rng.choice(x, size=(BOOTSTRAP, len(x))), axis=1)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def _diff_ci(a: np.ndarray, b: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    da = np.median(rng.choice(a, size=(BOOTSTRAP, len(a))), axis=1)
    db = np.median(rng.choice(b, size=(BOOTSTRAP, len(b))), axis=1)
    lo, hi = np.percentile(da - db, [2.5, 97.5])
    return float(lo), float(hi)


def findings(df: pd.DataFrame, features: tuple[str, ...], outcome: str = "lift_engagement") -> list[dict[str, Any]]:
    """Every (feature, value) of one platform's matured posts, with its evidence. `df` has one platform."""
    rng = np.random.default_rng(SEED)
    d = df[df[outcome].notna()]
    out: list[dict[str, Any]] = []
    for feature in features:
        if feature not in d:
            continue
        for value, g in d.groupby(feature, dropna=True):
            a = g[outcome].to_numpy(dtype=float)
            b = d.loc[d[feature] != value, outcome].to_numpy(dtype=float)
            row: dict[str, Any] = {"feature": feature, "value": str(value), "n": len(a), "rest_n": len(b),
                                   "median_lift": round(float(np.median(a)), 2)}  # fmt: skip
            if len(a) < MIN_N or len(b) < MIN_N:
                row["verdict"] = "too_few"
            else:
                lo, hi = _diff_ci(a, b, rng)
                c_lo, c_hi = _median_ci(a, rng)
                row.update(diff=round(float(np.median(a) - np.median(b)), 2), diff_ci=[round(lo, 2), round(hi, 2)],
                           ci=[round(c_lo, 2), round(c_hi, 2)])  # fmt: skip
                row["verdict"] = "works" if lo > 0 else "hurts" if hi < 0 else "no_clear_difference"
            out.append(row)
    return out
