# Postmortem: the data platform's CI was red for four days, unnoticed

- **Date:** 2026-10-04 → 2026-10-08 · **Impact:** every push to the data platform failed CI; no deploys depend on
  it, but tests and migrations were unverified for 4 days · **Severity:** ticket · **Status:** final

## Summary
Migration 0010 granted rights on schemas that only dbt creates; a fresh CI database did not have them. Then a
duplicate dbt schema entry broke `dbt parse`. Nobody looked at CI, and CI told nobody.

## Root cause
Two latent defects that only a *fresh* environment reveals, plus a CI with no alerting. The Mac's long-lived
database hid both.

## Detection
Found while setting up the VPS collector (an unrelated task) on day 4.

## Actions
| Action | Type | Status |
|---|---|---|
| `migrate()` creates the dbt schemas first; one schema entry per model | prevent | done |
| Red CI on `main` → Telegram, in both repos (H1), and the alert path tested | detect | done |
| Fresh-database migrations in CI (the CI database is always new) | prevent | already true, now meaningful |
