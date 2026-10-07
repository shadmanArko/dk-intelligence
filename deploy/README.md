# dk-intelligence on the VPS

What runs (`docker compose -p dk-intelligence`):

| Container | What | Memory limit | Reachable from |
|---|---|---|---|
| `intel-db` | own Postgres 16 + pgvector: registry, approvals, activations, events (later: ledger) | 384 MB | the `intel` docker network only |
| `intel-api` | scoring API (`/v1/predict` = harness predictor contract), approval page, inbox watcher, Telegram bot | 512 MB | `127.0.0.1:8710`, published to the tailnet by `tailscale serve` |

Nothing here touches the harness stack: separate compose project, network, database and volumes.

## First-time setup (as the `deploy` user)

### 1. Read-only checkout of this repo
```bash
ssh-keygen -t ed25519 -f ~/.ssh/dk_intelligence_repo -N "" -C "vps read dk-intelligence"
cat ~/.ssh/dk_intelligence_repo.pub
```
GitHub → dk-intelligence → Settings → Deploy keys → **Add deploy key**: paste it, leave "Allow write access" OFF.
```bash
printf 'Host github-dk-intelligence\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/dk_intelligence_repo\n  IdentitiesOnly yes\n' >> ~/.ssh/config
git clone git@github-dk-intelligence:shadmanArko/dk-intelligence.git /opt/dhaka-kacchi/dk-intelligence
```

### 2. `deploy/.env` (secrets stay on the VPS)
```bash
cd /opt/dhaka-kacchi/dk-intelligence/deploy
( umask 077; { echo "INTEL_DB_PASSWORD=$(openssl rand -hex 24)"; echo "INBOX_GID=$(getent group intelinbox | cut -d: -f3)"; echo "TELEGRAM_CHAT_ID=<your chat id>"; echo "INTEL_REQUIRE_TAILSCALE=true"; } > .env )
nano .env          # add the line TELEGRAM_BOT_TOKEN=<token from BotFather>
```

### 3. First deploy
```bash
./deploy.sh
```
Ends with `{"status":"ready",...}` and `== deployed <commit>`.

### 4. Approval page on the tailnet only
```bash
sudo tailscale serve status
sudo tailscale serve --bg --https=443 http://127.0.0.1:8710
```
If it says HTTPS is not enabled, enable "HTTPS Certificates" once in the Tailscale admin console (DNS page) and repeat.
It prints a URL like `https://vmd206122.<tailnet>.ts.net`. Add it to `.env` and restart:
```bash
echo "INTEL_PUBLIC_URL=https://vmd206122.<tailnet>.ts.net" >> .env && docker compose up -d
```
The page works only from your tailnet devices; every approve / reject / rollback there needs the
`Tailscale-User-Login` header that only `tailscale serve` adds, plus a per-page CSRF token.

### 5. Automatic deploys from GitHub (door 3)
```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_actions_intel_deploy -N "" -C "gha intel deploy"
sudo install -d -m 755 /opt/dhaka-kacchi/bin
sudo tee /opt/dhaka-kacchi/bin/run-intel-deploy.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
# The ONLY command the GitHub Actions intel key can run (forced command). Not tracked in git on purpose.
set -euo pipefail
cd /opt/dhaka-kacchi/dk-intelligence
git pull --ff-only origin main
exec /opt/dhaka-kacchi/dk-intelligence/deploy/deploy.sh
EOF
sudo chmod 755 /opt/dhaka-kacchi/bin/run-intel-deploy.sh
echo "command=\"/opt/dhaka-kacchi/bin/run-intel-deploy.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat ~/.ssh/github_actions_intel_deploy.pub)" >> ~/.ssh/authorized_keys
cat ~/.ssh/github_actions_intel_deploy      # copy into the GitHub secret below, then:
rm ~/.ssh/github_actions_intel_deploy ~/.ssh/github_actions_intel_deploy.pub
```
GitHub → dk-intelligence → Settings → Secrets and variables → Actions → New repository secret:
`INTEL_VPS_HOST` (same value as the harness `VPS_HOST`), `INTEL_VPS_USER` = `deploy`,
`INTEL_VPS_SSH_KEY` = the private key printed above. Every push to main then runs tests, then deploys.

### 6. Measurement (M6): warehouse access over the harness network
`intel-api` joins the harness network to read the warehouse as `intelligence_reader` (read-only) and to ask the
production predictor (`http://predictor:8000`) for its shadow score. Find the network name, then add two lines to
`.env` (the password is the one you set for `intelligence_reader`):
```bash
docker network ls --format '{{.Name}}' | grep -i kacchi
nano .env   # HARNESS_NETWORK=<name from above>
            # INTEL_WAREHOUSE_DSN=postgresql://intelligence_reader:<password>@postgres:5432/warehouse
git -C .. pull --ff-only && ./deploy.sh
```
Within a minute: `curl -s 127.0.0.1:8710/v1/ledger/summary` shows `last_measure` and the ledger counts.

### 7. Nightly backup of intel-db (joins the harness B2 backup)
```bash
mkdir -p /opt/dhaka-kacchi/logs && ./backup.sh && ls -l /opt/dhaka-kacchi/backups | tail -3
( crontab -l; echo "50 2 * * * /opt/dhaka-kacchi/dk-intelligence/deploy/backup.sh >> /opt/dhaka-kacchi/logs/intel-backup.log 2>&1" ) | crontab -
crontab -l | tail -3
```

## Everyday
| Task | Command (in `/opt/dhaka-kacchi/dk-intelligence/deploy`) |
|---|---|
| Status | `docker compose ps` · `curl -s 127.0.0.1:8710/ready` |
| Logs | `docker compose logs --tail 100 intel-api` |
| Restart | `docker compose up -d` |
| Manual deploy | `/opt/dhaka-kacchi/bin/run-intel-deploy.sh` |
| Database shell | `docker compose exec intel-db psql -U intel intel` |
| Roll back a model | Telegram `/rollback`, or the button on the page |
| Live ledger | `curl -s 127.0.0.1:8710/v1/ledger/summary` · Telegram `/status` |

