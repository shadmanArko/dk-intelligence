"""Load test and latency budget for intel-api (ADR 0023).

`run(base_url, levels, requests, endpoint)` fires `requests` calls at each concurrency level (1x, 10x, 50x, ...)
with a closed loop of `concurrency` workers, and reports throughput, error rate and p50 / p95 / p99 latency.
`micro_benchmark(bundle)` splits a prediction's own time into features vs model, single and batched (the request
path only times prepare / predict / ledger, because the feature and model code is sealed).

Endpoints:
- `deep`    GET /health/deep: database + one prediction, no writes (safe against production);
- `batch`   POST /v1/score/batch with 10 drafts: every active scorer, no writes (safe against production);
- `predict` POST /v1/predict: the harness contract; WRITES each call to the prediction ledger, so it is refused
            against anything but localhost unless `allow_writes` is set.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

Endpoint = Literal["deep", "batch", "predict"]
DRAFT = {
    "platform": "instagram",
    "content_type": "reel",
    "caption": "Kacchi biryani, slow-cooked dum #berlin",
    "planned_posted_at": "2026-10-10T18:00:00+02:00",
}


@dataclass
class LevelResult:
    endpoint: str
    concurrency: int
    requests: int
    errors: int
    seconds: float
    rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 2)


def _request(endpoint: Endpoint) -> tuple[str, str, dict[str, Any] | None]:
    if endpoint == "deep":
        return "GET", "/health/deep", None
    if endpoint == "batch":
        return "POST", "/v1/score/batch", {"drafts": [DRAFT] * 10}
    return "POST", "/v1/predict", DRAFT


async def _level(client: httpx.AsyncClient, endpoint: Endpoint, concurrency: int, total: int) -> LevelResult:
    method, path, body = _request(endpoint)
    latencies: list[float] = []
    errors = 0
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(total):
        queue.put_nowait(i)

    async def worker() -> None:
        nonlocal errors
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            t0 = time.perf_counter()
            try:
                r = await client.request(method, path, json=body)
                if r.status_code != 200:
                    errors += 1
            except httpx.HTTPError:
                errors += 1
            latencies.append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    seconds = time.perf_counter() - t0
    return LevelResult(
        endpoint,
        concurrency,
        total,
        errors,
        round(seconds, 2),
        round(total / seconds, 1),
        _pct(latencies, 0.50),
        _pct(latencies, 0.95),
        _pct(latencies, 0.99),
        round(max(latencies), 2),
    )


def is_local(base_url: str) -> bool:
    return (urlsplit(base_url).hostname or "") in {"127.0.0.1", "localhost", "::1"}


def run(
    base_url: str,
    levels: tuple[int, ...] = (1, 10, 50),
    requests: int = 200,
    endpoint: Endpoint = "deep",
    allow_writes: bool = False,
) -> list[LevelResult]:
    if endpoint == "predict" and not (is_local(base_url) or allow_writes):
        raise ValueError(
            "/v1/predict writes every call to the prediction ledger: use 'deep' or 'batch' against "
            "production, or pass allow_writes=True on purpose"
        )

    async def go() -> list[LevelResult]:
        limits = httpx.Limits(max_connections=max(levels), max_keepalive_connections=max(levels))
        async with httpx.AsyncClient(base_url=base_url, timeout=30, limits=limits) as client:
            await client.request(*_request(endpoint)[:2], json=_request(endpoint)[2])  # connection + warm path
            return [await _level(client, endpoint, c, requests) for c in levels]

    return asyncio.run(go())


def micro_benchmark(bundle: dict[str, Any], n: int = 200) -> dict[str, float]:
    """Milliseconds per draft for features and model, one at a time and in a batch of 100 (no HTTP, no DB)."""
    import pandas as pd

    from intel.models.content_scorer.features import MarketContext, build_features

    ctx = MarketContext.from_dict(bundle["context"])
    row = pd.DataFrame([{**DRAFT, "posted_at": dt.datetime(2026, 10, 10, 16, tzinfo=dt.UTC)}]).drop(
        columns="planned_posted_at"
    )
    batch = pd.concat([row] * 100, ignore_index=True)

    def timed(fn: Any, reps: int) -> float:
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - t0)
        return statistics.median(samples) * 1000

    X1 = build_features(row, ctx)[bundle["columns"]]
    Xb = build_features(batch, ctx)[bundle["columns"]]
    pipe = bundle["pipeline"]
    return {
        "features_ms_single": round(timed(lambda: build_features(row, ctx), n), 3),
        "model_ms_single": round(timed(lambda: pipe.predict_proba(X1), n), 3),
        "features_ms_per_draft_batch100": round(timed(lambda: build_features(batch, ctx), 20) / 100, 4),
        "model_ms_per_draft_batch100": round(timed(lambda: pipe.predict_proba(Xb), 20) / 100, 4),
    }


def as_rows(results: list[LevelResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
