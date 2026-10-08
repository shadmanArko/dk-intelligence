"""ADR 0023: load test against a real running intel-api (uvicorn, Postgres, a loaded model), the latency budget by
stage, batch scoring, warm-up, and the production-safety rule of the load-test tool. Latency limits here are
generous (shared CI machines); the measured numbers live in docs/results/P3_H9_performance.md."""

from __future__ import annotations

import pytest

from intel.loadtest import is_local, micro_benchmark, run
from tests.test_deploy_script import live_api  # noqa: F401  (fixture: real uvicorn + Postgres)
from tests.test_measure import Models

CI_P95_LIMIT_MS = 1500


@pytest.fixture
def api(live_api):  # noqa: F811
    url, app = live_api
    app.state.intel.models.loaded["content_scorer"] = Models().m
    return url, app


def test_concurrency_levels_without_errors(api):
    url, _ = api
    for endpoint in ("deep", "batch"):
        results = run(url, levels=(1, 10, 25), requests=60, endpoint=endpoint)
        for r in results:
            assert r.errors == 0, r
            assert r.p95_ms < CI_P95_LIMIT_MS, r
        assert results[1].rps >= results[0].rps * 0.8  # concurrency must not make the service slower in total


def test_predict_writes_one_ledger_row_per_call_and_the_others_none(api):
    from intel.serve.db import connect

    url, app = api
    with connect(app.state.intel.dsn) as c:
        before = c.execute("select count(*) n from prediction").fetchone()["n"]
    run(url, levels=(5,), requests=40, endpoint="deep")
    run(url, levels=(5,), requests=40, endpoint="batch")
    (r,) = run(url, levels=(5,), requests=40, endpoint="predict")
    with connect(app.state.intel.dsn) as c:
        after = c.execute("select count(*) n from prediction where source='api'").fetchone()["n"]
    assert r.errors == 0 and after - before == 41  # 40 + the warm-up call; deep and batch wrote nothing


def test_the_tool_refuses_to_write_to_production():
    assert is_local("http://127.0.0.1:8710") and not is_local("https://vps.example.ts.net")
    with pytest.raises(ValueError, match="writes every call"):
        run("https://vps.example.ts.net", endpoint="predict")


def test_latency_budget_metrics_and_batch_is_cheaper_per_draft(api):
    import httpx

    url, _ = api
    httpx.post(
        f"{url}/v1/predict",
        json={
            "platform": "instagram",
            "content_type": "reel",
            "caption": "x",
            "planned_posted_at": "2026-10-10T18:00:00+02:00",
        },
    )
    metrics = httpx.get(f"{url}/metrics").text
    for stage in ("prepare", "predict", "ledger"):
        assert f'intel_predict_stage_seconds_count{{stage="{stage}"}}' in metrics
    bench = micro_benchmark(Models().m["bundle"], n=30)
    assert bench["model_ms_per_draft_batch100"] < bench["model_ms_single"]
    assert bench["features_ms_per_draft_batch100"] < bench["features_ms_single"]


def test_batch_validates_and_limits_size(api):
    import httpx

    url, _ = api
    d = {
        "platform": "instagram",
        "content_type": "reel",
        "caption": "x",
        "planned_posted_at": "2026-10-10T18:00:00+02:00",
    }
    ok = httpx.post(f"{url}/v1/score/batch", json={"drafts": [d] * 3}).json()
    assert ok["drafts"] == 3 and len(ok["models"]["content_scorer"]) == 3
    assert httpx.post(f"{url}/v1/score/batch", json={"drafts": [d] * 101}).status_code == 422
    naive = {**d, "planned_posted_at": "2026-10-10T18:00:00"}
    assert httpx.post(f"{url}/v1/score/batch", json={"drafts": [naive]}).status_code == 400


def test_warm_up_measures_the_cold_start():
    from intel.serve.app import warm_up

    seconds = warm_up(Models().m["bundle"])
    assert seconds is not None and seconds > 0
    assert warm_up({"kind": "insights"}) is None
