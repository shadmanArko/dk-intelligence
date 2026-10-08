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

## Expected first check-mode diff on the VPS
- the deploy script's header comment ("Not tracked in git on purpose" → "Managed by infra/site.yml"); commands identical;
- the four cron jobs: hand-made lines replaced by the same lines with `#Ansible:` markers (in check mode the adoption
  step is skipped, so the preview shows them as additions);
- possibly the tunnel key's comment, if it was typed differently from the Mac's `.pub` file (the comment does not
  affect access).
