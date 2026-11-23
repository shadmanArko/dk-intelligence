# IW: the Sunday training on the homelab server

ADR 0044 · plan `docs/SERVER_MOVE_PLAN.md` · code `deploy/server/`, `src/intel/server.py`, `weekly.wait_for_mip`.

## Measured before the server run (Mac, Docker, 2026-11-23)
| What | Result |
|---|---|
| Image | `intel-weekly`, 1.58 GB (arm64 local build; the server builds amd64 itself) |
| Preflight in the container against throwaway mip (pg16) and dk-rag (pgvector pg16) | mip, knowledge db, mip files, both key files, data dir: ok; intel-api flagged (unreachable on purpose) -> exit 75 |
| Real training in the container on a copy of the Mac's data | content_scorer trained and judged: 682 posts, balanced accuracy 0.4817 vs 0.4843, verdict FAIL (same gates as on the Mac) |
| MLflow moved | 3 experiments + 46 runs rewritten to `/data/mlruns`; without it training stops on a read-only `/Users/...` path |
| Wait step, mip `success` this week | passes; the run reached the warehouse pull (stopped there: no VPS key in the test) |
| Wait step, mip `failed` | step `wait` fails: "has not succeeded ... (status: failed)" |
| Nothing due | exits 0 at once ("not due: done this week") |

## Measured on the server
(Filled in after IW-4 and the first Sunday: duration per step, peak memory, verdicts vs the Mac's last run.)
