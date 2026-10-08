# Monitoring (ADR 0016)

Prometheus, Alertmanager (→ Telegram), Grafana, node-exporter and a blackbox prober, as compose project
`dk-monitoring`, next to intel-api. Tailnet-only: Grafana is published by `tailscale serve` on port 8443; nothing
else leaves the box. What is promised and alerted: [docs/SLO.md](../../docs/SLO.md).

## First-time setup on the VPS (deploy user), after pulling a commit that contains this folder
```bash
cd /opt/dhaka-kacchi/dk-intelligence/deploy/monitoring
( umask 077; { echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 18)"; echo "GRAFANA_ROOT_URL=https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):8443/"; } > .env )
./up.sh
sudo tailscale serve --bg --https=8443 http://127.0.0.1:3330
```
- `up.sh` creates `/opt/dhaka-kacchi/metrics` (textfile metrics) and `/opt/dhaka-kacchi/monitoring-runtime` (rendered
  Alertmanager config + the bot token, mode 600), starts the stack and reloads configs. From now on every successful
  app deploy runs it again, so rule and dashboard changes in git go live with the deploy.
- Grafana: `https://<vps>.<tailnet>.ts.net:8443`, user `admin`, password: `grep GRAFANA_ADMIN_PASSWORD .env`.
  Dashboards are read-only (they are code: edit the JSON in git).

## Prove the alert path (once, and after changing Telegram settings)
```bash
docker compose exec alertmanager amtool alert add alertname=AlertPathTest severity=ticket \
  --annotation='summary="Test: monitoring alerts reach you"' --annotation='action="Nothing to do."' \
  --alertmanager.url=http://127.0.0.1:9093
```
A 🟠 message arrives within ~30 s; a ✅ "Resolved" follows when it expires (about 5 minutes later).

## Dead-man's switch
1. Create a free account at healthchecks.io → **Add Check**: name "dk-intelligence watchdog", period **5 minutes**,
   grace **10 minutes**; under Integrations add Telegram (or e-mail) for alerts.
2. Copy the check's ping URL (`https://hc-ping.com/…`) and on the VPS:
   ```bash
   cd /opt/dhaka-kacchi/dk-intelligence/deploy/monitoring && read -rp "Ping URL: " U && echo "HEALTHCHECKS_PING_URL=$U" >> .env && ./up.sh
   ```
   `up.sh` prints `== dead-man's switch: pinging healthchecks.io`; the check turns green within 2 minutes.

## Everyday
| Task | Command (in this folder) |
|---|---|
| Status | `docker compose ps` |
| Refresh after a config change | `./up.sh` (deploys do this automatically) |
| Firing alerts | Grafana → Alerting, or `docker compose exec alertmanager amtool alert --alertmanager.url=http://127.0.0.1:9093` |
| Silence during maintenance | `docker compose exec alertmanager amtool silence add alertname=~".+" --duration=1h --comment="maintenance" --alertmanager.url=http://127.0.0.1:9093` |
| Prometheus UI (on the VPS) | `curl -s 127.0.0.1:9090/api/v1/alerts` |
| Validate everything locally / in CI | `./check.sh` |
