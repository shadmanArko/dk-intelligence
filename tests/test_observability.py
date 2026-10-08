"""ADR 0016: /metrics, /health/deep, request ids and structured logs."""

from __future__ import annotations

import json
import logging

from prometheus_client.parser import text_string_to_metric_families

from intel.serve.db import connect
from intel.serve.observability import REQUEST_ID, JsonFormatter
from tests.test_measure import Models
from tests.test_serve import BODY, client, db  # noqa: F401  (fixtures)
from tests.test_serve import pytestmark as _db_mark

pytestmark = _db_mark


def samples(c) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    text = c.get("/metrics").text
    return {
        (s.name, tuple(sorted(s.labels.items()))): s.value
        for fam in text_string_to_metric_families(text)
        for s in fam.samples
    }


def value(m: dict, metric: str, /, **labels: str) -> float | None:
    return m.get((metric, tuple(sorted(labels.items()))))


def test_metrics_expose_build_db_workers_and_models(client):  # noqa: F811
    client.app.state.intel.models.loaded["content_scorer"] = Models().m
    client.app.state.intel.metrics.worker_ok("inbox")
    m = samples(client)
    assert value(m, "intel_build_info", version="unknown") == 1
    assert value(m, "intel_db_up") == 1
    assert value(m, "intel_model_active", name="content_scorer", version="cs-t") == 1
    assert value(m, "intel_worker_errors_total", worker="measure") == 0  # exported before the first failure
    assert value(m, "intel_worker_last_success_timestamp_seconds", worker="inbox") > 1.7e9
    assert value(m, "intel_ledger_posts") == 0 and value(m, "intel_approvals_pending") == 0


def test_requests_are_counted_by_route_template_not_raw_path(client):  # noqa: F811
    client.get("/approvals/12345")
    client.get("/approvals/67890")
    client.get("/no/such/page")
    m = samples(client)
    keys = {k for k in m if k[0] == "intel_http_requests_total"}
    routes = {dict(k[1])["route"] for k in keys}
    assert "/approvals/{approval_id}" in routes and "unmatched" in routes
    assert not any("12345" in r for r in routes)  # bounded label values
    assert value(m, "intel_http_request_duration_seconds_count", method="GET", route="/approvals/{approval_id}") == 2


def test_deep_health_predicts_without_writing_to_the_ledger(client):  # noqa: F811
    r = client.get("/health/deep")
    assert r.status_code == 200 and r.json()["prediction"] is None  # no model active yet: still healthy
    client.app.state.intel.models.loaded["content_scorer"] = Models().m
    for _ in range(3):
        r = client.get("/health/deep")
    body = r.json()
    assert (
        r.status_code == 200 and body["prediction"]["model"] == "cs-t" and 0 <= body["prediction"]["probability"] <= 1
    )
    with connect(client.app.state.intel.dsn) as c:
        assert c.execute("select count(*) n from prediction").fetchone()["n"] == 0
    client.post("/v1/predict", json=BODY)
    m = samples(client)
    assert value(m, "intel_predictions_total", model="content_scorer", source="api") == 1


def test_request_id_is_echoed_when_valid_and_replaced_when_not(client):  # noqa: F811
    good = client.get("/health", headers={"X-Request-ID": "abc12345-from-caller"})
    assert good.headers["X-Request-ID"] == "abc12345-from-caller"
    bad = client.get("/health", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert bad.headers["X-Request-ID"] != "<script>alert(1)</script>" and len(bad.headers["X-Request-ID"]) == 16


def test_json_log_line_carries_request_id_and_extra_fields():
    rec = logging.LogRecord("intel.http", logging.INFO, __file__, 1, "request", None, None)
    rec.route, rec.status = "/v1/predict", 200
    token = REQUEST_ID.set("req-1234abcd")
    try:
        line = json.loads(JsonFormatter().format(rec))
    finally:
        REQUEST_ID.reset(token)
    assert line["msg"] == "request" and line["request_id"] == "req-1234abcd"
    assert line["route"] == "/v1/predict" and line["status"] == 200 and line["level"] == "INFO"


def test_token_redaction_still_applies_to_json_logs(capsys):
    from intel.serve.app import configure_logging

    configure_logging()
    logging.getLogger("intel.test").warning("calling https://api.telegram.org/bot123456789:%s/getMe", "A" * 35)
    err = capsys.readouterr().err
    assert "bot<redacted>" in err and "A" * 35 not in err
    assert json.loads(err.strip().splitlines()[-1])["level"] == "WARNING"
