# 0017. Data contracts for every table a model consumes

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
The Sunday run trained on whatever the pull returned. The only data check was "social posts ≥ 80% of last week"
inside one model. A renamed column, negative counts, a metrics job that silently stopped, a half-empty market
snapshot or a calendar that no longer reaches into the future would all reach the models as features or labels, and
show up (if at all) as a quietly worse model weeks later. Upstream (`mip`) has its own dbt tests, but they protect the
platform's tables, not what *these* models assume.

## Decision
1. **Consumer-driven contracts** (`intel/quality/contracts.py`) for the 17 tables the models read (5 warehouse, 12
   market), covering only the columns they read:
   - **structure** checked on the manifest's Arrow types (stable; pandas dtypes change when a column has nulls);
   - **values** with pandera, all failures collected at once (lazy): keys unique, required fields present, counts
     ≥ 0, ratings parse to [0, 1], allowed values, coordinates inside Berlin and its delivery ring;
   - **volume** vs the previous snapshot (warehouse: > 20% fewer rows is an error; market: > 50%);
   - **freshness** of the newest row at snapshot time, with separate warn / error thresholds (posting pauses only
     warn; metrics 7 days stale block);
   - **horizon** for calendars (the occasion calendar must reach 60 days ahead);
   - **links** between tables (metrics → posts is an error; reviews → listings a warning);
   - no event timestamp after the snapshot itself.
2. **Gates in the run:** a warehouse pull that breaks a contract stops the run at `pull` with the reasons listed;
   no model sees it. A market snapshot that fails falls back to the newest earlier snapshot that passes (as for an
   unreachable platform), otherwise the run stops at `market`. Warnings travel in the run report (Telegram shows
   them); the full report and **data card** (rows, change vs previous, freshness, per-column nulls, distinct values,
   ranges, top values) are written to `data/quality/` next to (never inside) the immutable snapshot.
3. **pandera only in the training environment** (`train` group). The VPS imports `intel.weekly`, so the quality
   modules are imported lazily, and `tests/test_server_imports.py` imports all serving code with the training-only
   packages blocked.
4. **Calibrated on real data before enforcing:** the contracts were run on the latest real pull and market snapshot
   first; a gate that blocks good data every Sunday is worse than none.

## Consequences
- Good: bad data stops at the door, with a message that names the table, the check and example rows.
- Good: the first calibration found a real trap: the citizenship table mixes countries with subtotals ("Europa
  zusammen", "Zusammen" twice, "Insgesamt"), so summing it counts people ~3.6×. The contract now states the rule the
  planner relies on (country names unique), and an upstream fix is queued in the data platform.
- Cost: a contract must be updated when a model starts reading a new column (the check fails loudly if it doesn't).

## Alternatives considered
- Great Expectations: heavier, more configuration; pandera keeps contracts as typed Python next to the code.
- Relying on upstream dbt tests only: they cannot know what downstream models assume.
