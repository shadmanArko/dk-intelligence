# Phase 3 · H9 — performance and scaling (2026-10-08)

Production model, real uvicorn + Postgres, Mac (an upper bound for the VPS). Full table and plan: [SCALING.md](../SCALING.md).

| Endpoint | 1 client p95 | 10 clients p95 | 50 clients p95 | Throughput |
|---|---|---|---|---|
| `/health/deep` | 21 ms | 209 ms | 2.25 s | 56–73 req/s |
| `/v1/predict` | 22 ms | 253 ms | 2.03 s | 54–74 req/s |
| `/v1/score/batch` (10 drafts) | 12 ms | 288 ms | 1.64 s | 76–91 calls/s ≈ 900 drafts/s |

- Budget per prediction: prepare 0.3 ms, predict 14.5 ms under load (5.7 ms alone: features 4.2 + model 1.5),
  ledger write 12.7 ms. Batched per draft: features 0.18 ms, model 0.017 ms. Cold start 46 ms (now at load time).
- First limit: one CPU-bound process (~70 req/s); fix = split the stateless API from the singleton background worker.
- 0 errors at every level. 6 new tests (CI against a real server); strict typing 24 modules.
