# 0019. Governance: database-enforced lifecycle, hash-chained audit trail, model cards v2, reproducibility

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
Approval, rejection, expiry and rollback were enforced in Python only. A bug, or anyone with database access, could
approve a rejected package, rewrite who approved what, or edit history; the `event_log` was an ordinary table. Model
cards were a short summary without intended use, limits or calibration. "Every model can be rebuilt" was a claim,
never tested.

## Decision
1. **Lifecycle in the database.** `TRANSITIONS` (`serve/lifecycle.py`) lists the only status changes
   (candidate → approved / rejected / expired); a trigger enforces the same table, decisions are set once
   (`approval_decided_once`), activation history is append-only. Stages people see are derived:
   candidate, production, retired, rejected, expired, invalid, incompatible, pipeline test. A test tries all 42
   status pairs against the database and requires exactly the Python set.
2. **Tamper-evident audit trail.** `audit_log` rows are written by the registry in the same transaction as the
   change they record (received, approval requested, approved / rejected by whom and through which channel,
   expired, activated, rolled back); a failed action leaves no row. UPDATE, DELETE and TRUNCATE are refused; each
   row's SHA-256 covers the previous row's hash, so `audit_verify()` finds the first edited or missing entry even if
   a superuser bypasses the triggers. Existing history was backfilled into the chain. An **Audit page**, `/v1/audit`,
   `intel_audit_chain_ok` and a paging alert (`AuditTrailBroken`) make it visible.
3. **Model cards v2** (built into every package): intended use, users, out-of-scope uses, limitations and ethics
   from the model's plug-in (`ModelCard`, not the sealed spec); lineage (snapshots, split, commit, sealed-lock match,
   runtime); data checks of the pull (ADR 0017); training coverage (ADR 0018 reference); results with the 95% CI,
   confusion matrix and per platform; a calibration table from the test predictions; the judge's gates. Insights
   models get no invented metrics.
4. **Reproducibility, tested.** `intel reproduce RUN` trains again from the run's recorded snapshots and compares the
   chosen model, parameters, CV leaderboard, test metrics and every test prediction. A CI test trains twice on
   synthetic snapshots and requires identical results.

## Consequences
- Good: the record of who approved which model is trustworthy, and illegal states are impossible, not merely
  unlikely. The production model `cs-20261008T071453-4ea997` reproduces exactly from its lineage (75/75 test
  predictions identical).
- Good: the reproducibility test now exercises real training in CI (coverage 77% → 82.6%).
- Cost: governance rules live in two places (Python and SQL); the equality test keeps them in step.
- Limit: a superuser can still *break* the chain; the design guarantees it is *detected* (and alerted), not prevented.
  Off-box anchoring of the latest hash (e.g. in the nightly backup) is a possible next step.

## Alternatives considered
- Application-level checks only: one bug or one manual query away from an untrustworthy record.
- An external ledger / blockchain: heavy for one database; a hash chain gives tamper evidence at no cost.
