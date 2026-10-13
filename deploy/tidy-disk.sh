#!/usr/bin/env bash
# Weekly tidy of the VPS disk (ADR 0042, postmortem 2026-10-13). Removes what grows by itself and nothing needs:
#   Docker images no container uses, older than 3 days (every deploy keeps its own :previous, and CI re-pulls on demand),
#   stopped containers older than 3 days, build cache older than 3 days, and OUR database dumps older than 14 days
#   (the off-site copy at Backblaze keeps 30 days).
# NEVER touches Docker volumes (the databases live there), running containers, the harness's own backup files or anything else.
# Host crontab (deploy user), managed by infra/site.yml:
#   30 5 * * 0 /opt/dhaka-kacchi/dk-intelligence/deploy/tidy-disk.sh >> /opt/dhaka-kacchi/logs/disk-tidy.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=SCRIPTDIR/metrics.sh
source ./metrics.sh
BACKUPS="${TIDY_BACKUP_DIR:-/opt/dhaka-kacchi/backups}"
free_bytes() { df -Pk / | awk 'NR==2 {print $4 * 1024}'; }   # portable: the same on Linux and macOS
before="$(free_bytes)"

docker container prune -f --filter "until=72h" >/dev/null || echo "!! container prune failed"
docker image prune -a -f --filter "until=72h" >/dev/null || echo "!! image prune failed"
docker builder prune -af --filter "until=72h" >/dev/null || echo "!! builder prune failed"
if [[ -d "$BACKUPS" ]]; then
  find "$BACKUPS" -maxdepth 1 -type f -name 'intel_*.sql.gz*' -mtime +14 -delete || echo "!! old dumps not removed"
fi

after="$(free_bytes)"
freed=$((after - before))
echo "$(date -u +%FT%TZ) disk tidy: freed $((freed / 1048576)) MB, $((after / 1048576)) MB free ($(df -Pk / | awk 'NR==2 {print $5}') used)"
write_metrics disk_tidy <<METRICS
# HELP intel_disk_tidy_last_success_timestamp_seconds Last weekly disk tidy.
# TYPE intel_disk_tidy_last_success_timestamp_seconds gauge
intel_disk_tidy_last_success_timestamp_seconds $(date +%s)
# HELP intel_disk_tidy_last_freed_bytes What the last tidy freed (negative if something else grew meanwhile).
# TYPE intel_disk_tidy_last_freed_bytes gauge
intel_disk_tidy_last_freed_bytes $freed
METRICS
