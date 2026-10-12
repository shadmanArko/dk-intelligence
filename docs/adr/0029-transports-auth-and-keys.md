# 0029. Reach dk_intel over stdio or tailnet-only HTTP, with short-lived per-profile tokens

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
- The owner wants dk_intel usable "from anywhere":
  - Claude Code and Desktop now;
  - LangChain, OpenAI Agents SDK, Groq and Ollama agents later (Phase 5 agents run as separate processes).
- Constraints:
  - the three doors (ADR 0003) and no public exposure in Phase 4;
  - €0 (ADR 0012);
  - secrets only in `.env` (rule 4);
  - every client sees only its role's tools (ADR 0025).
- On stdio a profile is a command-line flag: least privilege for trusted local use, not a boundary.
- FastMCP 4.0.11 ships a JWT verifier (HS256 with a shared key, issuer, audience, required scopes) and Host/Origin
  protection for its HTTP app (spiked: right token in, wrong scope, wrong audience or no token refused).
- LangChain 1.4's `langchain.mcp.MCPAdapter` is built on FastMCP (pinned 1.4.3: the newest release older than 7 days).

## Decision
1. **Two transports.** stdio for the owner's own clients. Stateless Streamable HTTP (JSON responses) for everything
   else, with **one path per profile**: `/mcp/{profile}/`.
2. **Tokens:**
   - format: HS256 JWTs signed with a key derived from the host's `INTEL_MCP_SECRET`, never the secret itself;
   - claims: `iss=dk-intelligence`, `aud=dk_intel@<host>`, `sub`, `scope=<profile>`, `dk_max_sensitivity`, `exp`,
     `jti`;
   - a path accepts only its own scope.
   - **Lifetimes:** 5 minutes for Claude Code's headers helper (minted per connection, refused on a terminal);
     at most 90 days for agents.
   - **Storage:** issued straight into `.env`, never printed.
   - **Revocation:** by `jti` (a file re-read every minute), or by rotating the secret.
3. **Sensitivity ceiling.**
   - A token can carry `dk_max_sensitivity` (default S2, as the owner allowed).
   - Tools labelled above the ceiling are hidden and refused, like the profile gate. Unlabelled tools fail closed.
4. **Two loopback listeners, tailnet only:**
   - **8766 for local clients:** the `Tailscale-User-Login` header is stripped, so it can't be forged.
   - **8767, the only target of `tailscale serve`:** there the owner profile also needs a login listed in
     `INTEL_MCP_OWNER_LOGINS`.
   - Host and Origin are checked against an allowlist.
   - `/healthz` says only `ok`.
   - **Never `tailscale funnel`.** Public access would need OAuth 2.1 and a new ADR on the three doors.
5. **The audit records the token's subject and `jti` prefix** as the principal on HTTP (the OS user on stdio).
6. **One contract over every transport, tested in CI:**
   - in memory, over HTTP, and through LangChain's `MCPAdapter`: the same tool list, and a tool call with no LLM;
   - `docs/mcp/CATALOG.md` is generated from the code and checked for staleness.
7. **Client libraries stay out of the server.**
   - LangChain, `langchain-ollama` and `openai-agents` live in an opt-in `clients` group;
   - the examples run on a local model (Ollama) by default.

## Consequences
- **Good:**
  - Phase 5 agents can connect today with a role token, and see only that role's tools.
  - The owner's daily stdio setup is unchanged.
  - Tokens are short-lived or revocable, and none is printed.
- **Cost / risk:**
  - **One shared HS256 key per host.** Anyone with `.env` can mint tokens. That is acceptable on the owner's
    single-user Mac; the VPS gets its own secret (P4.7).
  - The tailnet listener trusts a header set by `tailscale serve`. That is why it binds loopback only and the
    header is stripped on the other listener.
  - The `clients` group adds about 40 packages to CI and moves `websockets` to 16.1.1 for everyone.

## Alternatives considered
- **FastMCP's static-token verifier:** documented as development only; long-lived tokens in config files.
- **OAuth 2.1 with an authorization server:** right for public access, unnecessary inside a tailnet with one human
  user; deferred until anything is public.
- **`langchain-mcp-adapters`:** pins `mcp<2` and can't share an environment with FastMCP 4.
- **One listener trusting the Tailscale header everywhere:** any local process could forge the owner's login.
