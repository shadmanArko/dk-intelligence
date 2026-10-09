"""Ask for a scorer approval only when the new version clearly beats the ACTIVE one (owner's decision, 2026-10-09).

The judge compares a challenger with today's harness predictor (the champion). That says "better than the old system",
not "better than what we serve now", so a passing challenger asked for approval every Sunday. Each approval restarts
the new version's monitoring and guardrail evidence from zero (it is counted per version), and at ~15 posts a week a
version replaced weekly never reaches the guardrail's 40 outcomes.

The incumbent check scores the active version on the challenger's test posts, keeping only posts the active version
never trained on (posted after its own training cutoff), and asks for approval only if the challenger's balanced
accuracy is higher by at least the judge's `min_gain_balanced_accuracy` on at least `min_posts` of them. Too few
shared posts means no new evidence, so the active version stays.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score

from intel.judge import paired_gain_ci

MIN_POSTS = 30  # the live gate's minimum, too: fewer shared posts are not evidence


@dataclass
class Comparison:
    model: str
    active_version: str
    new_version: str
    posts: int
    active_balanced_accuracy: float | None
    new_balanced_accuracy: float | None
    gain: float | None
    gain_ci95: tuple[float, float] | None
    min_gain: float
    clearly_better: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _posts(pull_dir: Path, ids: pd.Series) -> pd.DataFrame:
    sp = pd.read_parquet(
        pull_dir / "social_post.parquet", columns=["id", "platform", "content_type", "posted_at", "caption"]
    )
    sp = sp[sp["id"].astype(str).isin(set(ids.astype(str)))].copy()
    sp["caption"] = sp["caption"].fillna("")
    sp["posted_at"] = pd.to_datetime(sp["posted_at"], utc=True)
    return sp


def compare(new_run: Path, active_run: Path, pull_dir: Path, min_gain: float, min_posts: int = MIN_POSTS) -> Comparison:
    """Challenger `new_run` vs the active version's run `active_run` on the challenger's test posts."""
    from intel.models.content_scorer.predict import predict

    new_rep = json.loads((new_run / "report.json").read_text())
    act_rep = json.loads((active_run / "report.json").read_text())
    name = new_rep["kind"].removesuffix("_run")
    base = dict(model=name, active_version=act_rep["version"], new_version=new_rep["version"], min_gain=min_gain)

    te = pd.read_csv(new_run / "test_predictions.csv", dtype={"id": str})
    te["posted_at"] = pd.to_datetime(te["posted_at"], utc=True)
    cutoff = pd.Timestamp(act_rep["split"]["cutoff"])
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff
    te = te[te["posted_at"] >= cutoff]  # posts the active version trained on would flatter it
    posts = _posts(pull_dir, te["id"])
    te = te[te["id"].isin(set(posts["id"].astype(str)))]
    if len(te) < min_posts:
        return Comparison(
            **base,
            posts=len(te),
            active_balanced_accuracy=None,
            new_balanced_accuracy=None,
            gain=None,
            gain_ci95=None,
            clearly_better=False,
            reason=f"only {len(te)} test posts newer than the active version's training data (need {min_posts}): "
            "no new evidence, the active version stays",
        )

    posts = posts.set_index(posts["id"].astype(str)).loc[te["id"]].reset_index(drop=True)
    bundle = joblib.load(active_run / "model.joblib")
    active_pred = np.array([r["label"] for r in predict(bundle, posts)])
    new_pred = (te["challenger_p"].to_numpy() >= float(new_rep["challenger"]["threshold"])).astype(int)
    y = te["label"].to_numpy().astype(int)
    a, n = float(balanced_accuracy_score(y, active_pred)), float(balanced_accuracy_score(y, new_pred))
    gain = round(n - a, 4)
    ci = paired_gain_ci(y, active_pred, new_pred)
    ok = gain >= min_gain
    return Comparison(
        **base,
        posts=len(te),
        active_balanced_accuracy=round(a, 4),
        new_balanced_accuracy=round(n, 4),
        gain=gain,
        gain_ci95=ci,
        clearly_better=ok,
        reason=(
            f"{'beats' if ok else 'does not clearly beat'} the active {act_rep['version']}: balanced accuracy "
            f"{n:.3f} vs {a:.3f} (gain {gain:+.3f}, need {min_gain:+.2f}; 95% range {ci[0]:+.3f} to {ci[1]:+.3f}) "
            f"on {len(te)} posts it never trained on"
        ),
    )
