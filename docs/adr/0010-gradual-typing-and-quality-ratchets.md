# 0010. Gradual typing and quality ratchets

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
Baseline on 2026-10-08: mypy found **200** errors in the default mode (432 under `--strict`); 72% line coverage; no
type checking or coverage gate in CI. Two of the errors pointed at real hazards: optional settings used as if always
set, and SQL rows assumed to exist.

## Decision
- **Ratchet, not big bang.** The whole package passes mypy's default checks; a growing list of modules (the MLOps
  core: plug-ins, pipeline, settings, judge, evaluate, package, pulls, weekly run, DB access) passes the full
  `--strict` flag set. A module joins the strict list when it is next touched.
- **Fail fast on configuration:** `required(value, "ENV_NAME")` raises a named error at the point of use; the API
  validates its database setting at construction, not inside a background thread.
- **`one(cursor)`** states "this query returns exactly one row" and raises if it does not.
- **Typed shared state:** the API's untyped `state` dict became an `AppState` dataclass.
- **Coverage floor** in `pyproject.toml` (branch coverage, compared to 2 decimals, measured in CI), raised whenever
  coverage rises; never lowered.
- **Same checks everywhere:** pre-commit hooks (ruff, mypy, file hygiene, secret scan of staged changes, sealed-code
  lock) run from the locked environment; CI runs the same hooks plus a full-history secret scan and the tests with
  coverage.

## Consequences
- Good: 200 → 0 mypy errors outside the documented debt (ADR 0011); 14 modules strict; branch coverage ≈ 76% in CI.
- Good: the numbers can only improve without someone deliberately editing the floor in a reviewed change.
- Cost: annotations on new code; a few minutes per pull request.

## Alternatives considered
- `--strict` everywhere at once: weeks of churn in code that works, including sealed model code (ADR 0011).
- No gate: the 4-day red CI in the data platform (2026-10-04) shows unenforced checks decay.
