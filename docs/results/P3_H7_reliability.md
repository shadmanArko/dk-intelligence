# Phase 3 · H7 — reliability and disaster recovery (2026-10-08)

| | What | Where |
|---|---|---|
| Targets | RPO ≤ 24 h, RTO ≤ 30 min; failure → detection → recovery map | `docs/RELIABILITY.md` |
| Restore drill | weekly: newest backup → scratch container → tables, registry, **audit chain verified** → RPO / restore-time metrics → alerts | `deploy/restore_drill.sh` |
| Dead-man's switch | always-firing Watchdog → healthchecks.io every 2 min; external alert when pings stop | Alertmanager route, `up.sh` |
| Runbooks | 25 alert sections + disaster guide; Telegram links each alert to its section; coverage test both ways | `docs/runbooks/` |
| Postmortems | template + 4 real incidents | `docs/postmortems/` |
| Swap | 2 GB, swappiness 10 (one command) | `docs/RELIABILITY.md#swap` |

## Live (2026-10-08)
- First VPS drill: **failed** on `table model_reference missing`: the newest dump (00:50 UTC) predates migrations
  0005/0006. The drill was too strict, not the backup; it now checks each backup against its own schema version
  (test added). Re-run: `restored in 4 s: schema 0004_reach_outcome, 3 packages … drill OK (RPO 18 h, restore 4 s)`:
  well inside RPO 24 h / RTO 30 min.
- Dead-man's switch: 5 pings delivered to healthchecks.io, 0 failures; swap 2.0 GiB active.

## Found while testing
- **A flaky drill:** the first version waited for Postgres over its Unix socket. The official image starts a
  temporary socket-only server during initialisation, then restarts, so the check could pass and the next step hit
  the restart gap: the drill failed on its second run in testing. Readiness over TCP sees only the real server; the
  test then passed three runs in a row.
- An unverified date in a postmortem (written as "5 October"); corrected to what is actually known.

## Tests
Restore drill with real Docker (a real backup restores and verifies; a tampered audit trail fails the drill; no
backup fails), runbook coverage both ways, the Telegram runbook link; promtool cases for Watchdog and the drill
alerts; `amtool check-config` for both dead-man renderings (configured / not).
