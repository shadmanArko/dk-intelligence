# Phase 3 · H8 — infrastructure as code (2026-10-08)

| | |
|---|---|
| Playbook | `infra/site.yml` + `infra/group_vars/all.yml` (pull mode, `ansible_connection: local`) |
| Converges | door 1 (tunnel user, key with `permitopen` 5432/5442, from the Mac only), door 2 (inbox user, `rrsync -wo -no-del`, password locked), door 3 (forced-command deploy script; key checked or managed), directories, cron (4 jobs, hand-made lines adopted), swap 2 GB + swappiness 10, `tailscale serve` 443 / 8443 |
| Checks only | `.env` files (exist, mode 600), git checkouts, Docker, the door-3 key line |
| Not touched | the harness stack and its crontab lines; running containers (deploys stay with CI) |

## Test (CI, fresh ubuntu:24.04 container set up like the VPS)
`RUN1 ok=11 changed=10 failed=0` → `RUN2 ok=11 changed=0 failed=0`. The crontab kept the harness job once, adopted
the hand-made backup and daily-collector lines (once each, now marked), added the drill and weekly collector; the
door lines match the originals byte for byte. Tailscale, swap and Docker cannot run in a container: they are covered
by the first `--check --diff` on the VPS.

## First check-mode run on the VPS (2026-10-08)
`ok=20 changed=2 failed=0`; exactly the expected diff, nothing else:
- the deploy script's header comment ("Not tracked in git on purpose" → "Managed by infra/site.yml"); commands identical;
- the four cron jobs: an `#Ansible:` marker inserted above each existing hand-made line; no line duplicated, the
  harness's lines untouched;
- users, keys, directories, swap, `.env` modes and checkouts: `ok` (already as the code describes).

Applied the same day, after a second preview with the read-only checks active (door-3 forced-command key, `tailscale
serve`, Docker: all `ok`; `ok=23 changed=2`):

| Run | Result |
|---|---|
| apply | `ok=24 changed=2 failed=0`: the script comment and the four cron markers |
| apply again | `ok=24 changed=0 failed=0`: converged, the VPS now matches the code |
| `crontab -l \| grep -n backup.sh` | line 25: the harness backup (03:00); line 39: the intel-db backup (02:50) under its `#Ansible:` marker. Each runs once |

Lesson: `--check` skips every `command`/`shell` task, so the first preview silently did not run the read-only checks
(door-3 key present, `tailscale serve` status, Docker). They now carry `check_mode: false`. The preview also showed that
the cron module adopts identical lines itself, so the playbook's own adoption script was deleted.
