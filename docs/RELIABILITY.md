# Reliability and disaster recovery (ADR 0021)

## Targets
| | Target | How it is met | How it is measured |
|---|---|---|---|
| **RPO** (data that can be lost) | ≤ 24 h | nightly `pg_dump` at 02:50, synced to Backblaze B2 at 03:00 (30 days) | restore drill: `intel_restore_drill_backup_age_seconds` |
| **RTO** (time back in service) | ≤ 30 min | previous image kept (`:previous`), automatic rollback on a bad deploy, written restore procedure | restore drill: `intel_restore_drill_restore_seconds` (the long pole) |
| Availability / latency | 99.5% / 95% < 300 ms | [SLO.md](SLO.md) | burn-rate alerts |

## Layers of protection
| Failure | Caught by | Recovery |
|---|---|---|
| A deploy that starts but misbehaves | smoke test in the new container (ADR 0014) | automatic rollback to `:previous` |
| A model that is wrong in production | guardrail (ADR 0020) | automatic rollback to the earlier approved version |
| The service slow / failing | SLO burn-rate alerts (ADR 0016) | [runbooks](runbooks/alerts.md) |
| A worker silently stopped | `IntelWorkerStalled` | restart (all work is idempotent) |
| The database lost or corrupted | `IntelDbDown`, the weekly restore drill | [restore procedure](runbooks/disaster.md#restore-intel-db-from-a-backup-the-procedure-the-weekly-drill-rehearses) |
| A backup that would not restore | **weekly restore drill** (Sunday 04:30) | `RestoreDrillFailing` → fix before the next night |
| The history tampered with | audit hash chain, also verified in every restored backup | `AuditTrailBroken` |
| The VPS / monitoring / network down | **dead-man's switch** (Watchdog → healthchecks.io) | [disaster.md](runbooks/disaster.md#dead-mans-switch-fired) |
| Memory exhaustion (no swap) | `HostMemoryLow` | swap (below) + per-container memory limits |

## The restore drill
`deploy/restore_drill.sh`: newest backup → a throwaway `pgvector/pgvector:pg16` container (never the live database)
→ all core tables present, registry and audit trail non-empty, **audit chain verifies**, ledger present → metrics
(`intel_restore_drill_ok`, restore seconds, backup age) → alerts `RestoreDrillFailing` / `RestoreDrillStale`.
Tested with Docker in CI: a real backup restores and verifies; a backup of a tampered audit trail fails the drill.

## Dead-man's switch
Prometheus's `Watchdog` alert fires forever; Alertmanager posts it to a healthchecks.io ping URL every 2 minutes.
If the pings stop for 10 minutes, healthchecks.io (outside our infrastructure) emails / messages the owner. Only an
empty ping with the alert's name leaves the VPS.

## Swap
The VPS had **no swap**: when memory runs out the kernel kills a process without warning. A 2 GB swap file turns a
sudden kill into a slowdown the memory alert can catch (`vm.swappiness=10`: used only under pressure). One time, as
`deploy`:
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab && echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf && sudo sysctl -p /etc/sysctl.d/99-swappiness.conf && free -h
```
