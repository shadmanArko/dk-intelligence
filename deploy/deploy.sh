#!/usr/bin/env bash
# dk-intelligence deploy, run on the VPS by /opt/dhaka-kacchi/bin/run-intel-deploy.sh (forced-command key, door 3)
# after it has pulled main. Builds, starts, waits for /ready; on failure prints logs and exits non-zero.
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "deploy/.env missing: run the first-time setup in deploy/README.md"; exit 2; }

echo "== build"
docker compose build --pull intel-api
echo "== start"
docker compose up -d --remove-orphans
echo "== wait for /ready"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8710/ready >/dev/null 2>&1; then
    curl -fsS http://127.0.0.1:8710/ready; echo
    echo "== deployed $(git -C .. rev-parse --short HEAD)"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 3
done
echo "!! intel-api did not become ready"; docker compose logs --tail 80 intel-api; exit 1
