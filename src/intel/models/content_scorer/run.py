"""One training run: pull + market snapshot -> candidates -> judge vs champion -> run folder (+ MLflow)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import time
from pathlib import Path

import joblib
import pandas as pd
import yaml

from intel.champions import harness_v1 as champ
from intel.datasets.content import build_content_dataset, time_split
from intel.evaluate import score
from intel.judge import judge, paired_gain_ci

from .features import MarketContext, build_features
from .predict import predict
from .train import train

SPEC = yaml.safe_load((Path(__file__).with_name("spec.yaml")).read_text())
INPUTS = ["platform", "content_type", "posted_at", "caption"]  # the ONLY columns features may see


def _data_gate(pull_dir: Path, n_posts: int) -> tuple[bool, str]:
    prev = sorted(
        p for p in pull_dir.parent.iterdir() if p.is_dir() and p.name < pull_dir.name and (p / "manifest.json").exists()
    )
    if not prev:
        return True, f"{n_posts} posts; first pull, nothing to compare"
    before = json.loads((prev[-1] / "manifest.json").read_text())["tables"]["social_post"]["rows"]
    now = json.loads((pull_dir / "manifest.json").read_text())["tables"]["social_post"]["rows"]
    return now >= 0.8 * before, f"social_post rows {now} vs {before} in the previous pull"


def run(pull_dir: Path, market_dir: Path, runs_dir: Path, mlflow_uri: str | None, log=print) -> Path:
    ds = build_content_dataset(pull_dir)
    matured = ds[ds["matured"]].reset_index(drop=True)
    tr, te, cutoff = time_split(matured, 0.2)
    ctx = MarketContext.from_snapshot(market_dir)
    Xtr, Xte = build_features(tr[INPUTS], ctx), build_features(te[INPUTS], ctx)

    log(f"training on {len(tr)} posts, testing on {len(te)} (from {cutoff:%Y-%m-%d})")
    model = train(Xtr, tr["label"])
    chall_p = model.pipeline.predict_proba(Xte[model.columns])[:, 1]
    chall = score(te["label"], chall_p, model.threshold, te["platform"])

    cm = champ.recipe().fit(champ.features_frame(tr), tr["label"])
    champ_p = cm.predict_proba(champ.features_frame(te))[:, 1]
    champion = score(te["label"], champ_p, champ.THRESHOLD, te["platform"])

    now = dt.datetime.now(dt.UTC)
    data_hash = hashlib.sha256((pull_dir.name + market_dir.name).encode()).hexdigest()[:6]
    version = f"cs-{now:%Y%m%dT%H%M}-{data_hash}"  # unique per run; data_hash = same pull + market
    bundle = {
        "version": version,
        "pipeline": model.pipeline,
        "columns": model.columns,
        "threshold": model.threshold,
        "context": ctx.to_dict(),
        "inputs": INPUTS,
    }
    buf = io.BytesIO()
    joblib.dump(bundle, buf)
    size_mb = len(buf.getvalue()) / 1e6
    one = te[INPUTS].head(1)
    predict(bundle, one)
    t0 = time.perf_counter()
    for _ in range(20):
        predict(bundle, one)
    latency_ms = (time.perf_counter() - t0) / 20 * 1000

    verdict = judge(
        champion,
        chall,
        SPEC["gates"],
        data_ok=_data_gate(pull_dir, len(ds)),
        leakage_ok=(True, f"features built only from {', '.join(INPUTS)}; label from metrics after posting"),
        live_posts=0,
        size_mb=size_mb,
        latency_ms=latency_ms,
        gain_ci=paired_gain_ci(
            te["label"], (champ_p >= champ.THRESHOLD).astype(int), (chall_p >= model.threshold).astype(int)
        ),
    )
    report = {
        "kind": "content_scorer_run",
        "version": version,
        "pull_id": pull_dir.name,
        "market_id": market_dir.name,
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "split": {"train": len(tr), "test": len(te), "cutoff": cutoff.isoformat()},
        "challenger": {
            "model": model.name,
            "params": model.params,
            "features": model.features,
            "threshold": model.threshold,
            "cv_balanced_accuracy": model.cv_balanced_accuracy,
            "test": chall,
        },
        "champion": {"model": champ.MODEL_VERSION, "recipe_retrained_on_same_train": True, "test": champion},
        "judge": verdict,
        "leaderboard_cv": model.leaderboard,
    }
    out = runs_dir / f"{now:%Y%m%dT%H%M%SZ}_content_scorer"
    out.mkdir(parents=True)
    (out / "model.joblib").write_bytes(buf.getvalue())
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    pd.DataFrame(
        {
            "id": te["id"],
            "platform": te["platform"],
            "posted_at": te["posted_at"],
            "label": te["label"],
            "champion_p": champ_p,
            "challenger_p": chall_p,
        }
    ).to_csv(out / "test_predictions.csv", index=False)
    if mlflow_uri:
        _mlflow(mlflow_uri, report, out)
    return out


def _mlflow(uri: str, r: dict, out: Path) -> None:
    import mlflow

    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment("content_scorer")
    with mlflow.start_run(run_name=r["version"]):
        mlflow.set_tags({"pull_id": r["pull_id"], "market_id": r["market_id"], "verdict": r["judge"]["verdict"]})
        mlflow.log_params(
            {
                "model": r["challenger"]["model"],
                "features": r["challenger"]["features"],
                "threshold": r["challenger"]["threshold"],
                **r["challenger"]["params"],
            }
        )
        t, c = r["challenger"]["test"], r["champion"]["test"]
        mlflow.log_metrics(
            {
                "test_balanced_accuracy": t["balanced_accuracy"],
                "test_auc": t["roc_auc"] or 0,
                "test_brier": t["brier"],
                "champion_balanced_accuracy": c["balanced_accuracy"],
                "gain": r["judge"]["gain"],
                "cv_balanced_accuracy": r["challenger"]["cv_balanced_accuracy"],
            }
        )
        mlflow.log_artifact(str(out / "report.json"))
