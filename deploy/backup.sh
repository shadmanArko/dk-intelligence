#!/usr/bin/env bash
# Nightly dump of intel-db into the harness backup folder. The harness's own backup job (03:00) syncs every
# *.sql.gz there to Backblaze B2 and prunes after 30 days, so this needs no storage credentials of its own. Our own
# copies are pruned here too (ADR 0024: retention is enforced by the owner of the data, not left to a neighbour).
# Host crontab (deploy user), before the harness job:
#   50 2 * * * /opt/dhaka-kacchi/dk-intelligence/deploy/backup.sh >> /opt/dhaka-kacchi/logs/intel-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=SCRIPTDIR/metrics.sh
source ./metrics.sh
dest="/opt/dhaka-kacchi/backups/intel_$(date -u +%Y-%m-%d_%H%M%S).sql.gz"
tmp="$dest.partial"
docker compose exec -T intel-db pg_dump -U intel --no-owner intel | gzip > "$tmp"
gzip -t "$tmp" && mv "$tmp" "$dest"
find "$(dirname "$dest")" -maxdepth 1 -name 'intel_*.sql.gz' -mtime "+${INTEL_BACKUP_RETENTION_DAYS:-30}" -delete
echo "$(date -u +%FT%TZ) intel-db backup -> $dest ($(du -h "$dest" | cut -f1))"
write_metrics intel_backup <<METRICS
# HELP intel_backup_last_success_timestamp_seconds Last successful intel-db dump.
# TYPE intel_backup_last_success_timestamp_seconds gauge
intel_backup_last_success_timestamp_seconds $(date +%s)
# HELP intel_backup_last_size_bytes Size of the last successful dump.
# TYPE intel_backup_last_size_bytes gauge
intel_backup_last_size_bytes $(wc -c < "$dest" | tr -d ' ')
METRICS
