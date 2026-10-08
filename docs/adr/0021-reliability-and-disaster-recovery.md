# 0021. Reliability and disaster recovery: tested restores, a dead-man's switch, runbooks, postmortems

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
Backups ran nightly and went off-box (B2), but no backup had ever been restored. Every alert travelled through the
VPS itself, so the one failure it could not report was its own death. Alerts said what to check but not what to do
step by step, and incidents were fixed but not written down. The VPS had no swap.

## Decision
1. **Targets:** RPO ≤ 24 h, RTO ≤ 30 min for intel-api, alongside the SLOs (ADR 0016). `docs/RELIABILITY.md` maps
   each failure to what catches it and how it is recovered.
2. **Weekly restore drill** (`deploy/restore_drill.sh`, Sunday 04:30): the newest dump into a throwaway container,
   tables present, registry and audit non-empty, **audit chain verified in the restored copy**; it publishes RPO /
   restore time and alerts when failing or stale. Tested with Docker in CI, including a tampered backup that must
   fail. Readiness is checked over TCP: a Unix-socket check can hit the image's init-time restart (a flaky drill,
   found in testing).
3. **Dead-man's switch:** an always-firing `Watchdog` alert, routed only to a healthchecks.io ping URL every 2
   minutes; the external service alerts when pings stop. Without a URL it routes to `null` (nothing fails).
4. **Runbooks** for every alert (`docs/runbooks/alerts.md`), linked from each Telegram message; a test fails if an
   alert has no section or a section has no alert. A disaster guide covers API, database restore, VPS and Mac loss.
5. **Blameless postmortems** with a template; the four incidents so far are written up.
6. **Swap:** 2 GB, `vm.swappiness=10`, so memory pressure slows the box down (and alerts) instead of killing a process.

## Consequences
- Good: "we have backups" became "the newest backup restores and its audit chain verifies, every week".
- Good: the monitoring's own failure is now visible from outside.
- Cost: a free external account (healthchecks.io) and an empty ping leaving the box every 2 minutes.
- Limit: the drill restores the local copy; the B2 copy is checked only by its sync job (the harness's), not restored.
