# Phase 3 · H5 — governance (2026-10-08)

## What exists now
| | What | Where |
|---|---|---|
| Lifecycle | candidate → production ⇄ retired, or rejected / expired; invalid / incompatible / pipeline test never offered. Enforced by a DB trigger equal to the Python table (all 42 pairs tested) | `serve/lifecycle.py`, migration 0006 |
| Final decisions | an approval decision cannot be changed; activation history cannot be edited or deleted | triggers |
| Audit trail | every governance action, in the same transaction; append-only; SHA-256 chain; backfilled history | `audit_log`, `audit_verify()` |
| Visibility | `/audit` page (chain status, every model's stage, history), `/v1/audit`, `intel_audit_chain_ok`, `AuditTrailBroken` (page) | web, Prometheus |
| Model cards v2 | purpose, users, out of scope, lineage, data checks, training coverage, CI, confusion matrix, per platform, calibration, gates, limits, ethics | `package.model_card`, `ModelCard` on each plug-in |
| Reproducibility | `intel reproduce RUN` + a CI test that trains twice and requires identical results | `intel/reproduce.py` |

## Evidence on real data
- **The production model reproduces exactly:** `intel reproduce 20261008T071453Z_content_scorer` → identical model,
  parameters, CV leaderboard, metrics and all 75 test predictions (max difference 0.0); sealed code matches the
  lock; even the data fingerprint in the version name matches (`…-4ea997`).
- Its new card shows what the old one hid: balanced accuracy 0.688 (95% CI 0.60–0.78) vs 0.631 today, Facebook
  0.65 / Instagram 0.75, and a confusion matrix with 20 of 39 below-usual posts flagged as above (it leans optimistic);
  the 0.4–0.6 band is roughly calibrated (says 52%, 46% happen).

## Tests
204 (+15): lifecycle equality with the database, final decisions, append-only activations, audit order across
approve / rollback / reject, no audit row for a failed action, tamper detection (edit and delete, even with triggers
disabled), backfill of pre-existing history, the page / export / metrics, model card v2 (full and insights),
deterministic training, difference reporting, refusals; plus 1 promtool case for `AuditTrailBroken`. Coverage 82.6%
(floor raised to 80%); strict typing on 22 modules.

## Found on the way
- The migration-policy test read `before truncate on audit_log` (a trigger that *prevents* truncation) as a
  destructive statement; the rule now distinguishes the TRUNCATE statement from the trigger event.
