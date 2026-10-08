"""Reproduce a scorer training run from its recorded lineage and compare (ADR 0019).

A model is only auditable if it can be rebuilt: same snapshots (pull + market, both immutable and checksummed), same
code (the sealed files match the lock), same recipe (seeded). `reproduce(run_dir, data_dir)` trains again from the
run's own recorded snapshots into a scratch folder and compares what must not change: the chosen model and its
parameters, the cross-validation leaderboard, the test metrics of challenger and champion, and every test prediction.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

TOLERANCE = 1e-9


def _diff(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b)) for d in _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in _diff(x, y, f"{path}[{i}]")]
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return [] if abs(float(a) - float(b)) <= TOLERANCE else [f"{path}: {a} -> {b}"]
    return [] if a == b else [f"{path}: {a!r} -> {b!r}"]


def compare_runs(original: Path, again: Path) -> dict[str, Any]:
    import pandas as pd

    r1 = json.loads((original / "report.json").read_text())
    r2 = json.loads((again / "report.json").read_text())
    keep = ("model", "params", "features", "threshold", "test")
    diffs = _diff({k: r1["challenger"].get(k) for k in keep}, {k: r2["challenger"].get(k) for k in keep}, "challenger")
    diffs += _diff(r1["champion"].get("test"), r2["champion"].get("test"), "champion.test")
    diffs += _diff(
        [{k: v for k, v in c.items() if k != "_cand"} for c in r1.get("leaderboard_cv", [])],
        [{k: v for k, v in c.items() if k != "_cand"} for c in r2.get("leaderboard_cv", [])],
        "leaderboard_cv",
    )
    diffs += _diff(r1["split"], r2["split"], "split")
    p1, p2 = pd.read_csv(original / "test_predictions.csv"), pd.read_csv(again / "test_predictions.csv")
    worst = 0.0
    if list(p1["id"]) != list(p2["id"]):
        diffs.append("test posts differ")
    else:
        worst = float((p1["challenger_p"] - p2["challenger_p"]).abs().max())
        if worst > TOLERANCE:
            diffs.append(f"test predictions differ by up to {worst:.2e}")
    return {
        "identical": not diffs,
        "differences": diffs,
        "max_prediction_difference": worst,
        "test_posts": len(p1),
        "original": r1["version"],
        "reproduced": r2["version"],
    }


def reproduce(run_dir: Path, data_dir: Path, log: Any = None) -> dict[str, Any]:
    """Train again from the run's recorded snapshots and compare. Scorer runs (content / reach) only."""
    from intel.models.content_scorer.run import run as train_run
    from intel.sealed import diff as sealed_diff

    report = json.loads((run_dir / "report.json").read_text())
    name = report["kind"].removesuffix("_run")
    if name not in ("content_scorer", "reach_scorer"):
        raise ValueError(f"{run_dir.name}: only scorer runs can be reproduced (this is {name})")
    pull, market = data_dir / "pulls" / report["pull_id"], data_dir / "market" / report["market_id"]
    missing = [p.name for p in (pull, market) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"recorded snapshots no longer kept: {', '.join(missing)}")
    sealed = [d for d in sealed_diff() if d.startswith(f"{name}:")]
    with tempfile.TemporaryDirectory() as tmp:
        again = train_run(
            pull, market, Path(tmp), None, log=log or (lambda *_: None), target=report.get("target", "engagement")
        )
        result = compare_runs(run_dir, again)
    result.update(
        run=run_dir.name,
        pull=pull.name,
        market=market.name,
        sealed_code="matches the lock" if not sealed else "differs: " + "; ".join(sealed),
    )
    return result
