"""Creator matcher: rank Berlin food creators on TikTok by fit for Dhaka Kacchi. Transparent weighted percentiles.

Public business information only (handles, public posts and their counts). Contacting creators stays with the owner;
paid or gifted posts must be labelled "Werbung" / "Anzeige".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
from pathlib import Path

import joblib
import pandas as pd
import yaml

from intel.judge import gate

SPEC_FILE = Path(__file__).with_name("spec.yaml")
SPEC = yaml.safe_load(SPEC_FILE.read_text())
TOPIC_COLS = {
    "biryani_posts": 3.0,
    "rice_dish_posts": 1.5,
    "south_asian_posts": 2.0,
    "meat_rice_posts": 1.5,
    "halal_posts": 1.5,
    "street_food_posts": 1.0,
    "review_posts": 1.0,
}


def size_band(views) -> dict:
    f = 0 if views is None or pd.isna(views) else float(views)
    return next(b for b in SPEC["size_factor"] if f < b["max"])


def is_business(r) -> bool:
    import re

    # The scraped TikTok is_business flag is True for every account (useless) and the platform's business-match score
    # exists for almost none, so only the readable word rules below decide (spec.yaml business_words).
    text = " ".join(str(r.get(k) or "") for k in ("handle", "display_name", "bio")).lower()
    words = re.findall(r"[\wäöüß]+", text)
    joined = " ".join(words)
    for w in SPEC["business_words"]:
        w = w.lower()
        if (" " in w and w in joined) or any(t == w or (len(w) >= 4 and w in t) for t in words):
            return True
    return False


def score(cr: pd.DataFrame, today: dt.date) -> pd.DataFrame:
    df = cr.copy()
    excluded = df["handle"].str.lower().isin([h.lower() for h in SPEC.get("exclude_handles", [])])
    df["business_like"] = pd.Series([is_business(r) for r in df.to_dict("records")], index=df.index) | excluded
    for c in [
        "posts_seen",
        "berlin_posts",
        "measured_posts",
        "posts_90d",
        "followers",
        "median_views",
        "median_engagement_rate",
        *TOPIC_COLS,
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["last_post"] = pd.to_datetime(df["last_post"], utc=True)
    seen = df["posts_seen"].clip(lower=1)
    df["berlin_share"] = (df["berlin_posts"] / seen).clip(upper=1)
    df["food_share"] = (df["food_posts"].fillna(0) / seen).clip(upper=1)
    df["topic_score"] = sum(w * (df[c].fillna(0) / seen) for c, w in TOPIC_COLS.items())
    df["days_since_post"] = (pd.Timestamp(today, tz="UTC") - df["last_post"]).dt.days
    df["eligible"] = (
        (df["measured_posts"].fillna(0) >= SPEC["min_measured_posts"])
        & (df["days_since_post"].fillna(9999) <= SPEC["inactive_days"])
        & ~df["business_like"]
        & (df["food_share"] >= SPEC["min_food_share"])
    )
    el = df[df["eligible"]]
    ranks = {
        "berlin_focus": el["berlin_share"].rank(pct=True),
        "topic_fit": el["topic_score"].rank(pct=True),
        "engagement": el["median_engagement_rate"].rank(pct=True),
        "activity": el["posts_90d"].fillna(0).rank(pct=True),
    }
    fit = sum(SPEC["weights"][k] * r for k, r in ranks.items())
    bands = [size_band(v) for v in df["median_views"]]  # no follower counts from TikTok: typical views instead
    df["size_band"] = [b["label"] for b in bands]
    df["size_factor"] = [b["factor"] for b in bands]
    df["fit"] = fit.reindex(df.index)
    df["score"] = (df["fit"] * df["size_factor"]).round(4)
    for k, r in ranks.items():
        df[f"rank_{k}"] = r.reindex(df.index).round(3)
    return df.sort_values("score", ascending=False, na_position="last")


def _topics(r) -> list[str]:
    names = {
        "biryani_posts": "biryani",
        "rice_dish_posts": "rice dishes",
        "south_asian_posts": "South Asian food",
        "meat_rice_posts": "Middle Eastern / Turkish food",
        "halal_posts": "halal",
        "street_food_posts": "street food",
        "review_posts": "reviews / food tours",
    }
    return [f"{names[c]} ({int(r[c])})" for c in TOPIC_COLS if r[c] and r[c] > 0]


def summarise(df: pd.DataFrame, posts: pd.DataFrame, top_n: int = 30) -> dict:
    ex = {a: g.sort_values("views", ascending=False).head(2) for a, g in posts.groupby("account_id")}

    def row(r) -> dict:
        e = ex.get(r["account_id"], pd.DataFrame())
        return {
            "handle": r["handle"],
            "name": r["display_name"] if isinstance(r["display_name"], str) else None,
            "url": f"https://www.tiktok.com/@{r['handle']}",
            "followers": None if pd.isna(r["followers"]) else int(r["followers"]),
            "size": r["size_band"],
            "score": None if pd.isna(r["score"]) else float(r["score"]),
            "berlin_share": round(float(r["berlin_share"]), 2),
            "median_views": None if pd.isna(r["median_views"]) else int(r["median_views"]),
            "engagement_rate": None
            if pd.isna(r["median_engagement_rate"])
            else round(float(r["median_engagement_rate"]), 4),
            "posts_90d": int(r["posts_90d"]) if not pd.isna(r["posts_90d"]) else 0,
            "last_post": r["last_post"].date().isoformat() if not pd.isna(r["last_post"]) else None,
            "topics": _topics(r),
            "examples": [
                {"url": x.permalink, "caption": x.caption, "views": None if pd.isna(x.views) else int(x.views)}
                for x in e.itertuples()
            ],
        }

    el = df[df["eligible"] & df["score"].notna()]
    top = [row(r) for _, r in el.head(top_n).iterrows()]
    rice = [
        row(r)
        for _, r in el[(el["biryani_posts"].fillna(0) + el["south_asian_posts"].fillna(0)) > 0].head(15).iterrows()
    ]
    biz = df[df["business_like"] & (df["days_since_post"].fillna(9999) <= SPEC["inactive_days"])]
    businesses = [
        {
            "handle": r["handle"],
            "url": f"https://www.tiktok.com/@{r['handle']}",
            "median_views": None if pd.isna(r["median_views"]) else int(r["median_views"]),
            "posts_90d": 0 if pd.isna(r["posts_90d"]) else int(r["posts_90d"]),
            "topics": _topics(r),
        }
        for _, r in biz.sort_values("median_views", ascending=False).head(25).iterrows()
    ]
    return {
        "candidates": int(len(df)),
        "eligible": int(len(el)),
        "business_like": int(df["business_like"].sum()),
        "businesses": businesses,
        "by_size": el["size_band"].value_counts().to_dict(),
        "top": top,
        "south_asian_specialists": rice,
        "weights": SPEC["weights"],
        "size_factor": SPEC["size_factor"],
    }


def run(market_dir: Path, runs_dir: Path, today: dt.date | None = None, log=print) -> Path:
    today = today or dt.datetime.now(dt.UTC).date()
    cr = pd.read_parquet(market_dir / "creators.parquet")
    posts = pd.read_parquet(market_dir / "creator_posts.parquet")
    df = score(cr, today)
    ins = summarise(df, posts)
    log(
        f"creator matcher: {ins['eligible']} eligible of {ins['candidates']}; top @{ins['top'][0]['handle'] if ins['top'] else '-'}"
    )
    now = dt.datetime.now(dt.UTC)
    version = (
        f"cr-{now:%Y%m%dT%H%M%S}-{hashlib.sha256((market_dir.name + SPEC_FILE.read_text()).encode()).hexdigest()[:6]}"
    )
    bundle = {"kind": "insights", "version": version, "insights": ins}
    buf = io.BytesIO()
    joblib.dump(bundle, buf)
    g = SPEC["gates"]
    gates = [
        gate(
            "data",
            ins["candidates"] >= g["min_candidates"],
            f"{ins['candidates']} Berlin creators, {ins['eligible']} active with ≥{SPEC['min_measured_posts']} measured posts",
        ),
        gate("works", len(buf.getvalue()) / 1e6 <= g["max_package_mb"], f"{len(buf.getvalue()) / 1e6:.2f} MB"),
    ]
    failed = [x["gate"] for x in gates if x["status"] == "fail"]
    report = {
        "kind": "creator_matcher_run",
        "version": version,
        "pull_id": "-",
        "market_id": market_dir.name,
        "created_at": now.isoformat(timespec="seconds"),
        "split": {"train": 0, "test": ins["eligible"], "cutoff": now.isoformat()},
        "judge": {"verdict": "fail" if failed else "pass", "failed": failed, "gates": gates, "gain": None},
        "challenger": {
            "model": "weighted creator fit",
            "params": SPEC["weights"],
            "features": "spec.yaml",
            "threshold": None,
            "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": ins["eligible"]},
        },
        "champion": {"model": "none", "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": 0}},
        "top_complaints": [
            f"@{c['handle']} ({c['size']}, {', '.join(c['topics'][:2]) or 'food'})" for c in ins["top"][:5]
        ],
    }
    out = runs_dir / f"{now:%Y%m%dT%H%M%SZ}_creator_matcher"
    out.mkdir(parents=True)
    (out / "model.joblib").write_bytes(buf.getvalue())
    (out / "insights.json").write_text(json.dumps(ins, indent=1, ensure_ascii=False, default=str))
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    return out
