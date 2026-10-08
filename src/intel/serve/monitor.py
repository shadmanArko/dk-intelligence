"""Live model monitoring on the VPS (ADR 0018): each active scorer against the reference profile of its version.

Hourly, after the measurer. For every active scorer it compares the most recent shadow predictions of THAT version
with the version's reference profile (built on the Mac from the training run, delivered through the inbox):

- prediction drift: PSI of the live predicted probabilities vs the test-set scores;
- feature drift: PSI per input feature, from the features logged at serving time;
- live performance on matured 7-day outcomes: balanced accuracy vs the test result, and by platform;
- calibration: mean predicted probability vs the observed share of positives (and the Brier score).

Nothing is concluded from small samples: below MIN_SAMPLES the status is "insufficient" (a few posts a week means
weeks of data, and a loud false alarm teaches people to ignore alarms). A model whose inputs drifted or whose live
results fell short sets `retrain_requested`; the Mac's hourly job reads it and trains early (with a cool-down).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sklearn.metrics import balanced_accuracy_score

from intel.monitoring.drift import P_VALUE, SIGNIFICANT, feature_drift, is_drift, level, score_drift
from intel.serve.db import connect, kv_set, log_event
from intel.serve.measure import MODEL_LABEL

MIN_SAMPLES = 30  # predictions (for drift) and matured outcomes (for performance) before any judgement
WINDOW = 100  # most recent predictions / outcomes of the version that are compared
PERF_DROP = 0.10  # live balanced accuracy this far below the test result -> degraded
CALIBRATION_GAP = 0.15  # |mean predicted probability - observed positive share| -> miscalibrated
MAX_REFERENCE_BYTES = 256_000


class Reference(BaseModel):
    """What an uploaded reference profile must contain (anything else is ignored)."""

    model_config = ConfigDict(extra="allow")
    format: int
    model_name: str = Field(pattern=r"^[a-z_]{3,40}$")
    model_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,80}$")
    features: dict[str, dict[str, Any]]
    scores: dict[str, Any]
    base_rate: float
    test: dict[str, float | int | None]


def scan_references(dsn: str, inbox: Path) -> list[str]:
    """Store every new `reference-*.json` from the inbox (validated, size-limited); returns stored versions."""
    stored = []
    for f in sorted(inbox.glob("reference-*.json")):
        try:
            data = f.read_bytes()
            if len(data) > MAX_REFERENCE_BYTES:
                raise ValueError("reference too large")
            ref = Reference.model_validate(json.loads(data))
        except (OSError, ValueError, ValidationError) as e:
            log_event(dsn, "reference_invalid", file=f.name, error=str(e)[:200])
            continue
        with connect(dsn) as c:
            cur = c.execute(
                """insert into model_reference (model_name, model_version, payload) values (%s,%s,%s)
                   on conflict (model_name, model_version) do nothing""",
                (ref.model_name, ref.model_version, json.dumps(ref.model_dump())),
            )
        if cur.rowcount:
            log_event(dsn, "reference_received", model=ref.model_name, version=ref.model_version)
            stored.append(ref.model_version)
    return stored


def _live(dsn: str, name: str, version: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any] | None]:
    label = MODEL_LABEL.get(name, "label")
    with connect(dsn) as c:
        ref = c.execute(
            "select payload from model_reference where model_name=%s and model_version=%s", (name, version)
        ).fetchone()
        preds = c.execute(
            """select post_id, platform, probability, predicted_label, features from prediction
               where source='shadow' and model_name=%s and model_version=%s order by created_at desc limit %s""",
            (name, version, WINDOW),
        ).fetchall()
        outs = c.execute(
            f"""select p.platform, p.probability, p.predicted_label, o.{label} as y from prediction p
                join post_outcome o on o.post_id = p.post_id and o.horizon = '7d'
                where p.source='shadow' and p.model_name=%s and p.model_version=%s and o.{label} is not null
                order by p.created_at desc limit %s""",  # noqa: S608 (label is from a fixed mapping)
            (name, version, WINDOW),
        ).fetchall()
    return pd.DataFrame(preds), pd.DataFrame(outs), (ref["payload"] if ref else None)


def _performance(outs: pd.DataFrame) -> dict[str, Any]:
    y, yhat, p = outs["y"].astype(int), outs["predicted_label"].astype(int), outs["probability"].astype(float)
    two = y.nunique() == 2
    out: dict[str, Any] = {
        "n": len(outs),
        "accuracy": round(float((y == yhat).mean()), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y, yhat)), 4) if two else None,
        "observed_rate": round(float(y.mean()), 4),
        "mean_probability": round(float(p.mean()), 4),
        "brier": round(float(((p - y) ** 2).mean()), 4),
    }
    out["calibration_gap"] = round(out["mean_probability"] - out["observed_rate"], 4)
    out["by_platform"] = {
        str(k): {"n": len(g), "accuracy": round(float((g["y"] == g["predicted_label"]).mean()), 4)}
        for k, g in outs.groupby("platform")
    }
    return out


def model_health(dsn: str, name: str, version: str) -> dict[str, Any]:
    preds, outs, ref = _live(dsn, name, version)
    h: dict[str, Any] = {
        "model": name,
        "version": version,
        "predictions": len(preds),
        "outcomes": len(outs),
        "reasons": [],
        "retrain_requested": False,
    }
    if ref is None:
        return h | {"status": "no_reference", "note": "no reference profile for this version yet (sent by the Mac)"}
    h["reference"] = {
        "test_balanced_accuracy": ref["test"].get("balanced_accuracy"),
        "base_rate": ref["base_rate"],
        "built_at": ref.get("built_at"),
    }
    if len(preds) >= MIN_SAMPLES:
        h["prediction_psi"] = score_drift(ref["scores"], preds["probability"].astype(float).tolist())
        logged = [f for f in preds["features"] if isinstance(f, dict)]
        if len(logged) >= MIN_SAMPLES:
            h["feature_psi"] = feature_drift(ref["features"], pd.DataFrame(logged))
        sc = ref["scores"]
        if is_drift(h["prediction_psi"], len(sc["shares"]), len(preds), int(sc.get("n") or 0)):
            h["reasons"].append(f"predicted scores shifted (PSI {h['prediction_psi']})")
        fr = ref["features"]
        drifted = sorted(
            k
            for k, v in h.get("feature_psi", {}).items()
            if is_drift(v, len(fr[k]["shares"]), len(logged), int(fr[k].get("n") or 0))
        )
        if len(drifted) >= 2:
            h["reasons"].append(f"inputs shifted: {', '.join(drifted)}")
    if len(outs) >= MIN_SAMPLES:
        perf = _performance(outs)
        h["live"] = perf
        test_ba = ref["test"].get("balanced_accuracy")
        if (
            perf["balanced_accuracy"] is not None
            and test_ba is not None
            and perf["balanced_accuracy"] < test_ba - PERF_DROP
        ):
            h["reasons"].append(f"live balanced accuracy {perf['balanced_accuracy']} vs {test_ba} at training")
        if abs(perf["calibration_gap"]) > CALIBRATION_GAP:
            h["reasons"].append(
                f"miscalibrated: predicts {perf['mean_probability']:.0%} on average, {perf['observed_rate']:.0%} happen"
            )
    enough = len(preds) >= MIN_SAMPLES or len(outs) >= MIN_SAMPLES
    perf_reasons = [r for r in h["reasons"] if r.startswith(("live", "miscalibrated"))]
    h["status"] = "insufficient" if not enough else "degraded" if perf_reasons else "drift" if h["reasons"] else "ok"
    if not enough:
        h["note"] = f"{len(preds)} predictions / {len(outs)} matured outcomes so far; judging from {MIN_SAMPLES}"
    h["retrain_requested"] = bool(h["reasons"])
    h["drift_level"] = level(h.get("prediction_psi"))
    return h


def run_monitor(dsn: str, active: dict[str, str]) -> dict[str, Any]:
    """Health of every active scorer; stored in kv 'monitoring' (the Mac reads it through /v1/monitoring)."""
    models = {name: model_health(dsn, name, version) for name, version in sorted(active.items())}
    result = {
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "models": models,
        "retrain_requested": any(m["retrain_requested"] for m in models.values()),
        "reasons": [f"{n}: {r}" for n, m in models.items() for r in m["reasons"]],
        "thresholds": {
            "min_samples": MIN_SAMPLES,
            "window": WINDOW,
            "psi_significant": SIGNIFICANT,
            "p_value": P_VALUE,
            "performance_drop": PERF_DROP,
            "calibration_gap": CALIBRATION_GAP,
        },
    }
    kv_set(dsn, "monitoring", result)
    if result["retrain_requested"]:
        log_event(dsn, "retrain_requested", reasons=result["reasons"])
    return result
