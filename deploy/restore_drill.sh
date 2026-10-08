#!/usr/bin/env bash
# Weekly restore drill (ADR 0021): a backup counts only once it has been restored. Restores the NEWEST intel-db dump
# into a throwaway Postgres container (never the live database), checks it, measures RPO / RTO, publishes metrics.
#
#   checks: every core table exists; registry and audit trail non-empty; the audit hash chain verifies
#           (audit_verify() IS NULL); the newest ledger prediction is recorded
#   RPO:    age of the backup when the drill runs (target <= 24 h + the nightly schedule)
#   RTO:    restore duration (target: whole recovery <= 30 min; restore is the long pole)
#
# Host crontab (deploy user), Sunday 04:30, after the nightly backup (02:50) and its B2 sync (03:00):
#   30 4 * * 0 /opt/dhaka-kacchi/dk-intelligence/deploy/restore_drill.sh >> /opt/dhaka-kacchi/logs/intel-restore-drill.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=SCRIPTDIR/metrics.sh
source ./metrics.sh

BACKUPS="${INTEL_BACKUP_DIR:-/opt/dhaka-kacchi/backups}"
IMAGE="${INTEL_DRILL_IMAGE:-pgvector/pgvector:pg16}"   # the same image as intel-db
NAME="intel-restore-drill-$$"
TABLES="model_package approval model_activation event_log kv ledger_post prediction post_outcome training_run model_reference audit_log schema_migrations"

fail() {
  echo "!! restore drill FAILED: $1"
  write_metrics intel_restore_drill <<METRICS || true
# HELP intel_restore_drill_last_run_timestamp_seconds When the restore drill last ran.
# TYPE intel_restore_drill_last_run_timestamp_seconds gauge
intel_restore_drill_last_run_timestamp_seconds $(date +%s)
# HELP intel_restore_drill_ok 1 if the last drill restored and verified the newest backup.
# TYPE intel_restore_drill_ok gauge
intel_restore_drill_ok 0
METRICS
  exit 1
}
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

dump=""
for f in "$BACKUPS"/intel_*.sql.gz; do  # the newest by modification time (portable: GNU and BSD)
  [[ -f "$f" ]] || continue
  if [[ -z "$dump" || "$f" -nt "$dump" ]]; then dump="$f"; fi
done
[[ -n "$dump" ]] || fail "no backup found in $BACKUPS"
age_s=$(( $(date +%s) - $(date -r "$dump" +%s) ))
echo "== $(date -u +%FT%TZ) drill on $(basename "$dump") ($(( age_s / 3600 )) h old)"
gzip -t "$dump" || fail "backup is not a valid gzip file"

start=$(date +%s)
docker run -d --name "$NAME" -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=intel -e POSTGRES_USER=intel "$IMAGE" >/dev/null
# Ask over TCP: the image's init phase runs a temporary server on the Unix socket only, then restarts; a socket check
# can succeed against that temporary server and then hit the restart gap (a flaky drill, seen in testing).
ready() { docker exec "$NAME" pg_isready -h 127.0.0.1 -U intel -d intel >/dev/null 2>&1; }
for _ in $(seq 1 90); do
  ready && break
  sleep 1
done
ready || fail "scratch Postgres did not start"
gunzip -c "$dump" | docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U intel -d intel >/dev/null \
  || fail "restore into a fresh database failed"
restore_s=$(( $(date +%s) - start ))

q() { docker exec "$NAME" psql -At -U intel -d intel -c "$1"; }
for t in $TABLES; do
  [[ "$(q "select to_regclass('public.$t') is not null")" == "t" ]] || fail "table $t missing after restore"
done
packages="$(q 'select count(*) from model_package')"
audit="$(q 'select count(*) from audit_log')"
[[ "$packages" -gt 0 ]] || fail "registry is empty"
[[ "$audit" -gt 0 ]] || fail "audit trail is empty"
bad="$(q 'select coalesce(audit_verify()::text, '"''"')')"
[[ -z "$bad" ]] || fail "audit chain broken at entry $bad in the restored copy"
predictions="$(q 'select count(*) from prediction')"
echo "== restored in ${restore_s}s: ${packages} packages, ${audit} audit entries (chain verified), ${predictions} predictions"

write_metrics intel_restore_drill <<METRICS
# HELP intel_restore_drill_last_run_timestamp_seconds When the restore drill last ran.
# TYPE intel_restore_drill_last_run_timestamp_seconds gauge
intel_restore_drill_last_run_timestamp_seconds $(date +%s)
# HELP intel_restore_drill_last_success_timestamp_seconds When a drill last restored and verified a backup.
# TYPE intel_restore_drill_last_success_timestamp_seconds gauge
intel_restore_drill_last_success_timestamp_seconds $(date +%s)
# HELP intel_restore_drill_ok 1 if the last drill restored and verified the newest backup.
# TYPE intel_restore_drill_ok gauge
intel_restore_drill_ok 1
# HELP intel_restore_drill_restore_seconds Time to restore the backup into a fresh database (RTO's long pole).
# TYPE intel_restore_drill_restore_seconds gauge
intel_restore_drill_restore_seconds ${restore_s}
# HELP intel_restore_drill_backup_age_seconds Age of the restored backup when the drill ran (RPO).
# TYPE intel_restore_drill_backup_age_seconds gauge
intel_restore_drill_backup_age_seconds ${age_s}
METRICS
echo "== drill OK (RPO $(( age_s / 3600 )) h, restore ${restore_s} s)"
