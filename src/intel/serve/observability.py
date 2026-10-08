"""Metrics, request ids and structured logs for intel-api (ADR 0016).

- `Metrics` owns one Prometheus registry per app (tests build many apps in one process; the global registry would
  collide). Counters and histograms are updated in-process; everything that lives in the database (ledger
  freshness, last training report, pending approvals) is read at scrape time by `StateCollector`, so a number on the
  dashboard is never older than the scrape.
- `instrument()` adds one middleware: a request id (accepted from `X-Request-ID` when well-formed, else generated,
  always returned), latency and status per route TEMPLATE (bounded label cardinality), and one JSON log line per
  request (except probes and scrapes).
- `JsonFormatter` writes one JSON object per log line, with the request id when there is one. The token-redaction
  filter in app.py still runs first.

Metric names follow Prometheus conventions: `intel_` prefix, base units (seconds), `_total` for counters.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily
from prometheus_client.registry import Collector

if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response

    from intel.serve.app import AppState

log = logging.getLogger("intel.http")
REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")
QUIET_PATHS = frozenset({"/metrics", "/health", "/health/deep"})  # scraped / probed every few seconds
WORKERS = ("inbox", "measure", "telegram", "reports")
STATE_SQL = """
select (select max(created_at) from prediction) as last_prediction,
       (select max(recorded_at) from post_outcome) as last_outcome,
       (select count(*) from ledger_post) as posts,
       (select count(*) from approval where decision is null and expires_at > now()) as pending,
       (select max(received_at) from training_run) as last_training,
       (select status from training_run order by received_at desc limit 1) as training_status
"""
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        r = self.registry
        self.requests = Counter(
            "intel_http_requests",
            "HTTP requests by route template and status",
            ["method", "route", "status"],
            registry=r,
        )
        self.latency = Histogram(
            "intel_http_request_duration_seconds",
            "HTTP request latency by route template",
            ["method", "route"],
            buckets=LATENCY_BUCKETS,
            registry=r,
        )
        self.predictions = Counter(
            "intel_predictions",
            "Predictions made, by model and source (api, try, score, shadow)",
            ["model", "source"],
            registry=r,
        )
        self.worker_last_success = Gauge(
            "intel_worker_last_success_timestamp_seconds",
            "Last time a background worker finished a loop cleanly",
            ["worker"],
            registry=r,
        )
        self.worker_errors = Counter("intel_worker_errors", "Background worker loop failures", ["worker"], registry=r)
        for w in WORKERS:  # exported from the start, so "never succeeded" is visible as 0 rather than missing
            self.worker_errors.labels(w)

    def worker_ok(self, worker: str) -> None:
        self.worker_last_success.labels(worker).set(time.time())

    def worker_failed(self, worker: str) -> None:
        self.worker_errors.labels(worker).inc()


class StateCollector(Collector):
    """Read at scrape time: build, models, and what the database knows (ledger, training, approvals)."""

    def __init__(self, state: AppState) -> None:
        self.state = state

    def collect(self) -> Iterator[Any]:
        s = self.state
        info = InfoMetricFamily("intel_build", "Running build")
        info.add_metric([], {"version": s.settings.intel_git_sha})
        yield info
        if s.started_at:
            yield GaugeMetricFamily(
                "intel_start_time_seconds", "When this process started", value=s.started_at.timestamp()
            )
        active = GaugeMetricFamily(
            "intel_model_active", "Active approved models (1 per name)", labels=["name", "version"]
        )
        for name, m in sorted(s.models.loaded.items()):
            active.add_metric([name, str(m["version"])], 1)
        yield active
        db_up = GaugeMetricFamily("intel_db_up", "1 if intel-db answered this scrape")
        try:
            row = self._db_row()
        except Exception:
            db_up.add_metric([], 0)
            yield db_up
            return
        db_up.add_metric([], 1)
        yield db_up
        for key, name, doc in (
            ("last_prediction", "intel_ledger_last_prediction_timestamp_seconds", "Newest prediction in the ledger"),
            ("last_outcome", "intel_ledger_last_outcome_timestamp_seconds", "Newest measured outcome in the ledger"),
            ("last_training", "intel_training_last_report_timestamp_seconds", "Newest weekly training report received"),
        ):
            if row[key] is not None:
                yield GaugeMetricFamily(name, doc, value=row[key].timestamp())
        yield GaugeMetricFamily("intel_ledger_posts", "Posts in the ledger", value=row["posts"])
        yield GaugeMetricFamily("intel_approvals_pending", "Approvals waiting for a decision", value=row["pending"])
        if row["training_status"]:
            st = GaugeMetricFamily(
                "intel_training_last_status", "Status of the newest training report", labels=["status"]
            )
            st.add_metric([row["training_status"]], 1)
            yield st

    def _db_row(self) -> dict[str, Any]:
        from intel.serve.db import connect, one

        with connect(self.state.dsn) as c:
            return dict(one(c.execute(STATE_SQL)))


def route_of(request: Request) -> str:
    """The route TEMPLATE (`/approvals/{approval_id}`), never the raw path: label values stay a small fixed set."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def instrument(app: FastAPI, metrics: Metrics) -> None:
    @app.middleware("http")
    async def observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _ID_RE.match(incoming) else uuid.uuid4().hex[:16]
        token = REQUEST_ID.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = route_of(request)
            metrics.requests.labels(request.method, route, str(status)).inc()
            metrics.latency.labels(request.method, route).observe(elapsed)
            if request.url.path not in QUIET_PATHS:
                log.info(
                    "request",
                    extra={
                        "method": request.method,
                        "route": route,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 1),
                    },
                )
            REQUEST_ID.reset(token)


_STD = set(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, request_id (if any), any `extra=` fields, exception."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := REQUEST_ID.get():
            out["request_id"] = rid
        out.update({k: v for k, v in vars(record).items() if k not in _STD and not k.startswith("_")})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)
