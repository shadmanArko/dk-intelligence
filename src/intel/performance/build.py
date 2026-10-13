"""One row per post: what it was, and what it did, growth-normalised.

Outcome = likes + comments + shares + saves (engagement) and reach (audience; views where a platform has no reach),
each at the post's LATEST snapshot. The accounts grew a lot (Facebook reach about 5x since April), so a raw count
compares a post with a different era. `lift` compares each post with the median of the previous 20 matured posts on the
same platform: 1.0 = a usual post, 2.0 = twice what we were getting then. Counts are small (a usual post has a few
likes), so lift is smoothed: (x + 1) / (median + 1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from intel.data import load_table
from intel.performance.features import post_features
from intel.performance.urls import post_key_of

MATURE_DAYS = 7  # a post's numbers keep growing for a week; younger posts are not judged yet
WINDOW = 20
MIN_PRIOR = 5


def _lift(values: pd.Series) -> pd.Series:
    prior = values.shift(1).rolling(WINDOW, min_periods=MIN_PRIOR).median()
    return (values.astype(float) + 1.0) / (prior + 1.0)


def build_dataset(pull_dir: Path) -> pd.DataFrame:
    posts = load_table(pull_dir, "social_post", ["id", "platform", "posted_at", "permalink", "content_type", "caption"])
    snaps = load_table(
        pull_dir,
        "social_metrics_snapshot",
        ["social_post_id", "captured_at", "likes", "comments", "shares", "saves", "impressions", "reach", "views"],
    )
    latest = (
        snaps.sort_values(["social_post_id", "captured_at"], kind="stable")
        .groupby("social_post_id", as_index=False)
        .tail(1)
        .rename(columns={"social_post_id": "id"})
    )
    df = posts.merge(latest, on="id", how="inner")
    df = df[df["likes"].notna()].copy()
    df["engagement"] = df[["likes", "comments", "shares", "saves"]].astype(float).fillna(0).sum(axis=1)
    df["audience"] = df["reach"].astype(float).fillna(df["views"].astype(float)).fillna(df["impressions"].astype(float))
    df["age_days"] = (df["captured_at"] - df["posted_at"]).dt.total_seconds() / 86400
    df["matured"] = df["age_days"] >= MATURE_DAYS
    feats = pd.DataFrame(
        [
            post_features(
                pd.Timestamp(when).to_pydatetime(),
                kind if isinstance(kind, str) else None,
                text if isinstance(text, str) else None,
            )
            for when, kind, text in zip(df["posted_at"], df["content_type"], df["caption"], strict=True)
        ],
        index=df.index,
    )
    df = pd.concat([df, feats], axis=1)
    df["key"] = ["{}:{}".format(*k) if (k := post_key_of(u)) else None for u in df["permalink"]]
    out = []
    for _, g in df.groupby("platform", sort=True):
        g = g.sort_values(["posted_at", "id"], kind="stable").copy()
        ok = g[g["matured"]]
        g["lift_engagement"] = _lift(ok["engagement"]).reindex(g.index)
        g["lift_audience"] = _lift(ok["audience"]).reindex(g.index)
        out.append(g)
    return pd.concat(out).sort_values(["platform", "posted_at"], kind="stable").reset_index(drop=True)


def join_agent_posts(calendar_rows: list[dict[str, Any]], dataset: pd.DataFrame,
                     features: dict[tuple[str, str], dict[str, Any]] | None = None) -> pd.DataFrame:  # fmt: skip
    """Our own posts with their results. The publisher knows post_key → live_url; the warehouse knows permalink →
    metrics; the platform's post id joins them. `features` adds what we knew about the idea (pillar, angle, hook...),
    keyed by (post_key, platform). A published post the warehouse has not collected yet is kept, with no result."""
    by_key = dataset.drop_duplicates("key").set_index("key") if len(dataset) else dataset
    rows = []
    for r in calendar_rows:
        if r.get("state") != "published" or not r.get("live_url"):
            continue
        k = post_key_of(str(r["live_url"]))
        key = f"{k[0]}:{k[1]}" if k else None
        row: dict[str, Any] = {"post_key": r.get("post_key"), "platform": r.get("platform"), "live_url": r["live_url"],
                               "key": key, "slot": r.get("slot")}  # fmt: skip
        if key and len(by_key) and key in by_key.index:
            m = by_key.loc[key]
            for col in ("id", "posted_at", "engagement", "audience", "matured", "age_days", "lift_engagement",
                        "lift_audience"):  # fmt: skip
                row[col] = m[col]
            row["collected"] = True
        else:
            row["collected"] = False
        row.update((features or {}).get((str(r.get("post_key")), str(r.get("platform"))), {}))
        rows.append(row)
    return pd.DataFrame(rows)


def idea_features(state: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """What the studio knew about each idea before posting, from a run's state.json: keyed (post_key, platform)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in state.get("ideas", {}).values():
        idea = row.get("idea") or {}
        content = ((row.get("design") or {}).get("spec") or {}).get("content") or {}
        base = {"pillar": row.get("pillar"), "angle": row.get("angle"), "idea_format": idea.get("format"),
                "hook_length": len(content.get("hook") or ""), "shots": len(content.get("shots") or []),
                "music_culture": (content.get("music") or {}).get("culture"),
                "window_planned": idea.get("window")}  # fmt: skip
        for platform, caption in (row.get("captions") or {}).items():
            out[(str(idea.get("job_id")), platform)] = {**base, **{f"cap_{k}": v for k, v in _caption(caption).items()}}
    return out


def _caption(text: str) -> dict[str, Any]:
    from intel.performance.features import caption_features

    return caption_features(text)
