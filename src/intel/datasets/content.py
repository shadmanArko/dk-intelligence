"""Content dataset: one row per Facebook / Instagram post with the champion's label.

Label (same as the harness predictor, `ml/00-problem-framing`): engagement = likes + comments + shares at the
post's LATEST metrics snapshot; label = 1 when engagement > the platform's expanding median up to and including
this post (posts ordered by posted_at). Kept identical on purpose, so champion and challenger are judged on the
same question.

Differences from the harness extract, both deliberate:
- The harness SQL joins every snapshot; it had one per post on 2026-09-23. Since hourly/daily snapshots started
  there are several per post, so we take the latest one per post (otherwise posts would be duplicated).
- `matured` marks posts whose latest snapshot is at least MATURE_DAYS after posting. Younger posts are still
  collecting engagement, so their label is not final; evaluation uses matured posts only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from intel.data import load_table

PLATFORMS = ("facebook", "instagram")
MATURE_DAYS = 7


def expanding_median_label(df: pd.DataFrame) -> pd.DataFrame:
    """Add expanding_median_engagement and label per platform (input sorted by posted_at within platform).

    Mirrors the SQL: percentile_cont(0.5) over rows with rn <= current rn, NULL engagements ignored, and a NULL
    engagement gives label 0 (NULL > x is not true)."""
    out = []
    for _, g in df.groupby("platform", sort=True):
        g = g.sort_values(["posted_at", "id"], kind="stable").copy()
        vals = g["engagement"].to_numpy(dtype=float)
        med = np.full(len(vals), np.nan)
        for i in range(len(vals)):
            seen = vals[: i + 1]
            seen = seen[~np.isnan(seen)]
            if len(seen):
                med[i] = np.median(seen)
        g["expanding_median_engagement"] = med
        g["label"] = (g["engagement"].to_numpy(dtype=float) > med).astype(int)
        out.append(g)
    return pd.concat(out).sort_values(["platform", "posted_at"], kind="stable").reset_index(drop=True)


def build_content_dataset(pull_dir: Path) -> pd.DataFrame:
    posts = load_table(pull_dir, "social_post", ["id", "platform", "posted_at", "content_type", "caption"])
    posts = posts[posts["platform"].isin(PLATFORMS)]
    snaps = load_table(
        pull_dir,
        "social_metrics_snapshot",
        ["social_post_id", "captured_at", "likes", "comments", "shares", "impressions", "reach"],
    )
    latest = (
        snaps.sort_values(["social_post_id", "captured_at"], kind="stable")
        .groupby("social_post_id", as_index=False)
        .tail(1)
        .rename(columns={"social_post_id": "id"})
    )
    df = posts.merge(latest, on="id", how="inner")
    parts = df[["likes", "comments", "shares"]].astype("Float64")
    df["engagement"] = parts.sum(axis=1, skipna=False).astype(float)
    df["snapshot_age_days"] = (df["captured_at"] - df["posted_at"]).dt.total_seconds() / 86400
    df["matured"] = df["snapshot_age_days"] >= MATURE_DAYS
    return expanding_median_label(df)


def time_split(df: pd.DataFrame, test_fraction: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    """Time-ordered split: the newest `test_fraction` of posts is the holdout. Returns (train, test, cutoff)."""
    d = df.sort_values("posted_at", kind="stable").reset_index(drop=True)
    cut = int(round(len(d) * (1 - test_fraction)))
    cutoff = d["posted_at"].iloc[cut]
    return d[d["posted_at"] < cutoff], d[d["posted_at"] >= cutoff], cutoff
