# Phase 3 · H0 — engineering foundation (2026-10-08)

## What changed
| | Before | After |
|---|---|---|
| Adding a model | edit `weekly.py`, `package.py`, `pipeline.py` (+ Telegram) | one folder with `plugin.py` (ADR 0009) |
| `weekly.py` model steps | 6 hand-written blocks, ~250 lines | one loop over plug-ins |
| Diagram arrows | hand-drawn; 2 missing (community map and occasion planner read the pull) | derived from declared inputs |
| Weekly run happy path | untested (`weekly.py` 50% covered) | end-to-end test with a model that exists only in the test (76%) |
| mypy | 200 errors, not in CI | 0 (+44 documented debt), 14 modules strict, in pre-commit + CI |
| Coverage | not measured in CI | branch coverage 76.7%, floor 76% in CI |
| Secret scanning | none | gitleaks: staged changes (pre-commit), full history (CI); both repos clean |
| Sealed model code | protected only at VPS intake | lock file + test + hook; changes are deliberate (ADR 0011) |
| Decisions | in commits and chat | 13 ADRs + architecture page with diagrams |

## Real issues found on the way
- **Silent discovery gap:** `models/content_scorer/` had no `__init__.py`; plug-in discovery skipped it without an
  error. Fixed, and discovery now fails if any model folder with a `spec.yaml` is not produced by a plug-in.
- **Late configuration errors:** optional settings (database URLs, inbox key) were passed on as `None` and failed deep
  inside a run. Now `required(value, "ENV_NAME")` names the missing variable at the point of use, and the API refuses
  to start without its database URL.
- **Unstated row guarantees:** `fetchone()["id"]` after `INSERT … RETURNING` assumed a row. `one(cursor)` states and
  checks it.
- **Untyped shared state:** the API's `state` dict (11 keys, used from 4 threads and ~40 routes) became a typed
  `AppState` dataclass.
- **A scan that scanned nothing:** the gitleaks pre-commit hook only sees staged files, so in CI it would always pass.
  CI now runs a separate full-history scan.
- **A per-module `strict = true` in mypy applied strictness to every module.** The flags are now spelled out.

## Verification
- 130 tests pass (117 before + 13 new: plug-in contract, validation cases, end-to-end weekly run, failing plug-in,
  Telegram texts, sealed lock).
- Pipeline definition before and after: identical steps and boxes; the arrow set differs only by the two missing
  arrows above.
- Package source lists identical (same files, same order): existing packages on the VPS stay compatible.
- `pre-commit run --all-files`: all hooks pass.
