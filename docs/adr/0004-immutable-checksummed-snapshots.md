# 0004. Immutable, checksummed data snapshots

- **Status:** Accepted
- **Date:** 2026-10-03 (back-filled)

## Context
A model is only reproducible if the exact data it saw can be found again. The warehouse changes every hour.

## Decision
Every training input is a **snapshot folder**: Parquet per table + `manifest.json` (row counts, schema, source
versions, snapshot time) + `SHA256SUMS`.
- Read in one `REPEATABLE READ`, read-only transaction, so all tables are from the same instant.
- Written to `<id>.partial` and renamed only after verification (a crash never leaves a half snapshot).
- Never modified; the newest N are kept (`INTEL_KEEP_PULLS`).
- Every run report and package names the snapshot ids it used.

## Consequences
- Good: "which data trained model X?" is one lookup; any snapshot can be re-verified (`intel pulls verify`).
- Cost: disk (small today: ~MBs per pull).

## Alternatives considered
- Train straight from live tables: not reproducible.
- DVC / lakeFS: the same guarantees with more moving parts than this data size needs (ADR 0013).
