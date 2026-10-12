# Runbook: dk_intel (the MCP server)

Everything below runs on the Mac, from the repo root:
`cd "/Users/shadman.arko/Documents/Work_DoNotTouch/Dhaka Kacchi/dk-intelligence"`.

## Health at a glance
| Question | Command | Good looks like |
|---|---|---|
| Are calls working, how fast? | `uv run intel mcp audit stats` | errors low; search p95 under ~3 s once warm |
| Has anyone tampered with the audit? | `uv run intel mcp audit verify` | `{"ok": true, ...}` |
| What's in the knowledge base? | `uv run intel pack status` | `active` names the newest pack |
| Is the HTTP server up? | `curl -s 127.0.0.1:8766/healthz` | `{"status":"ok"}` |
| Live counters (HTTP only) | `curl -s 127.0.0.1:8766/metrics \| grep intel_mcp` | `intel_mcp_auth_failures_total` not climbing |

## Something is wrong
| Symptom | Likely cause | Fix |
|---|---|---|
| Tools say `UPSTREAM_UNAVAILABLE ... knowledge database` | the `dk-rag` container is stopped | `uv run intel pack init --start` |
| Tools say `UPSTREAM_UNAVAILABLE ... INTEL_API_URL` / intel-api | VPS or tailnet down | check Tailscale; model tools recover by themselves; brand and search keep working |
| `BRAND_UNAVAILABLE` | the active pack is missing or was edited (checksums) | `uv run intel pack status`; activate the previous pack: `uv run intel pack activate <id>` |
| A tool leaks something it shouldn't | — | **kill switch:** add it to `INTEL_MCP_DISABLED_TOOLS` in `.env` (comma-separated), then restart: a new Claude Code session (stdio) or restart `intel mcp serve --transport http`. Then fix and add a test |
| Search is slow right after start | models load on the first search (stdio: per process) | expected 5–20 s once; the HTTP server warms them at start |
| `Rate limit exceeded` | one principal above 10 requests/s | slow the client; limits are per token on HTTP |
| Sunday message says "Knowledge pack refused by its gate" | the new pack lost a dev question's answer or has residual PII | nothing breaks (last week's stays). Look at `data/knowledge/gates/<pack>.json`, fix, then `uv run intel pack load <pack>` |
| `audit verify` fails | the chain file was edited or truncated | keep the file for investigation, start a new month's chain, write a postmortem |

## Tokens and keys (HTTP)
- **Issue:** `uv run intel mcp token issue --client <name> --profile <role> --ttl 30d --write-env INTEL_MCP_TOKEN_<NAME>`.
  Add `--max-sensitivity S1` to keep review and creator text away from that client.
- **Revoke one:** `uv run intel mcp token revoke <jti>` (the id printed at issue). Takes effect within a minute.
- **Rotate everything** (a leak of `.env`): `uv run intel mcp init --write-env --rotate`, restart the HTTP server,
  re-issue the tokens you still need. Every old token stops working immediately.
- Never paste a token into chat or a terminal command; they live in `.env` only.

## Knowledge pack
- **Weekly:** the Sunday run rebuilds, gates and activates it (soft step; see the Sunday Telegram message).
- **By hand:** `uv run intel pack build --index --no-activate`, then `uv run intel pack load <pack>` (gated).
- **Roll back:** `uv run intel pack activate <previous id>`; the database follows on the next `pack load`.

## Changing the server (the recipe)
1. **A tool:** the capability (with fakes and unit tests) → `@mcp.tool` in the right `servers/*.py` (a
   "Use when / Not for" description, read-only annotations, `labels()` meta) → `profiles.yaml` → `budgets.yaml`
   → `uv run intel mcp catalog` (review the snapshot diff; contracts only grow) → tests.
2. **A prompt:** a file in `mcp_server/prompts/` with front matter, then rerun the tool-use eval
   (`uv run python -m intel.mcp_server.tool_eval`).
3. **A profile:** one entry in `profiles.yaml`, then `uv run intel mcp catalog`.
4. **A client:** `intel mcp config` learns its format, plus one example and one contract test.

## On the VPS (ADR 0030)

### Go-live, once (owner)
Before you start: this release's CI deploy is live (it brings the knowledge intake and migration 0007). Run
everything on the VPS as `deploy`, not root.

1. **Mac: create the VPS's own signing secret** (a separate key, so Mac tokens never work on the VPS):
   ```bash
   cd "/Users/shadman.arko/Documents/Work_DoNotTouch/Dhaka Kacchi/dk-intelligence" && uv run intel mcp init --host vps --write-env
   ```
   Expected: `INTEL_MCP_SECRET_VPS written to .env (value not shown)`.
2. **Mac: send the search models (once, ~1.2 GB) and the current pack (~65 MB)** through the upload-only inbox:
   ```bash
   cd "/Users/shadman.arko/Documents/Work_DoNotTouch/Dhaka Kacchi/dk-intelligence" && uv run intel pack ship --models
   ```
   Expected: two `uploaded knowledge-…` lines.
3. **VPS: two database passwords.** Generated on the VPS; they never leave it:
   ```bash
   cd /opt/dhaka-kacchi/dk-intelligence/deploy && ( umask 077; { echo "INTEL_MCP_READER_PASSWORD=$(openssl rand -hex 24)"; echo "INTEL_MCP_LOADER_PASSWORD=$(openssl rand -hex 24)"; } >> .env ) && echo ok
   ```
4. **Mac: copy the VPS secret to the clipboard** (it isn't shown):
   ```bash
   cd "/Users/shadman.arko/Documents/Work_DoNotTouch/Dhaka Kacchi/dk-intelligence" && grep '^INTEL_MCP_SECRET_VPS=' .env | cut -d= -f2- | tr -d '\n' | pbcopy && echo copied
   ```
   **VPS:** paste it into the hidden prompt:
   ```bash
   cd /opt/dhaka-kacchi/dk-intelligence/deploy && read -rsp "INTEL_MCP_SECRET_VPS: " S && echo && ( umask 077; printf 'INTEL_MCP_SECRET_VPS=%s\n' "$S" >> .env ) && unset S && echo saved
   ```
5. **VPS: the tailnet name, your login, and the switch** (`COMPOSE_PROFILES=mcp` is decision D8):
   ```bash
   cd /opt/dhaka-kacchi/dk-intelligence/deploy && read -rp "This VPS's tailnet name (…ts.net): " H && read -rp "Your Tailscale login: " L && { echo "INTEL_MCP_ALLOWED_HOSTS=$H"; echo "INTEL_MCP_OWNER_LOGINS=$L"; echo "COMPOSE_PROFILES=mcp"; } >> .env && echo ok
   ```
6. **VPS: the tailnet port** (Ansible adds `tailscale serve` 8444 → 127.0.0.1:8711). Preview first, then apply:
   ```bash
   cd /opt/dhaka-kacchi/dk-intelligence && ansible-playbook -i infra/inventory.yml infra/site.yml --check --diff -K
   ```
   ```bash
   cd /opt/dhaka-kacchi/dk-intelligence && ansible-playbook -i infra/inventory.yml infra/site.yml -K
   ```
7. **VPS: deploy.** This starts intel-mcp next to intel-api:
   ```bash
   cd /opt/dhaka-kacchi/dk-intelligence/deploy && ./deploy.sh 2>&1 | tail -8
   ```
   Expected: `== deployed <commit>` and then `== intel-mcp <commit> healthy`. Paste the output back.
8. **VPS, 5 minutes later:** the pack is loaded, and memory looks fine:
   ```bash
   cd /opt/dhaka-kacchi/dk-intelligence/deploy && docker compose ps intel-mcp && docker compose logs --tail 30 intel-mcp | grep -i "knowledge pack" ; docker stats --no-stream --format "{{.Name}} {{.MemUsage}}" | grep -E "mcp|intel-db" ; free -h
   ```
   Paste the output back. Claude then tests it from the Mac over the tailnet with a short-lived VPS token.
9. **Mac: weekly shipping.** Add `INTEL_SHIP_KNOWLEDGE=true` to the Mac's `.env`. From then on, each Sunday's gated
   pack goes to the VPS by itself, and the Sunday message says "and sent to the VPS".

### Day to day
| Task | Command |
|---|---|
| Stop it (kill switch; ordering and intel-api are unaffected) | `cd /opt/dhaka-kacchi/dk-intelligence/deploy && docker compose stop intel-mcp` |
| Start it again | `cd /opt/dhaka-kacchi/dk-intelligence/deploy && docker compose up -d intel-mcp` |
| Logs | `cd /opt/dhaka-kacchi/dk-intelligence/deploy && docker compose logs --tail 100 intel-mcp` |
| Revoke a VPS token | `cd /opt/dhaka-kacchi/dk-intelligence/deploy && echo <jti> >> mcp-revoked.txt` (a jti is not secret; works within a minute) |
| Roll the knowledge back | Mac: `uv run intel pack rollback <earlier pack id> --vps` |
| Issue a token for an agent | Mac: `uv run intel mcp token issue --client <name> --profile <role> --host vps --ttl 30d --write-env INTEL_MCP_TOKEN_<NAME>` |
| Turn it off for good | remove `COMPOSE_PROFILES=mcp` from `deploy/.env`, then `docker compose stop intel-mcp` |
