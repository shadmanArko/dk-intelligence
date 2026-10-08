# Scaling plan (ADR 0023)

Measured on 2026-10-08 with the production model (`cs-20261008T071453-4ea997`), a real intel-api (uvicorn) and
Postgres, on the owner's Mac (Apple Silicon; the VPS is slower per core: treat these as an upper bound and re-run
`intel loadtest` there for exact numbers).

## Where we are
| Concurrent clients | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|
| 1 | 54 req/s | 18 ms | 21 ms | 26–33 ms | 0 |
| 10 | 73 req/s | 117–133 ms | 209–253 ms | 227–361 ms | 0 |
| 50 | 68 req/s | 460–506 ms | ~2 s | 2.6–3.1 s | 0 |

(`/health/deep` and `/v1/predict`; the batch endpoint scores 10 drafts per call: 90 calls/s ≈ 900 drafts/s.)

**Latency budget of one prediction** (server-side means across the run): prepare 0.3 ms · predict 14.5 ms (under
load; 5.7 ms alone: features 4.2 ms + model 1.5 ms) · ledger write 12.7 ms. Features are 73% of the compute.
Batched per draft: features 0.18 ms (23× cheaper), model 0.017 ms (89× cheaper). Cold start of a newly loaded
model: 46 ms, paid at load time (warm-up), never by a request.

**Real traffic today:** a few requests per hour plus one probe every 30 s → ~50,000× below saturation.

## What breaks first, and what to do then
| # | Limit (measured or expected) | Trigger to act | Step |
|---|---|---|---|
| 1 | One Python process is CPU-bound at ~70 req/s (pandas / scikit-learn hold the GIL); latency then grows with the queue | p95 of `/v1/*` > 300 ms for a day (SLO burn alert), or sustained > 30 req/s | **Split roles**: `intel-api` (stateless; N uvicorn workers / replicas) and `intel-worker` (inbox, Telegram, measure, reports: exactly ONE). Not just "more workers": every process today also runs the background loops, and two Telegram pollers or two measurers would conflict and double-count. |
| 2 | Feature computation is 73% of a prediction | the same, after step 1 | Batch where callers can (the batch endpoint is 16× cheaper per draft); cache features by input hash (captions repeat across drafts) |
| 3 | The ledger write (12.7 ms) is in the request path | `/v1/predict` > ~50 req/s | Write through a queue (Redis / NATS) or in batches; accept "at most a few seconds" of loss on a crash, or keep it synchronous for audit-grade calls only |
| 4 | Postgres on the same small VPS (384 MB) | DB CPU > 70% or connections > 15 | A connection pool (PgBouncer); then a managed Postgres |
| 5 | One host | the VPS > 70% CPU most of the day, or a second service needs isolation | A second host behind a load balancer; then container orchestration (ADR 0013's trigger for Kubernetes: > 1 host) |
| 6 | Training on one Mac, in memory | the training data no longer fits in memory (~millions of posts), or training > 1 h | Out-of-core training (Polars / DuckDB), then distributed (Ray / Spark) |
| 7 | Features computed twice (Mac for training, VPS for serving) from shared sealed code | more than one consumer needs the same online features, or freshness under an hour | A feature store (Feast) |

## How to measure again
```bash
uv run intel loadtest https://<vps>.<tailnet>.ts.net --endpoint deep --levels 1,10,50   # safe: no writes
uv run intel loadtest https://<vps>.<tailnet>.ts.net --endpoint batch                   # safe: no writes
```
`--endpoint predict` writes every call to the prediction ledger and is refused against anything but localhost.
