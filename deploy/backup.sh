#!/usr/bin/env bash
# Nightly dump of intel-db into the harness backup folder. The harness's own backup job (03:00) syncs every
# *.sql.gz there to Backblaze B2 and prunes after 30 days, so this needs no storage credentials of its own.
# Host crontab (deploy user), before the harness job:
#   50 2 * * * /opt/dhaka-kacchi/dk-intelligence/deploy/backup.sh >> /opt/dhaka-kacchi/logs/intel-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
dest="/opt/dhaka-kacchi/backups/intel_$(date -u +%Y-%m-%d_%H%M%S).sql.gz"
tmp="$dest.partial"
docker compose exec -T intel-db pg_dump -U intel --no-owner intel | gzip > "$tmp"
gzip -t "$tmp" && mv "$tmp" "$dest"
echo "$(date -u +%FT%TZ) intel-db backup -> $dest ($(du -h "$dest" | cut -f1))"
