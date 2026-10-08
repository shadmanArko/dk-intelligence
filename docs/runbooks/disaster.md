# Disaster recovery

Targets ([RELIABILITY.md](../RELIABILITY.md)): **RPO 24 h** (nightly backup), **RTO 30 min** for intel-api.

## intel-api will not start
1. `docker compose logs --tail 200 intel-api`. A migration error names the migration (they are additive, ADR 0015).
2. Run the previous image: `INTEL_IMAGE=dk-intelligence:previous docker compose up -d intel-api`, then
   `curl -s 127.0.0.1:8710/ready`.
3. If GitHub's registry is unreachable and no image is local: `./deploy.sh --local-build` (emergency, unscanned).

## Restore intel-db from a backup (the procedure the weekly drill rehearses)
1. Pick the dump: newest `/opt/dhaka-kacchi/backups/intel_*.sql.gz` (older ones: Backblaze B2, 30 days, synced by the
   harness at 03:00).
2. `docker compose stop intel-api`
3. `docker compose exec intel-db psql -U intel -d postgres -c 'drop database intel with (force)' -c 'create database intel'`
4. `gunzip -c <dump> | docker compose exec -T intel-db psql -v ON_ERROR_STOP=1 -U intel -d intel`
5. `docker compose exec intel-db psql -U intel -d intel -c 'select audit_verify()'` — must be empty (NULL).
6. `docker compose up -d intel-api`; `curl -s 127.0.0.1:8710/ready`. Approvals made after the dump must be redone.

## VPS lost (new machine)
Until H8 (infrastructure as code) this follows the setup guides in order: [deploy/README.md](../../deploy/README.md)
(steps 1–8), [monitoring/README.md](../../deploy/monitoring/README.md), the data collector's README, then restore
intel-db as above from the newest B2 copy. The models re-arrive with the next Sunday run; the Mac re-sends reference
profiles (`intel send-references`).

## Mac lost
The VPS keeps serving and measuring. Training stops (IntelTrainingOverdue after 8 days). On a new Mac: clone the repos,
`uv sync`, restore the data platform's database from the external-drive backup (data platform README), recreate
`.env` files and SSH keys (doors 1 and 2 need the owner on the VPS to add the new public keys), then
`uv run intel weekly`. Pulls are re-taken from the warehouse; nothing on the Mac is the only copy of anything the
service needs.

## Dead-man's switch fired
healthchecks.io stopped receiving the Watchdog: the VPS, Prometheus, Alertmanager or the network is down.
1. Can you reach the VPS over Tailscale? `ssh deploy@<vps>` — if not, the hosting provider's console (reboot).
2. If yes: `docker compose -p dk-monitoring ps` and `docker compose ps` in both deploy folders.
3. After recovery, check BackupStale / RestoreDrillStale / CollectorStale for anything that missed a run.
