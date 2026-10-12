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
