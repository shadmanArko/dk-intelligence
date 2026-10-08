"""ADR 0018: drift math, reference profiles, the VPS monitor, the skew check and the drift trigger."""

from __future__ import annotations

import datetime as dt
import json
import uuid

import httpx
import numpy as np
import pandas as pd
import pytest

from intel.monitoring.drift import feature_drift, level, profile, psi, score_drift, score_profile
from tests.test_measure import intel, warehouse  # noqa: F401  (fixtures)
from tests.test_serve import client, db  # noqa: F401  (fixtures)

# ---------- drift math ----------


def test_psi_is_zero_for_the_same_distribution_and_large_for_a_shift():
    assert psi([0.5, 0.5], [0.5, 0.5]) == 0
    assert psi([0.9, 0.1], [0.1, 0.9]) > 1
    assert level(0.05) == "stable" and level(0.2) == "moderate" and level(0.4) == "significant"


def test_profiles_cover_numeric_categorical_unseen_and_missing():
    num = profile(pd.Series(list(range(100)) + [None], dtype="float"))
    assert num["kind"] == "numeric" and len(num["shares"]) == len(num["edges"]) + 2  # bins + missing
    assert num["shares"][-1] == pytest.approx(1 / 101)
    cat = profile(pd.Series(["facebook"] * 64 + ["instagram"] * 36))
    assert cat["categories"] == ["facebook", "instagram"]
    live = pd.DataFrame({"platform": ["threads"] * 50, "n": list(range(50))})
    drift = feature_drift({"platform": cat, "n": profile(pd.Series(range(50)))}, live)
    assert drift["platform"] > 1 and drift["n"] < 0.01  # an unseen platform is maximal drift; same numbers none
    bools = profile(pd.Series([True, False] * 10))
    assert bools["kind"] == "categorical" and set(bools["categories"]) == {"True", "False"}


def test_small_samples_need_a_real_shift_not_just_a_noisy_psi():
    from intel.monitoring.drift import drift_p_value, is_drift

    rng = np.random.default_rng(7)
    ref = profile(pd.Series(rng.integers(20, 300, 300)))
    same = pd.DataFrame({"x": rng.integers(20, 300, 40)})  # same distribution, small sample
    noisy = feature_drift({"x": ref}, same)["x"]
    k = len(ref["shares"])
    assert noisy > 0.1  # PSI alone looks alarming on 40 values ...
    assert not is_drift(noisy, k, 40, 300)  # ... but chance explains it
    moved = pd.DataFrame({"x": rng.integers(400, 600, 40)})  # genuinely longer captions
    real = feature_drift({"x": ref}, moved)["x"]
    assert is_drift(real, k, 40, 300) and drift_p_value(real, k, 40, 300) < 1e-6


def test_score_drift():
    ref = score_profile([0.5, 0.55, 0.45, 0.52] * 25)
    assert score_drift(ref, [0.5, 0.55, 0.45, 0.52] * 10) == 0
    assert score_drift(ref, [0.9] * 40) > 1


# ---------- the VPS monitor ----------

pytestmark_db = pytest.mark.skipif(
    not __import__("os").environ.get("INTEL_TEST_PG_DSN"), reason="INTEL_TEST_PG_DSN not set"
)


def _reference(version="cs-t"):
    rng = np.random.default_rng(0)
    train = pd.DataFrame(
        {
            "platform": ["facebook"] * 64 + ["instagram"] * 36,
            "hour_bucket": (["morning", "afternoon", "evening"] * 34)[:100],
            "caption_length": rng.integers(20, 300, 100),
        }
    )
    from intel.monitoring.reference import build_reference

    return build_reference(
        model_name="content_scorer",
        model_version=version,
        X_train=train,
        y_train=pd.Series([0, 1] * 50),
        test_scores=pd.Series(rng.uniform(0.4, 0.6, 75)),
        test_labels=pd.Series([0, 1] * 37 + [1]),
        threshold=0.5,
        test_metrics={"balanced_accuracy": 0.7, "roc_auc": 0.75, "brier": 0.22, "n": 75},
        data={"pull_id": "p", "market_id": "m", "run": "r"},
    )


def _history(dsn, n, *, version="cs-t", shift=False, wrong=False, overconfident=False, outcomes=True):
    """n scored posts with logged features and (optionally) 7-day outcomes."""
    from intel.serve.db import connect

    rng = np.random.default_rng(1)
    now = dt.datetime.now(dt.UTC)
    with connect(dsn) as c:
        for i in range(n):
            pid, at = uuid.uuid4(), now - dt.timedelta(days=n - i)
            y = i % 2
            features = {
                "platform": "threads" if shift else ("facebook" if i % 3 else "instagram"),
                "hour_bucket": "night" if shift else ["morning", "afternoon", "evening"][i % 3],
                "caption_length": int(rng.integers(20, 300)),
            }
            p = 0.95 if overconfident else (0.55 if y else 0.45)  # inside the reference's 0.4-0.6 test scores
            label = (1 - y) if wrong else y
            c.execute(
                "insert into ledger_post (post_id, platform, posted_at, content_type, caption_sha256, caption_length)"
                " values (%s,'facebook',%s,'reel','x',10)",
                (pid, at),
            )
            c.execute(
                """insert into prediction (source, model_name, model_version, post_id, platform, planned_at,
                   input_sha256, probability, threshold, predicted_label, features, created_at)
                   values ('shadow','content_scorer',%s,%s,'facebook',%s,'h',%s,0.5,%s,%s,%s)""",
                (version, pid, at, p, int(p >= 0.5), json.dumps(features), at),
            )
            if outcomes:
                c.execute(
                    """insert into post_outcome (post_id, horizon, captured_at, age_hours, engagement, label)
                       values (%s,'7d',%s,170,10,%s)""",
                    (pid, at + dt.timedelta(days=7), label),
                )


def _with_reference(dsn, ref=None):
    from intel.serve.db import connect

    ref = ref or _reference()
    with connect(dsn) as c:
        c.execute(
            "insert into model_reference (model_name, model_version, payload) values (%s,%s,%s)",
            (ref["model_name"], ref["model_version"], json.dumps(ref)),
        )


@pytestmark_db
def test_monitor_needs_a_reference_and_enough_data(intel):  # noqa: F811
    from intel.serve.monitor import model_health

    assert model_health(intel, "content_scorer", "cs-t")["status"] == "no_reference"
    _with_reference(intel)
    _history(intel, 10)
    h = model_health(intel, "content_scorer", "cs-t")
    assert h["status"] == "insufficient" and not h["retrain_requested"] and "judging from 30" in h["note"]


@pytestmark_db
def test_monitor_ok_on_the_training_distribution(intel):  # noqa: F811
    from intel.serve.monitor import model_health

    _with_reference(intel)
    _history(intel, 60)
    h = model_health(intel, "content_scorer", "cs-t")
    assert h["status"] == "ok", h
    assert h["live"]["balanced_accuracy"] == 1.0 and h["live"]["n"] == 60
    assert set(h["feature_psi"]) == {"platform", "hour_bucket", "caption_length"}


@pytestmark_db
def test_monitor_flags_drifted_inputs(intel):  # noqa: F811
    from intel.serve.monitor import model_health

    _with_reference(intel)
    _history(intel, 40, shift=True, outcomes=False)
    h = model_health(intel, "content_scorer", "cs-t")
    assert h["status"] == "drift" and h["retrain_requested"]
    assert any(r.startswith("inputs shifted: hour_bucket, platform") for r in h["reasons"]), h["reasons"]


@pytestmark_db
def test_monitor_flags_worse_live_results_and_miscalibration(intel):  # noqa: F811
    from intel.serve.monitor import model_health

    _with_reference(intel)
    _history(intel, 40, wrong=True)
    h = model_health(intel, "content_scorer", "cs-t")
    assert h["status"] == "degraded" and any(r.startswith("live balanced accuracy 0.0 vs 0.7") for r in h["reasons"])

    _history(intel, 40, version="cs-over", overconfident=True)
    _with_reference(intel, _reference("cs-over"))
    h2 = model_health(intel, "content_scorer", "cs-over")
    assert any(r.startswith("miscalibrated: predicts 95% on average, 50% happen") for r in h2["reasons"]), h2


@pytestmark_db
def test_references_from_the_inbox_are_validated_and_stored_once(intel, tmp_path):  # noqa: F811
    from intel.serve.db import connect
    from intel.serve.monitor import scan_references

    (tmp_path / "reference-content_scorer-cs-t.json").write_text(json.dumps(_reference()))
    (tmp_path / "reference-bad.json").write_text(json.dumps({"format": 1, "model_name": "x"}))
    assert scan_references(intel, tmp_path) == ["cs-t"]
    assert scan_references(intel, tmp_path) == []  # stored once
    with connect(intel) as c:
        kinds = [r["kind"] for r in c.execute("select kind from event_log order by id")]
    assert kinds.count("reference_received") == 1 and "reference_invalid" in kinds


@pytestmark_db
def test_monitoring_endpoint_and_metrics(client):  # noqa: F811
    from prometheus_client.parser import text_string_to_metric_families

    from tests.test_measure import Models

    st = client.app.state.intel
    st.models.loaded["content_scorer"] = Models().m  # version cs-t
    _with_reference(st.dsn)
    _history(st.dsn, 40, shift=True, outcomes=False)
    st.monitoring = None
    body = client.get("/v1/monitoring").json()
    assert body["retrain_requested"] is True and body["models"]["content_scorer"]["status"] == "drift"
    names = {
        s.name: s.value
        for f in text_string_to_metric_families(client.get("/metrics").text)
        for s in f.samples
        if s.labels.get("model") == "content_scorer" and s.name == "intel_model_retrain_requested"
    }
    assert names == {"intel_model_retrain_requested": 1}


# ---------- Mac side: skew, references, the drift trigger ----------


def test_drift_trigger_respects_the_cool_down(tmp_path):
    from intel.weekly import drift_due

    def api(payload):
        return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))

    w = tmp_path / "weekly"
    w.mkdir()
    now = dt.datetime(2026, 10, 14, 12, tzinfo=dt.UTC)
    assert drift_due(None, w, now)[0] is False
    assert drift_due("http://x", w, now, api({"retrain_requested": False}))[0] is False
    ask = {"retrain_requested": True, "reasons": ["content_scorer: inputs shifted: platform"]}
    due, why = drift_due("http://x", w, now, api(ask))
    assert due and "inputs shifted" in why
    (w / "weekly-20261013T100000Z.run.json").write_text(
        json.dumps(
            {"status": "fail", "started_at": "2026-10-13T10:00:00+00:00", "finished_at": "2026-10-13T10:30:00+00:00"}
        )
    )
    due, why = drift_due("http://x", w, now, api(ask))
    assert not due and "cool-down" in why
    down = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("no"))))
    assert drift_due("http://x", w, now, down) == (False, "monitoring not reachable (ConnectError)")


def _scorer_run(runs, version, pull_id, market_id, bundle):
    import joblib

    rd = runs / f"20261008T000000Z_content_scorer_{version}"
    rd.mkdir(parents=True)
    joblib.dump(bundle, rd / "model.joblib")
    (rd / "report.json").write_text(
        json.dumps({"kind": "content_scorer_run", "version": version, "pull_id": pull_id, "market_id": market_id})
    )
    return rd


def test_skew_check_finds_served_features_that_training_would_not_compute(tmp_path):
    from intel.weekly import skew_check
    from tests.quality_fixtures import warehouse_tables, write_snapshot
    from tests.test_measure import Models

    pull = write_snapshot(tmp_path / "pulls", "p1", warehouse_tables())
    bundle = Models().m["bundle"]
    _scorer_run(tmp_path / "runs", "cs-t", "p1", "m1", bundle)
    from intel.serve.measure import served_features

    posts = pd.read_parquet(pull / "social_post.parquet")
    rows = []
    for _, p in posts.head(5).iterrows():
        row = pd.DataFrame([{k: p[k] for k in ("platform", "content_type", "caption", "posted_at")}])
        rows.append(
            {
                "post_id": p["id"],
                "model_name": "content_scorer",
                "model_version": "cs-t",
                "features": served_features(bundle, row),
            }
        )
    assert skew_check(rows, pull, tmp_path / "runs") == {"checked": 5, "mismatches": 0, "skipped": 0, "examples": []}
    rows[0]["features"]["caption_length"] += 3  # served something training would not compute
    r = skew_check(rows, pull, tmp_path / "runs")
    assert r["mismatches"] == 1 and "caption_length: served" in r["examples"][0]


def test_references_are_sent_for_new_and_serving_versions_once(tmp_path):
    from intel.weekly import mark_sent, references_to_send

    d = tmp_path
    for v in ("cs-new", "cs-live"):
        rd = _scorer_run(d / "runs", v, "p1", "m1", {})
        (rd / "reference.json").write_text(json.dumps({"model_version": v}))
    new = [r for r in (d / "runs").iterdir() if r.name.endswith("cs-new")]
    ledger = [
        {"model_name": "content_scorer", "model_version": "cs-live"},
        {"model_name": "harness_predictor", "model_version": "2026-09-23"},
    ]
    files = references_to_send(ledger, new, d, print)
    assert sorted(f.name for f in files) == [
        "reference-content_scorer-cs-live.json",
        "reference-content_scorer-cs-new.json",
    ]
    mark_sent(d, files)
    assert references_to_send(ledger, new, d, print) == []  # never twice
    rd = _scorer_run(d / "runs", "cs-loaded", "p1", "m1", {})
    (rd / "reference.json").write_text(json.dumps({"model_version": "cs-loaded"}))
    # loaded on the VPS but not in the ledger yet (no new post scored since its approval)
    assert [f.name for f in references_to_send(ledger, [], d, print, {"cs-loaded"})] == [
        "reference-content_scorer-cs-loaded.json"
    ]


@pytestmark_db
def test_shadow_scoring_logs_the_served_features(intel, warehouse):  # noqa: F811
    from intel.serve.db import connect
    from intel.serve.measure import Measurer
    from tests.test_measure import START, Models, harness

    wh, ids = warehouse
    Measurer(intel, wh, START, Models(), "http://predictor:8000", http=harness()).run_once()
    with connect(intel) as c:
        rows = {
            r["model_name"]: r["features"]
            for r in c.execute("select model_name, features from prediction where post_id=%s", (ids["fb"],))
        }
    cols = Models().m["bundle"]["columns"]
    assert set(rows["content_scorer"]) == set(cols)  # exactly the model's inputs
    assert rows["content_scorer"]["platform"] == "facebook" and rows["harness_predictor"] is None
