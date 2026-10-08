# Phase 3 · H2 — data quality for ML (2026-10-08)

## What exists now
| | What | Where |
|---|---|---|
| Contracts | 17 consumed tables (5 warehouse, 12 market): structure (Arrow types), values (pandera), volume, freshness, calendar horizon, cross-table links, no future timestamps | `src/intel/quality/contracts.py` |
| Validator + data card | findings (error / warning, with example rows) and a per-table card: rows, change vs previous, freshness, per-column nulls / distinct / range / top values | `src/intel/quality/validate.py` → `data/quality/<source>-<id>.json` |
| Gates in the Sunday run | warehouse: a broken contract stops the run at `pull`; market: fall back to the newest earlier snapshot that passes, else stop at `market` | `intel/weekly.py` |
| Reporting | data-check summary in the run report → stored on the VPS; Telegram mentions warnings; progress page shows counts and the first findings | `serve/runs.py` (`DataQuality`) |
| By hand | `uv run intel quality [--pull ID] [--market ID]` | `intel/cli.py` |

## Calibration on real data (before enforcing)
| Snapshot | Tables | Errors | Warnings |
|---|---|---|---|
| warehouse `20261008T072013Z` | 5 | 0 | 0 |
| market `20261008T072031Z` (first run) | 12 | **1** | 0 |
| market, after making the rule precise | 12 | 0 | 0 |

**The one error was real:** `afs_citizenship` mixes countries with subtotals ("Europa zusammen", "Asien zusammen",
"Zusammen" twice, "Insgesamt"). Summing `residents` gives 3.49 M, about 3.6× the real 0.97 M foreign residents. The
occasion planner looks countries up by name, so it was unaffected, but any future "total audience" would have been
3.6× too large. The contract now states the rule the consumer relies on (country names unique; subtotals recognised),
still catches a duplicated country, and an upstream fix (an `is_aggregate` flag in the data platform) is queued.

## Near miss caught by a new test
The VPS imports `intel.weekly` (for the training-overview page) but its image has no `pandera` (training-only
dependency). A top-level import of the quality modules would have crashed that page on the server.
`tests/test_server_imports.py` imports every serving module with the training-only packages blocked; it fails on the
bad version (checked) and passes on the fix (type-only imports).

## Tests
174 (+14): one per failure class on generated snapshots (missing column, wrong type, duplicate key, negative count,
orphan rows, future timestamp, stale metrics as warning vs error, volume drop, calendar horizon, unparseable rating,
duplicate country vs subtotals), the three run gates (warehouse stop, market fallback, market stop), the run report on
the VPS, and the server-import guard. Coverage 78.8% (floor raised to 77%); strict typing on 17 modules.
