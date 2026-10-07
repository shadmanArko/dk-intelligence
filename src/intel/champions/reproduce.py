"""M2: reproduce the champion and set the benchmark the challenger must beat.

A. Recipe check: the original data + split from the harness repo, our copy of the recipe -> must give the
   published test balanced accuracy (0.6179). Proves the copy is exact.
B. Production artifact on posts it never saw (published after its training data ended), matured only.
C. Benchmark: the recipe retrained on today's pull, tested on the newest 20% of matured posts. This is the number
   M3's challenger is judged against (same split, same scoring code).
Also reported: label drift between the harness extract (2026-09-23) and today's metrics for the same posts.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from intel.champions import harness_v1 as champ
from intel.datasets.content import build_content_dataset, time_split
from intel.evaluate import score

PUBLISHED_TEST_BALANCED_ACCURACY = 0.6179


def _harness_original(harness_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    ml = harness_dir / "ml"
    full = pd.read_csv(ml / "01-data/artifacts/dataset.csv")
    full["posted_at"] = pd.to_datetime(full["posted_at"], utc=True)
    train_ids = set(pd.read_csv(ml / "02-split/artifacts/train_ids.csv")["id"])
    test_ids = set(pd.read_csv(ml / "02-split/artifacts/test_ids.csv")["id"])
    train = full[full["id"].isin(train_ids)].sort_values("posted_at").reset_index(drop=True)
    test = full[full["id"].isin(test_ids)].sort_values("posted_at").reset_index(drop=True)
    return train, test


def recipe_check(harness_dir: Path) -> dict:
    train, test = _harness_original(harness_dir)
    model = champ.recipe().fit(champ.features_frame(train), train["label"])
    proba = model.predict_proba(champ.features_frame(test))[:, 1]
    s = score(test["label"], proba, champ.THRESHOLD, test["platform"])
    s["published_balanced_accuracy"] = PUBLISHED_TEST_BALANCED_ACCURACY
    s["reproduced_exactly"] = s["balanced_accuracy"] == PUBLISHED_TEST_BALANCED_ACCURACY
    return s


def label_drift(harness_dir: Path, ds: pd.DataFrame) -> dict:
    train, test = _harness_original(harness_dir)
    # Post ids differ between the harness's local extract and the VPS warehouse (uuids are generated per
    # database), so match on (platform, posted_at), which is unique per account.
    key = ["platform", "posted_at"]
    old = pd.concat([train, test])[[*key, "label", "engagement"]]
    m = old.merge(ds[[*key, "label", "engagement"]], on=key, suffixes=("_then", "_now"))
    return {
        "posts_then": int(len(old)),
        "posts_compared": int(len(m)),
        "label_agreement": round(float((m["label_then"] == m["label_now"]).mean()), 4),
        "engagement_grew_for": int((m["engagement_now"] > m["engagement_then"]).sum()),
    }


def production_on_unseen(harness_dir: Path, ds: pd.DataFrame) -> dict:
    model, meta = champ.load_production(harness_dir)
    train, test = _harness_original(harness_dir)
    trained_until = max(train["posted_at"].max(), test["posted_at"].max())
    unseen = ds[(ds["posted_at"] > trained_until) & ds["matured"]].reset_index(drop=True)
    unseen_all = ds[ds["posted_at"] > trained_until]
    out = {
        "model_version": meta["model_version"],
        "trained_on_posts_until": trained_until.isoformat(),
        "unseen_posts": int(len(unseen_all)),
        "unseen_matured_posts": int(len(unseen)),
    }
    if len(unseen):
        proba = model.predict_proba(champ.features_frame(unseen)[meta["feature_columns"]])[:, 1]
        out["score"] = score(unseen["label"], proba, meta["threshold"], unseen["platform"])
    return out


def benchmark(ds: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    matured = ds[ds["matured"]].reset_index(drop=True)
    train, test, cutoff = time_split(matured, 0.2)
    model = champ.recipe().fit(champ.features_frame(train), train["label"])
    proba = model.predict_proba(champ.features_frame(test))[:, 1]
    split = pd.concat([train.assign(split="train"), test.assign(split="test")])[
        ["id", "platform", "posted_at", "split"]
    ]
    return {
        "matured_posts": int(len(matured)),
        "train": int(len(train)),
        "test": int(len(test)),
        "cutoff": cutoff.isoformat(),
        "train_period": [train["posted_at"].min().isoformat(), train["posted_at"].max().isoformat()],
        "score": score(test["label"], proba, champ.THRESHOLD, test["platform"]),
    }, split


def run(pull_dir: Path, harness_dir: Path, runs_dir: Path) -> Path:
    ds = build_content_dataset(pull_dir)
    bench, split = benchmark(ds)
    report = {
        "kind": "champion_reproduction",
        "champion": champ.MODEL_VERSION,
        "pull_id": pull_dir.name,
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dataset": {
            "posts": int(len(ds)),
            "by_platform": {k: int(v) for k, v in ds["platform"].value_counts().sort_index().items()},
            "matured": int(ds["matured"].sum()),
            "positive_rate": round(float(ds["label"].mean()), 4),
        },
        "A_recipe_check": recipe_check(harness_dir),
        "B_production_on_unseen_posts": production_on_unseen(harness_dir, ds),
        "C_benchmark_for_challenger": bench,
        "label_drift_since_2026_09_23": label_drift(harness_dir, ds),
    }
    out = runs_dir / f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}_champion_{champ.NAME}"
    out.mkdir(parents=True)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    split.to_csv(out / "benchmark_split.csv", index=False)
    return out
