# Disk space: this Mac and the VPS

Both disks are small. The studio makes videos; most of them are only needed until their post goes out.

## This Mac (automatic since ADR 0042)
- **What goes:** generated videos in `dhaka-kacchi-reels/out/jobs/` that no post needs (a finished post past `keep_days_after_post`
  days, a cancelled one, an experiment), old knowledge packs (`data/knowledge/kp-*`, the active one and the newest 3 stay) and
  old copies of what was sent to the VPS (`data/packages/knowledge`). Settings: `config/studio/retention.yaml`.
- **What never goes:** your footage and photos (`incoming/`, `raw/`, `public/`), `out/campaign`, a video whose post still waits
  to go out (draft or scheduled), a video a stopped or cut-off run can still use, and anything while a run is working. If the
  publisher cannot be asked, nothing is deleted.
- **When:** once a day inside the scheduled check (a notification when it frees 100 MB or more), by the **Free disk space** button
  on Operations (which first shows how much it would free), and by hand: `uv run intel studio cleanup` (a preview) and
  `uv run intel studio cleanup --apply`. `--unknown-days 0` also deletes experiments made in the last day.
- **Today** shows a card when less than 15% of the disk is free.
- The plan is 5 days (not 10), so fewer videos exist at once: `plan.yaml horizon_days: 5`, `schedule.yaml lead_days: 2`,
  `min_days_between: 3`. Loading the settings refuses values that would let the calendar run dry.

## The VPS (you run these; Claude has no shell there)
The alert `HostDiskLow` means less than 15% of `/` is free. Do the steps in order; stop when there is room (aim for 30% free).

**Step 1: look, change nothing.**
```bash
cd /opt/dhaka-kacchi/dk-intelligence/deploy && df -h / && docker system df && sudo du -xh --max-depth=2 / 2>/dev/null | sort -rh | head -25
```
Expected: the disk size and use, how much Docker holds (images, containers, volumes, build cache), and the 25 biggest
folders. **Paste this back**: it says where the space is.

**Step 2: Docker leftovers** (images that no running container uses; what runs stays). Every deploy pulls a new image.
```bash
cd /opt/dhaka-kacchi/dk-intelligence/deploy && docker image prune -a -f --filter "until=48h" && docker builder prune -f --filter "until=48h" && df -h / | tail -1
```
Expected: `Total reclaimed space: ...` and a smaller "Use%".

**Step 3: system logs.**
```bash
sudo journalctl --vacuum-time=7d && df -h / | tail -1
```

**Step 4: old local backups** (Backblaze B2 keeps 30 days; `--print` first, then delete):
```bash
find /opt/dhaka-kacchi/backups -type f -mtime +7 -print
```
Expected: a list of files older than 7 days. If the list looks right:
```bash
find /opt/dhaka-kacchi/backups -type f -mtime +7 -delete && df -h / | tail -1
```

**Step 5: the publisher's media** (the videos of your drafts live here until the post is final plus 7 days).
```bash
cd /srv/dk/app/deploy && docker compose -f compose.prod.yaml --env-file .env exec daemon dk media purge --dry-run
```
Expected: what it would delete. If the service is not called `daemon`, paste the error and I will give the right name.
To keep media for 2 days instead of 7 after a post is final, add this line to `/srv/dk/app/deploy/.env` and restart:
```bash
cd /srv/dk/app/deploy && echo 'MEDIA_RETENTION_DAYS=2' >> .env && docker compose -f compose.prod.yaml --env-file .env up -d
```
(ADR 0021 of dk-publishing: a file is kept while any post that uses it is not final, so scheduled posts are safe.)

**Step 6: what the intel stack keeps.** Packs arrive through the upload-only inbox every Sunday (about 65 MB) and the model
bundle once (about 1.1 GB). The Step 1 listing shows whether copies pile up; paste it back and I will say what is safe to delete.

Never: `docker system prune --volumes` (it can delete the database volumes) or anything under the Postgres data folder.
