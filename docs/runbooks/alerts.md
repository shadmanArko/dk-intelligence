# Alert runbooks

One section per alert; every Telegram alert links here (`#alertname` in lower case). Commands run on the VPS as
`deploy`, in `/opt/dhaka-kacchi/dk-intelligence/deploy` unless stated. 🔴 = act now, 🟠 = today. Promises and
targets: [SLO.md](../SLO.md), [RELIABILITY.md](../RELIABILITY.md). A test fails if an alert has no section here.

## IntelApiDown
🔴 Prometheus cannot scrape intel-api for 3 minutes.
1. `docker compose ps` — is `intel-api` running / restarting?
2. `docker compose logs --tail 100 intel-api` — the last error (JSON lines; look for `"level": "ERROR"`).
3. Was there a deploy? `tail -3 /opt/dhaka-kacchi/logs/intel-deploys.log`. A failed deploy rolls back by itself; if
   the previous image is also broken: `INTEL_IMAGE=dk-intelligence:previous docker compose up -d`.
4. Out of memory? `docker inspect --format '{{.State.OOMKilled}}' $(docker compose ps -q intel-api)` and see
   HostMemoryLow.
5. Still down after 15 min: [disaster.md](disaster.md#intel-api-will-not-start).

## IntelApiAvailabilityBurnFast
🔴 The deep health probe fails often enough to spend 2% of the monthly error budget in an hour. Usually the same
causes as IntelApiDown (if that fires too, it suppresses this one). If the API is up: `curl -s 127.0.0.1:8710/health/deep`
— a 500 there means the database or the active model fails; check IntelDbDown and the logs.

## IntelApiAvailabilityBurnSlow
🟠 Intermittent probe failures over hours. Look for restarts (`docker compose ps` — the "Up" time), memory pressure,
and slow database answers. Note it in the budget review (SLO.md).

## IntelApiLatencyBurnFast
🔴 Most probes take over 300 ms. Check host load and memory (Grafana: VPS dashboard), `docker stats --no-stream`,
and whether a backup or the collector is running (they share the disk). A new model version that is much slower
shows in the judge's `works` gate; roll back with `/rollback` if it started after an approval.

## IntelApiLatencyBurnSlow
🟠 Slowly worse latency. Compare the p95 latency panel with the last deploy and the last model approval.

## IntelDbDown
🔴 intel-api cannot reach intel-db. `docker compose ps intel-db`, `docker compose logs --tail 50 intel-db`, disk space
(`df -h /`). A full disk stops Postgres: see HostDiskCritical. Data loss is bounded by the nightly backup (RPO 24 h).

## IntelWorkerStalled
🟠 A background worker stopped finishing its loop (inbox, telegram, reports, measure).
`docker compose logs --tail 200 intel-api | grep -i <worker>`. A restart is safe (`docker compose up -d intel-api` —
work is idempotent: the inbox re-scans, the measurer back-fills, Telegram resumes from its stored offset).

## IntelWorkerErrors
🟠 A worker keeps failing. The exception is in the logs (`"logger": "intel.api"`). Common: the warehouse unreachable
(measure), Telegram API errors (telegram), a malformed upload in the inbox (inbox — it is refused and logged once).

## IntelTrainingOverdue
🟠 No weekly training report for 8 days. On the Mac: `uv run intel weekly-status`; is the Mac on, and the launchd job
loaded (`launchctl list | grep intel.weekly`)? Run `uv run intel weekly` by hand if needed. Models keep serving.

## AuditTrailBroken
🔴 The audit trail's hash chain no longer verifies: a governance record was changed or removed outside the API.
1. Open `/audit` — it shows the first broken entry.
2. Do **not** approve or reject models until it is explained.
3. Check who had database access (`docker compose exec intel-db psql` sessions, the `deploy` user's shell history).
4. Compare with the last good backup (the restore drill verifies the chain of the newest backup).
5. Write a postmortem ([template](../postmortems/TEMPLATE.md)).

## HostDiskLow
🟠 Less than 15% free on `/`. `docker system df`; `docker image prune` (keeps images in use); old backups in
`/opt/dhaka-kacchi/backups` (B2 keeps 30 days anyway); `journalctl --vacuum-time=7d`.

## HostDiskCritical
🔴 Less than 5% free: Postgres stops writing. Free space now: `docker image prune -a`, delete the oldest local backups,
truncate big logs in `/opt/dhaka-kacchi/logs`. Then find what grew (`du -xh / --max-depth=2 | sort -h | tail`).

## HostDiskMetricMissing
🟠 node-exporter reports no root filesystem, so the disk alerts are blind. Check the node-exporter mounts in
`deploy/monitoring/docker-compose.yml` (`/:/host:ro`, `--path.rootfs=/host`) and `docker compose -p dk-monitoring ps`.

## HostMemoryLow
🔴 Less than 10% memory available. If swap is not set up yet, the kernel kills a process without warning
(see [RELIABILITY.md](../RELIABILITY.md#swap)). `docker stats --no-stream` — which container grew? Every container
has a memory limit; a container at its limit is restarted by Docker (look for OOMKilled).

## BackupStale
🟠 No successful intel-db backup for 26 h. `tail /opt/dhaka-kacchi/logs/intel-backup.log`; run
`./backup.sh` by hand; check the cron entry (`crontab -l | grep backup`) and disk space.

## RestoreDrillFailing
🟠 The weekly drill could not restore and verify the newest backup. `tail -40 /opt/dhaka-kacchi/logs/intel-restore-drill.log`
— the `!!` line says why (no backup, broken gzip, restore error, missing table, empty registry, broken audit chain).
A backup that does not restore is not a backup: fix today, then run `./restore_drill.sh` by hand.

## RestoreDrillStale
🟠 No successful drill for 8 days. Check the cron entry (`crontab -l | grep restore_drill`) and run it by hand.

## CollectorStale
🟠 The VPS data collector has had no clean daily run for 26 h. `tail -50 /opt/dhaka-kacchi/logs/mip-collector.log`.
The Mac's Sunday import still works with what was collected; a day missed is recovered by the next run's catch-up.

## CollectorStepsFailing
🟠 The last collector run had failed steps. `grep '!! failed' /opt/dhaka-kacchi/logs/mip-collector.log | tail`.
Typical: an API quota (YouTube), a source changed its format (the platform's contracts quarantine the rows).

## PrometheusRuleFailures
🟠 Prometheus failed to evaluate rules. `docker compose -p dk-monitoring logs prometheus | tail`; a bad rule would
have failed `check.sh` in CI, so suspect a missing metric type or a restart in progress.

## AlertmanagerNotificationsFailing
🟠 Alerts cannot reach Telegram (or the dead-man's switch). Check the bot token and chat id in `deploy/.env`, rerun
`deploy/monitoring/up.sh`, and send the test alert from [monitoring/README](../../deploy/monitoring/README.md).
While this fires, Grafana is the only place alerts are visible.

## Watchdog
Never act: it fires forever on purpose and keeps the external dead-man's switch fed. If healthchecks.io reports
the check **down**, follow [disaster.md](disaster.md#dead-mans-switch-fired).

## ModelRetrainRequested
🟠 Monitoring asks to retrain a model (drifted inputs or worse live results). `/v1/monitoring` lists the reasons.
The Mac trains early by itself when it is on (3-day cool-down); nothing to do unless it persists for a week.

## ModelLiveAccuracyLow
🟠 Live balanced accuracy is more than 0.10 below the test result. Compare by platform in `/v1/monitoring`; if the
guardrail has the evidence it rolls back by itself (ADR 0020). To keep the current version: Telegram `/keep`.

## ModelMonitoringStale
🟠 No monitoring result for 3 h while models are active: the measure worker is probably stalled (IntelWorkerStalled).

## IntelMcpDown
🟠 intel-mcp (the MCP server for AI clients, ADR 0030) stopped answering. Ordering and intel-api are unaffected.
1. `docker compose ps intel-mcp` and `docker compose logs --tail 100 intel-mcp`, in /opt/dhaka-kacchi/dk-intelligence/deploy.
2. Out of memory? `docker inspect --format '{{.State.OOMKilled}}' $(docker compose ps -q intel-mcp)`. If so, see
   IntelMcpMemoryHigh.
3. `docker compose up -d intel-mcp` is safe. To keep it off: `docker compose stop intel-mcp` ([mcp.md](mcp.md#on-the-vps-adr-0030)).

## IntelMcpMemoryHigh
🟠 intel-mcp is above 90% of its 2.5 GiB limit for 10 minutes, usually many searches at once.
`docker compose restart intel-mcp` frees it (models reload in ~1 minute). If the host is short of memory too
(HostMemoryLow), stop it: `docker compose stop intel-mcp`. The ordering system always comes first.

## IntelMcpAuthFailures
🟠 More than 20 refused requests to intel-mcp in 15 minutes. Either a client's token expired or was revoked (re-issue
it on the Mac with `intel mcp token issue … --host vps`), or someone on the tailnet is probing. The audit chain lists
each refusal (`kind: http`, reason, listener; never the token).
