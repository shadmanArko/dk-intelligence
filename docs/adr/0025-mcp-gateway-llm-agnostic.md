# 0025. Expose the models through one LLM-agnostic MCP gateway of four read-only servers

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
- **What exists:** seven models and an MLOps platform (Phases 1–3). Only people use them today, through web pages and
  Telegram.
- **What the owner wants:** an agentic marketing system. Any AI should be able to use the models: Claude Code now;
  LangChain, OpenAI Agents SDK, Groq or Ollama agents later; harness agents in Phase 5.
- **Requirements:** cheap in tokens, safe, easy to maintain, and connectable anywhere.
- **The ecosystem moves fast:**
  - The MCP spec of 2026-07-28 made the protocol stateless and deprecated sampling.
  - FastMCP 4 rebuilt on MCP Python SDK v2.
  - LangChain 1.4 moved its MCP client into `langchain.mcp` (beta).
- **Hard constraints:**
  - the three doors (ADR 0003);
  - €0 running cost and no paid AI APIs (ADR 0012);
  - read-only access to the warehouse;
  - rule 10 on origin data;
  - intel-api routes that write the ledger or change production must stay unreachable.

## Decision
1. **Protocol-first, LLM-agnostic.** Our MCP servers never call an LLM: no sampling, no paid API.
   - Clients bring their own model.
   - Generation (plans, captions, image and video ideas) happens in the client. It is guided by our versioned MCP
     prompts, grounded by our tools, and judged by our checks and the production scorer.
2. **One endpoint, four servers.**
   - `dk_intel` is a FastMCP gateway that mounts `dk_models`, `dk_evidence`, `dk_brand` and `dk_studio` in one
     process, **without namespaces**.
   - Each server also runs alone (`intel mcp serve --server …`).
   - Tool names are unique across all four servers, and a test enforces it: FastMCP keeps only one of two
     duplicate names, without an error.
3. **Read-only in Phase 4.**
   - Every tool is annotated `readOnlyHint=true` (and idempotent, closed-world). The server enforces the real
     limits itself.
   - intel-api is reached only through an explicit (method, path) allowlist, tested against every route the app
     has.
   - `FastMCP.from_fastapi` is never used.
   - Writes arrive in Phase 5 as a separate server (`dk_propose`): proposals into the approval queue, with
     idempotency keys and reversal tokens, and held only by deterministic code.
4. **Role profiles enforced by the server.** `profiles.yaml` defines six profiles: owner, analyst, planner, writer,
   producer, checker. One middleware filters every list (tools, resources, prompts) and refuses every call, read
   and get outside the profile.
   - Forced arguments are gateway `ToolTransform`s. Example: the checker's `get_sources` with `include_text`
     fixed to `false`.
   - Over HTTP, the profile comes from a signed token. On stdio, it is least privilege, not a security boundary.
5. **Layering.**
   1. A capability core of plain typed Python (Pydantic in and out, no MCP, HTTP or SQL imports).
   2. Ports (Protocols) and adapters, the only code that does I/O.
   3. FastMCP as a thin edge.

   One shared middleware kit holds errors, context, audit, auth, the profile gate, limits, metrics, cache, egress,
   the copy guard and the budget. It serves every server, including the Phase 5 write server.
6. **Versioning.**
   - `fastmcp` is pinned exactly: **4.0.11**, which brings `mcp` 2.3.0. It is an exception to the 7-day age rule
     because of its security fixes (transforms, visibility and auth now also apply to hashed tool lookups, which is
     exactly what the profile gate relies on).
   - Upgrades go only through `tests/test_fastmcp_assumptions.py` and the contract tests.
   - Tool contracts are additive only, like migrations (ADR 0015). A breaking change gets a new `…_v2` name and a
     deprecation window of at least 4 weeks.
   - We don't use FastMCP's own component versioning: it only works with FastMCP clients, which would break
     connect-anywhere.

## Consequences
- **Good:**
  - Any MCP client works, and the platform still costs €0.
  - The agent framework (LangChain/LangGraph, the Claude Agent SDK, the OpenAI Agents SDK) can be chosen or changed
    in Phase 5 without touching the tools.
  - A spec or SDK change touches only the edge.
  - CI tests the whole contract without an LLM or a network.
  - Each role sees a small tool list, so it spends fewer tokens and has less to misuse.
- **Cost / risk:**
  - Quality of generated content depends on the client's model. Mitigation: the deterministic checks and the
    production scorer run inside `evaluate_drafts`, and the tool-use eval measures small local models.
  - One process is one failure domain for all four servers. Acceptable at our scale; any server can be split out,
    because each runs alone.
  - The pin needs active upkeep. It is reviewed when 4.1.x is two weeks old (from Thu 10-15).

## Alternatives considered
- **Build agents first, with no shared tool layer.** Every agent would wrap the models in its own way, and the
  safety rules would be copied into each one.
- **LangChain inside the server or RAG engine.** `langchain.mcp` is client-side and in beta. It belongs where the
  Phase 5 agents run, not in the core.
- **Five separate server processes** (one per bounded context): five processes per Claude Code session, cross-server
  prompts broken, and no security gain over server-enforced profiles.
- **One flat server.** Fine for the protocol, but it loses the separately testable and deployable seams the owner
  asked for.
- **Server-side generation** (sampling, a local LLM in the server): breaks ADR 0012 or RAM limits, and the spec
  deprecated sampling.
- **`FastMCP.from_fastapi` auto-conversion:** it would expose `/v1/predict`, approvals and rollback.
