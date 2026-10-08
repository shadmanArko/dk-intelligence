# 0023. Performance: a measured latency budget, batching, warm-up, and a trigger-based scaling plan

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
"How would this scale?" had no measured answer: no load test, no idea where a prediction's time goes, and a first
request after each model load that paid the cold start.

## Decision
1. **Stage timing in the request path** (`intel_predict_stage_seconds{stage}`: prepare, predict, ledger), and a
   micro-benchmark for the split inside `predict` (features vs model), because that code is sealed (ADR 0011).
2. **Warm-up at load time:** every newly loaded scorer makes one prediction immediately
   (`intel_model_warmup_seconds`), so no user or probe pays the cold start.
3. **Batch endpoint** `POST /v1/score/batch` (≤ 100 drafts, every active scorer, vectorised, not written to the
   ledger): 23× cheaper features and 89× cheaper model per draft than one at a time.
4. **Load-test tool** (`intel loadtest`): closed-loop concurrency levels, p50/p95/p99, errors; safe by default
   (`deep` / `batch` write nothing; `predict` writes the ledger and is refused against non-local hosts).
5. **A test in CI** runs the tool against a real uvicorn + Postgres + model: no errors at any level, the ledger
   gets exactly one row per `/v1/predict`, batching is cheaper per draft (generous latency limits; shared runners).
6. **`docs/SCALING.md`**: measured capacity, the limit that breaks first, and a trigger metric for every heavier step.

## Consequences
- Measured: ~70 req/s per process, p95 21 ms (1 client) / ~230 ms (10) / ~2 s (50); today's traffic is ~50,000×
  below that. The first limit is the single CPU-bound process.
- The scaling step is NOT "more uvicorn workers": each process also runs the background loops (Telegram polling,
  measurement), which must run exactly once. Step 1 is splitting the stateless API from a singleton worker.

## Alternatives considered
- k6 / Locust: standard and fine; a 150-line async client in the repo needs no extra runtime and runs inside the
  existing pytest job with a real server.
