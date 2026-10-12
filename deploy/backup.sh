#!/usr/bin/env bash
# Nightly dump of intel-db into the harness backup folder. The harness's own backup job (03:00) syncs every
# *.sql.gz there to Backblaze B2 and prunes after 30 days, so this needs no storage credentials of its own.
# Host crontab (deploy user), before the harness job:
#   50 2 * * * /opt/dhaka-kacchi/dk-intelligence/deploy/backup.sh >> /opt/dhaka-kacchi/logs/intel-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=SCRIPTDIR/metrics.sh
source ./metrics.sh
dest="/opt/dhaka-kacchi/backups/intel_$(date -u +%Y-%m-%d_%H%M%S).sql.gz"
tmp="$dest.partial"
# --exclude-table-data='rag.*': the knowledge base is rebuildable from its pack and keeps third-party review text out
# of the off-site backup (ADR 0030); its tables' structure stays. --no-privileges: intel owns everything; the rag-only
# roles and their grants are (re)created by deploy.sh, so a restore never fails on a role it doesn't have.
docker compose exec -T intel-db pg_dump -U intel --no-owner --no-privileges --exclude-table-data='rag.*' intel \
  | gzip > "$tmp"
gzip -t "$tmp" && mv "$tmp" "$dest"
echo "$(date -u +%FT%TZ) intel-db backup -> $dest ($(du -h "$dest" | cut -f1))"
write_metrics intel_backup <<METRICS
# HELP intel_backup_last_success_timestamp_seconds Last successful intel-db dump.
# TYPE intel_backup_last_success_timestamp_seconds gauge
intel_backup_last_success_timestamp_seconds $(date +%s)
# HELP intel_backup_last_size_bytes Size of the last successful dump.
# TYPE intel_backup_last_size_bytes gauge
intel_backup_last_size_bytes $(wc -c < "$dest" | tr -d ' ')
METRICS
