# 2026-10-13: after a VPS reboot the ordering site's API was down

## What happened
The owner asked for a VPS restart (pending "System restart required"). After the reboot `https://dhakakacchi.com/order/`
could not place orders: `api.dhakakacchi.com`, `publish.` and `media.` refused connections on 80/443. The marketing
page itself (another host) stayed up.

## Cause
`deploy-caddy-1` (the harness's Caddy, the only service publishing 80/443) was `Exited (128)`. At boot `tailscaled`
started first and bound `<tailnet-ip>:443` (tailscale serve for intel-api). Docker then failed to bind `0.0.0.0:443`
for Caddy, and a failed start does not retry. The first `docker start` left the container running **without its port
mappings**, so only recreating it from the compose file fixed it.

## Fix (by hand, 2026-10-13)
`systemctl stop tailscaled`, `docker compose up -d --force-recreate --no-deps caddy` in the harness `deploy/` folder,
`systemctl start tailscaled`. Public API returned 200; tailnet serve entries (443, 8443, 8444) were intact.
Prometheus and Alertmanager also needed `docker start` (they exited cleanly at shutdown).

## Prevention
`infra/site.yml`: a systemd drop-in makes `tailscaled` start after Docker and wait (max 120 s) until something holds
`0.0.0.0:443`, then start anyway. Takes effect at the next reboot.

## Not done / follow-ups
- An alert when `api.dhakakacchi.com` stops answering from outside (blackbox probe) was not checked in this incident.
- Monitoring containers have no restart policy that survives a clean shutdown: check their compose `restart:` setting.
