# Infrastructure as code (ADR 0022)

`site.yml` describes the dk-intelligence side of the VPS: the three doors (tunnel-only, upload-only inbox,
forced-command deploy), directories, the deploy script, cron, swap and `tailscale serve`. It **checks** but never
writes secrets (`.env` files, private keys), the git checkouts and Docker. The harness and its crontab lines are left
alone. App deploys stay with CI; this converges the host.

**Pull mode:** it runs ON the VPS from the checkout the deploy already keeps current, so no machine (and no AI
assistant) needs a shell login to the VPS to manage it.

## First time on the VPS
```bash
sudo apt install -y ansible          # includes the ansible.posix collection
cd /opt/dhaka-kacchi/dk-intelligence
ansible-playbook -i infra/inventory.yml infra/site.yml --check --diff -K    # preview: changes nothing
ansible-playbook -i infra/inventory.yml infra/site.yml -K                   # apply
ansible-playbook -i infra/inventory.yml infra/site.yml -K                   # again: must say changed=0
```
`-K` asks for the `deploy` user's sudo password.

## Change something
Edit `group_vars/all.yml` (or `site.yml`) in git → push → on the VPS `git pull` (or wait for the next deploy) → run
with `--check --diff`, then apply. Tested in CI on a fresh Ubuntu 24.04 container (`tests/test_infra.py`): it converges,
a second run changes nothing, hand-made cron lines are adopted (not duplicated) and the harness's cron line is kept.

## Rebuild a lost VPS
1. New Ubuntu machine with Docker and Tailscale (the harness's setup), user `deploy` with sudo.
2. Clone this repo and the data platform (read-only deploy keys, deploy/README.md step 1).
3. Put the `.env` files back (from your password manager) — the playbook stops and names any that are missing.
4. Run the playbook (above), then `deploy/deploy.sh`, `deploy/monitoring/up.sh`, and restore intel-db
   ([docs/runbooks/disaster.md](../docs/runbooks/disaster.md)).
