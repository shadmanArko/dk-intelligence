"""The judge: automatic gates a challenger must pass before a human is even asked (plan section 3.4)."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import balanced_accuracy_score


def paired_gain_ci(y, champ_pred, chall_pred, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% bootstrap interval of (challenger - champion) balanced accuracy on the SAME resampled posts."""
    y, a, b = (np.asarray(v) for v in (y, champ_pred, chall_pred))
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) == 2:
            diffs.append(balanced_accuracy_score(y[i], b[i]) - balanced_accuracy_score(y[i], a[i]))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return round(float(lo), 4), round(float(hi), 4)


def gate(name: str, ok: bool | None, detail: str) -> dict:
    return {"gate": name, "status": "pass" if ok else ("pending" if ok is None else "fail"), "detail": detail}


def judge(
    champ: dict,
    chall: dict,
    g: dict,
    *,
    data_ok: tuple[bool, str],
    leakage_ok: tuple[bool, str],
    live_posts: int,
    size_mb: float,
    latency_ms: float,
    gain_ci: tuple[float, float],
) -> dict:
    gain = round(chall["balanced_accuracy"] - champ["balanced_accuracy"], 4)
    drops = {
        p: round(champ["per_platform"][p]["balanced_accuracy"] - v["balanced_accuracy"], 4)
        for p, v in chall["per_platform"].items()
        if v["balanced_accuracy"] is not None and champ["per_platform"][p]["balanced_accuracy"] is not None
    }
    gates = [
        gate("data", *data_ok),
        gate("no_leakage", *leakage_ok),
        gate(
            "better",
            gain >= g["min_gain_balanced_accuracy"] and (chall["roc_auc"] or 0) >= (champ["roc_auc"] or 0),
            f"balanced accuracy {chall['balanced_accuracy']} vs {champ['balanced_accuracy']} (gain {gain:+}, "
            f"95% range {gain_ci[0]:+} to {gain_ci[1]:+}); AUC {chall['roc_auc']} vs {champ['roc_auc']}",
        ),
        gate(
            "no_platform_worse",
            all(d <= g["max_platform_drop"] for d in drops.values()),
            ", ".join(f"{p} {-d:+}" for p, d in drops.items()),
        ),
        gate(
            "calibrated",
            chall["brier"] <= champ["brier"] + g["max_brier_increase"],
            f"Brier {chall['brier']} vs {champ['brier']}",
        ),
        gate(
            "live_evidence",
            None if live_posts < g["min_live_posts"] else True,
            f"{live_posts} measured live posts (need {g['min_live_posts']}); ledger starts in M6",
        ),
        gate(
            "works",
            size_mb <= g["max_package_mb"] and latency_ms <= g["max_latency_ms"],
            f"model {size_mb:.2f} MB, {latency_ms:.0f} ms per post",
        ),
    ]
    failed = [x["gate"] for x in gates if x["status"] == "fail"]
    return {
        "verdict": "fail" if failed else "pass",
        "failed": failed,
        "gain": gain,
        "gain_ci95": list(gain_ci),
        "gates": gates,
    }
