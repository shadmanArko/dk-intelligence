# Phase 4 · P4.5: connect anywhere: HTTP, tokens, LangChain and example clients (2026-10-12)

dk_intel now serves every role profile over Streamable HTTP with a signed token per call, next to stdio. LangChain
and the OpenAI Agents SDK connect with a role token and run on a free local model. Decision:
[ADR 0029](../adr/0029-transports-auth-and-keys.md). How to connect: [docs/mcp/CLIENTS.md](../mcp/CLIENTS.md).

## What was built
| Part | Where | What it does |
|---|---|---|
| HTTP app | `mcp_server/http.py` | one Starlette app, `/mcp/{profile}/` per profile (stateless, JSON), `/healthz`; two loopback listeners (local 8766, tailnet 8767); Host/Origin allowlist; the `Tailscale-User-Login` header stripped locally and required for the owner on the tailnet listener |
| Tokens | `mcp_server/auth.py` | HS256 JWTs with a purpose-derived key; `iss`, `aud=dk_intel@<host>`, `sub`, `scope=<profile>`, `dk_max_sensitivity`, `exp`, `jti`; FastMCP's `JWTVerifier` per path (scope must equal the path's profile) + a revocation file re-read every minute |
| Sensitivity ceiling | `SensitivityCeiling` middleware | tools above a token's ceiling are hidden and refused; unlabelled tools fail closed |
| Audit | `AuditMiddleware` | on HTTP the principal is the token's subject and `jti` prefix |
| CLI | `intel mcp serve --transport http [--listener local\|tailnet\|both]`, `init --write-env`, `token issue\|header\|revoke`, `config`, `catalog` | secrets and tokens go into `.env` only; `token header` (Claude Code's helper) refuses a terminal |
| Catalog | `docs/mcp/CATALOG.md` (generated) | profiles × tools × data class × budget; a test fails when it is stale |
| Clients | `examples/clients/langchain_agent.py`, `openai_agents_ollama.py` | a writer agent on Ollama `gpt-oss:20b` (€0); the Agents SDK file also runs on Groq's free tier |
| Dependencies | `clients` group (opt-in, installed in CI) | `langchain[mcp]==1.4.3` (newest release ≥7 days old), `langchain-ollama==1.1.0`, `openai-agents==0.23.1`. LangChain's `mcp` extra builds on FastMCP ≥4.0.1, so it shares our environment (`langchain-mcp-adapters` couldn't) |

## Spikes (start of P4.5)
- `JWTVerifier(public_key=<shared key>, algorithm="HS256", issuer, audience, required_scopes=[profile])` works for
  per-path scopes. Right token: through. Wrong scope, wrong audience or no token: refused with 401.
- Several profile gateways mount under one Starlette app if their lifespans are entered together.
- `langchain.mcp.MCPAdapter` exists as designed (beta warning on import); it wraps tools, not prompts.

## Measured
### The contract, in CI (`tests/test_mcp_http.py`, a real uvicorn server on a free port)
| Check | Result |
|---|---|
| A writer token lists exactly the writer's 7 tools; `find_creators` is hidden **and** refused | pass |
| A writer token on `/mcp/owner/`; no token | refused |
| Expired, another host's audience, another secret, revoked `jti` | 401 each |
| `Host: evil.example` | 421 |
| Owner on the tailnet listener without, or with a wrong, Tailscale login | refused; with the owner's login: through |
| A forged login header on the local listener | stripped; the token alone decides |
| An analyst token with ceiling S1 | `search_reviews` hidden and refused, `funnel_status` visible |
| Same tool list in memory, over HTTP and through LangChain's `MCPAdapter`, plus a tool call through LangChain with no LLM | identical; the call returns |

### A live LangChain agent (local `gpt-oss:20b`, writer token, HTTP)
- Task: "Write two Instagram captions about our Borhani drink for Saturday, and check them."
- It called `brand_rules` 3× and `evaluate_drafts` 3× (a rewrite loop) and returned two German-first captions.
- **Our own re-check:** both still at verdict *rewrite*. They had 1 hashtag where the playbook wants 3–5; the
  role prompt forbids only *blocked* drafts, so the model stopped there.
- Claude, on the same tools in P4.4, brought all 14 drafts to *keep*. Output quality is the client model's; the
  server's checks make it visible either way (ADR 0028).
- The re-check found one more German-detection false positive ("Geheimnis **aus** Kacchi"). The word list is
  widened, with a test.

### Token cost per role (from the catalog)
| Profile | Tools | Tool list (tokens, est.) |
|---|---|---|
| owner | 18 | 7,630 |
| analyst | 11 | 4,181 |
| planner | 10 | 3,773 |
| writer | 7 | 2,962 |
| checker | 6 | 2,452 |
| producer | 4 | 1,585 |

A writer agent pays 39% of the owner's tool-list cost on every turn.

## Not done yet (needs the owner)
- `uv run intel mcp init --write-env` (the signing secret; it's written into `.env` and never shown). The tests
  and the live demo used throwaway secrets that never left the process.
- **Tailnet reach:** `tailscale serve` on the Mac, then a call from another device (steps in `docs/mcp/CLIENTS.md`).
  That is the last "done when" of P4.5.
- The Groq run: only if the owner adds a free-tier `GROQ_API_KEY`.
