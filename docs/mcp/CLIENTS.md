# Connecting clients to dk_intel

dk_intel is one read-only MCP server with six role profiles ([CATALOG.md](CATALOG.md)). Pick the transport by
who is connecting ([ADR 0029](../adr/0029-transports-auth-and-keys.md)):

| Client | Transport | Auth | Config |
|---|---|---|---|
| Claude Code (daily use) | stdio | the OS user (trusted local use) | committed `.mcp.json` |
| Claude Code, several sessions sharing one server | HTTP, loopback | a 5-minute token minted per connection | `uv run intel mcp config --client claude-code --transport http` |
| Claude Desktop | stdio | the OS user | `uv run intel mcp config --client claude-desktop` |
| LangChain agent (Ollama, €0) | HTTP | a role token in `.env` | [examples/clients/langchain_agent.py](../../examples/clients/langchain_agent.py) |
| OpenAI Agents SDK (Ollama, or Groq's free tier) | HTTP | a role token in `.env` | [examples/clients/openai_agents_ollama.py](../../examples/clients/openai_agents_ollama.py) |
| Your other devices on the tailnet | HTTPS via `tailscale serve` | a token, plus your Tailscale login for `owner` | below |

**Rule of thumb:** a role profile on stdio is least privilege, not a security boundary (any local process can pass
`--profile owner`). Agents and anything not run by you connect over HTTP, where the signed token decides.

## One-time setup (owner)

```bash
cd "/Users/shadman.arko/Documents/Work_DoNotTouch/Dhaka Kacchi/dk-intelligence"
uv sync --group clients
uv run intel mcp init --write-env
```
Expected: `INTEL_MCP_SECRET written to .env (value not shown)`. Rotating it (`--rotate`) invalidates every token.

## Tokens

```bash
uv run intel mcp token issue --client langchain --profile writer --ttl 30d --write-env INTEL_MCP_TOKEN_LANGCHAIN
```
- The token goes straight into `.env`; the command prints only its id (`jti`).
- `--max-sensitivity S1` keeps review and creator text (S2) away from a client, for example one whose model runs
  at a provider you'd rather not send it to.
- **Revoke** by id: `uv run intel mcp token revoke <jti>`. Servers notice within a minute, no restart.
- **Claude Code's helper** mints 5-minute tokens on each connection (`intel mcp token header`). It refuses to run
  in a terminal, so a token never lands in a shell history.

## Run the HTTP server

```bash
uv run intel mcp serve --transport http                  # local clients: http://127.0.0.1:8766/mcp/<profile>/
uv run intel mcp serve --transport http --listener both  # + 127.0.0.1:8767 for tailscale serve
```
`GET /healthz` returns `{"status": "ok"}` and nothing else.

## Reach from your other devices (tailnet only, never public)

1. In `.env`, set (names only shown here):
   - `INTEL_MCP_OWNER_LOGINS`: your Tailscale login, e.g. `you@example.com`;
   - `INTEL_MCP_ALLOWED_HOSTS`: this Mac's tailnet name, e.g. `your-mac.your-tailnet.ts.net`.
2. Start the server with `--listener both`.
3. Forward HTTPS on the tailnet to the tailnet listener:
   ```bash
   tailscale serve --bg --https=8443 http://127.0.0.1:8767
   ```
   Expected: `Available within your tailnet: https://<mac>.<tailnet>.ts.net:8443/`.
4. From another device: `https://<mac>.<tailnet>.ts.net:8443/mcp/<profile>/` with a token. The owner profile also
   needs your Tailscale login, which `tailscale serve` adds and which the server trusts **only** on that listener.
5. **Never** use `tailscale funnel` (public internet). Stop with `tailscale serve --https=8443 off`.
