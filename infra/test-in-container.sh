#!/usr/bin/env bash
# Runs INSIDE a fresh ubuntu:24.04 container (tests/test_infra.py): set up a host like the VPS (a deploy user whose
# crontab already holds a harness job and hand-made copies of our jobs), apply the playbook twice, print what the
# test asserts on. The second run must change nothing.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null && apt-get install -y -qq ansible cron sudo python3 >/dev/null
useradd -m -s /bin/bash deploy
printf '%s\n' "0 3 * * * /opt/dhaka-kacchi/dhaka_kacchi_ai_harness/deploy/backup.sh >> /opt/dhaka-kacchi/logs/backup.log 2>&1" \
  "50 2 * * * /opt/dhaka-kacchi/dk-intelligence/deploy/backup.sh >> /opt/dhaka-kacchi/logs/intel-backup.log 2>&1" \
  "15 10 * * * /opt/dhaka-kacchi/mip-collector/deploy/collector/run.sh daily >> /opt/dhaka-kacchi/logs/mip-collector.log 2>&1" \
  | crontab -u deploy -
cd /repo
ansible-playbook -i infra/inventory.yml infra/site.yml -e in_container=true > /tmp/run1.log 2>&1 || { cat /tmp/run1.log; exit 1; }
ansible-playbook -i infra/inventory.yml infra/site.yml -e in_container=true > /tmp/run2.log 2>&1 || { cat /tmp/run2.log; exit 1; }
echo "RUN1 $(grep -E '^vps' /tmp/run1.log)"
echo "RUN2 $(grep -E '^vps' /tmp/run2.log)"
echo "== crontab"; crontab -u deploy -l
echo "== tunnel"; cat /home/intel/.ssh/authorized_keys; getent passwd intel | cut -d: -f7
echo "== inbox"; cat /home/intelinbox/.ssh/authorized_keys; stat -c '%U:%G %a' /opt/dhaka-kacchi/intel-data/inbox
passwd -S intelinbox | cut -d' ' -f2
echo "== door3"; stat -c '%U %a' /opt/dhaka-kacchi/bin/run-intel-deploy.sh; tail -2 /opt/dhaka-kacchi/bin/run-intel-deploy.sh
echo "== dirs"; stat -c '%n %U %a' /opt/dhaka-kacchi/metrics /opt/dhaka-kacchi/monitoring-runtime
