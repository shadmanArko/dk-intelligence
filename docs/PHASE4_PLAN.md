# dk-intelligence — Phase 4 plan: MCP + RAG, one tool layer every AI can use

Status: APPROVED 2026-10-09 · P4.0 started · the detailed design is
[PHASE4_MCP_ARCHITECTURE.md](PHASE4_MCP_ARCHITECTURE.md): **where the two differ, the architecture page wins**
(differences listed in its §8.6: milestone dates, ADR numbers, tool names)

## 1. Goal

Phases 1–3 built seven models and a production MLOps platform. Today only people use them, through web pages and
Telegram. Phase 4 lets **any AI** use them safely:
- Claude Code and Claude Desktop now;
- later, LangChain agents, OpenAI or Groq-based agents, ready-made agents and local models.

The marketing department this serves needs four things:
1. **Cited answers.** For example: "Why do biryani places in Wedding get 1-star reviews?", answered with the
   actual reviews as sources.
2. **Scoring.** "Score these 10 captions and say why", using the content scorer that already runs in production.
3. **A content-plan helper.** A 10–30-day plan per platform that uses occasions, competitors' complaints, our
   community areas, the funnel and what worked before.
4. **Idea generation.** Caption, image and video ideas per platform, grounded in the data, the brand, trends and
   hidden patterns. Weak drafts are scored, then rewritten.

**The principle: protocol-first and LLM-agnostic.**
- The MCP server **never calls an LLM**. It offers tools (actions), resources (reference text) and prompts
  (ready-made workflows). The client brings its own model.
- Ideas and plans are therefore written by whichever model is connected, guided by our prompts and grounded in
  our tools.
- **Why:**
  - the server costs €0, so ADR 0012 holds for the platform;
  - any client works, now and later;
  - the agent framework (LangChain/LangGraph, the Claude Agent SDK, the OpenAI Agents SDK) can be chosen or
    changed in Phase 5 without touching the tools.

Every milestone ends as in Phase 3:
- code and tests;
- an ADR;
- a results doc with measured numbers;
- an interview story.

## 2. What changed in the outside world (checked 2026-10-09)

| Fact | What it means for us |
|---|---|
| The MCP spec of **2026-07-28** is *stateless*: no opening handshake and no session id; each request carries its version and capabilities. Tasks and MCP Apps are extensions. Sampling, Roots and Logging are deprecated (12-month runway) | Our server is built stateless from the start. We don't use sampling: the server never asks the client's LLM for anything, which fits "the server never calls an LLM" |
| The Python MCP SDK **2.0** is a breaking change for that spec. **FastMCP** builds servers on top of it | Pin versions. Our tool logic never imports MCP, so the next spec change only touches a thin adapter |
| **LangChain 1.4** (2026-09-03) moved MCP into `langchain.mcp` (`MCPAdapter`, beta, built on FastMCP). It's client-side: it turns MCP tools into LangChain tools for `create_agent`. The old `langchain-mcp-adapters` (0.3.x) is for older LangChain versions | LangChain is how *Phase 5 agents* connect to our server. We prove compatibility now with a CI test (P4.5), without picking an LLM or an agent yet |

Sources:
- [MCP 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [LangChain MCP docs](https://docs.langchain.com/oss/python/langchain/mcp)
- [LangChain blog](https://langchain.com/blog/mcp-in-langchain-stateless-protocol-elicitation-and-more)

## 3. Architecture

```
 CLIENTS (bring their own LLM)
   Claude Code / Desktop ── stdio ─────────────┐
   LangChain agent ── langchain.mcp.MCPAdapter ┤
   OpenAI Agents SDK / Groq / Ollama agents ───┤ MCP (stateless; stdio or Streamable HTTP + token)
                                               ▼
 ┌─ src/intel/mcp_server/  (FastMCP, thin adapter) ─────────────────────────────────────────┐
 │  tools (allowlist) · resources (brand card, rules, model cards) · prompts (workflows)    │
 │  middleware: audit log · size and rate limits · blocked-words check · redaction · metrics│
 └──────────────────────────────────────┬───────────────────────────────────────────────────┘
                                        ▼
 ┌─ src/intel/capabilities/  (plain typed Python, Pydantic in/out, no MCP imports) ─────────┐
 │  score_drafts · get_insights · upcoming_occasions · competitor_complaints · community_   │
 │  areas · funnel_status · find_creators · monitoring_status · check_caption ·            │
 │  search_reviews · search_posts · trend_signals                                           │
 └──────────────┬─────────────────────────────────────┬─────────────────────────────────────┘
                ▼ ports (interfaces)                  ▼
   Mac adapter (now): intel-api over the tailnet      src/intel/rag/  (index builder + hybrid search)
   (allowlisted routes), door 1, mip_reader            pgvector + Postgres full-text search, RRF
   VPS adapter (P4.7): in-process, intel-db            local multilingual embedding model
```

### 3.1 Why three layers (the "best practice" part)
- **Ports and adapters (hexagonal).**
  - The capabilities don't know where the data lives. A Mac adapter reads through the existing doors; a VPS
    adapter (later) reads in-process.
  - Moving the server to the VPS becomes a deployment step, not a rewrite.
- **The protocol is a thin edge.** Specs and SDKs change fast (two big MCP revisions in a year). The edge is the
  only code that changes with them.
- **One place for safety.**
  - Allowlist, audit, limits, redaction and the blocked-words check live once, in the middleware.
  - Every client gets them. A per-agent framework would copy them into each agent.
- **Contract tests at every edge:**
  - capabilities: unit tests with fake adapters;
  - MCP: tool schemas are snapshot-tested, so a schema change is a visible diff;
  - LangChain: `MCPAdapter` loads every tool in memory.

### 3.2 Tools: read-only, allowlisted
- **Model tools:**
  - `score_drafts` (≤ 100 drafts → `POST /v1/score/batch`; never `/v1/predict`, which writes the ledger);
  - `get_insights(name)` (a **new** read-only JSON route `GET /v1/insights/{name}`; today bundles are rendered
    as HTML only);
  - `upcoming_occasions`, `competitor_complaints`, `community_areas`, `funnel_status`, `find_creators`,
    `monitoring_status`.
- **Brand and policy tools:**
  - `check_caption`: blocked words from the snapshot's `content_rules.yaml`, length per platform;
  - `brand_card` and `facts`: prices and hours from `facts.yaml`, never from retrieved text.
- **Evidence tools (RAG):**
  - `search_reviews`, `search_posts`, `trend_signals`;
  - filters: area, platform, language, date, rating;
  - every hit carries a citation: source table, id, URL, date.
- **Never exposed:** `/v1/predict`, `POST /try`, approvals, rollback, and any write to a harness database.
  - FastMCP can convert a whole FastAPI app automatically. **We don't**, because that would expose these routes.
  - A test fails if any non-allowlisted route becomes reachable.
- **Writes** come only in Phase 5, through the harness design: an idempotency key, a reversal token and the
  `agent_action` approval queue.

### 3.3 Prompts: ready-made workflows for the client's model
`cited_answer`, `content_plan`, `caption_ideas`, `image_ideas`, `video_ideas`; each takes the platform and a date
range. Each prompt tells the model:
1. which tools to call, in which order;
2. to use the brand card and the blocked words;
3. to cite its sources;
4. to **score its drafts and rewrite the ones below the threshold** (the self-improving loop agreed on
   2026-10-07).

A prompt is versioned text in git, so it can be reviewed, tested and improved like code.

### 3.4 RAG engine
- **Corpora, in order:**
  1. competitor reviews: the 13.7k in the market snapshot, then the ~236k with text from `core.review`;
  2. our 466 own posts;
  3. market and creator posts (2,210 creator posts, then a sample of `core.post`).

  Brand docs **don't** go into RAG: the brand card always sits in the prompt.
- **Build:**
  - from immutable pulls and market snapshots (rule 7);
  - chunked per review or post, keeping metadata;
  - embedded on the Mac;
  - the **index is a versioned artifact**, like a model: it records the pull id, the snapshot id, the embedding
    model and its hash, and the row counts.
- **Search:** hybrid, combining pgvector (dense) and Postgres full-text search (German + English) with
  reciprocal rank fusion, plus metadata filters. No GraphRAG.
- **Embedding model:** multilingual (85% of reviews are German), run locally, €0. We benchmark
  multilingual-e5-small against base on the golden set (P4.0). Small wins unless base is clearly better, because
  the query-time model must later fit the VPS (intel-api has 512 MB).
- **Where it lives:**
  - Phase 4: a local pgvector database on the Mac (Docker), which dk-intelligence owns;
  - P4.7: intel-db gets it through an additive migration, with the index shipped as a package over door 2;
  - **never** the data platform's Postgres: we only have the read-only `mip_reader` role there.

### 3.5 Data and privacy
- **The owner's decision (2026-10-09):** any connected client may see the data that lives on the VPS, i.e. the
  models and their data.
- **Default safeguard:** names and handles of *private* commenters and customers are redacted in tool output.
  This loses nothing useful for marketing and keeps us in line with GDPR. The owner can switch it off; the ADR
  records it.
- H10 isn't merged yet, so each new corpus's purpose and retention are declared here (§3.4). They move into
  `policy.yaml` when H10 lands.
- Rule 10 applies to everything generated: no religious ritual or deity words, no targeting by ethnicity or
  religion. The middleware checks outgoing text.

### 3.6 Reachability
- **Now:** the Mac (stdio, for Claude Code and Desktop) and the tailnet (Streamable HTTP plus a bearer token,
  from P4.5).
- **Later:** the VPS (P4.7).
- **Public internet** (claude.ai connectors, ChatGPT): not in Phase 4. It would need OAuth, rate limits and a new
  ADR changing the three-doors rule.
- No door is widened.

## 4. Milestones

### P4.0: Decisions and spikes
- **ADRs:**
  - 0025: MCP as the integration contract, with an LLM-agnostic server;
  - 0026: the RAG engine, with the index as a versioned artifact;
  - 0027: the data-egress and redaction policy;
  - 0028: generation lives in the client; the platform stays at €0.
- **Spikes, each with measured numbers:**
  - pin the MCP SDK and FastMCP versions, and check that `langchain.mcp` 1.4 loads a FastMCP server in memory;
  - embedding speed and RAM on the Mac for e5-small vs e5-base.
- **Done when:** the ADRs are written and the versions are pinned, with the spike numbers in the results doc.

### P4.1: Capability core and the first MCP server (models)
- `src/intel/capabilities/` with the model tools, plus Pydantic schemas and fake adapters for the tests.
- A new read-only route `GET /v1/insights/{name}`, which returns the active bundle as JSON (additive, with a test).
- A stdio server (`uv run intel mcp serve`) and the `.mcp.json` entry for Claude Code.
- An append-only **tool-call audit log**: who, which tool, the arguments' hash, the duration and the result size.
- **Done when:** Claude Code answers "score these 10 captions and explain" and "what are next month's occasions",
  every call is logged, and the allowlist test passes.

### P4.2: Reviews RAG with citations
- The index builder (`intel rag build`), local pgvector, hybrid search, `search_reviews`, `competitor_complaints`
  with evidence.
- A **golden set**: 20+ questions the owner spot-checks, with the reviews that should be found.
- **Done when:** recall@10 and nDCG@10 are measured and reported, and the cited answer to the Wedding question
  passes the owner's spot check.

### P4.3: Posts, trends and hidden patterns
- Index our own posts and the market and creator posts.
- `search_posts`; `trend_signals` (topics rising week over week); patterns that combine the weakness miner, the
  community map and the funnel ("what competitors' customers miss that we do well").
- **Done when:** the golden set is extended to posts, and the numbers are reported.

### P4.4: Content workflows (prompts)
- The `content_plan`, `caption_ideas`, `image_ideas`, `video_ideas` and `cited_answer` prompts, per platform.
- The score-and-rewrite loop.
- `check_caption` runs on every draft.
- **Done when:**
  - a 14-day plan for one platform comes out of Claude Code with sources;
  - every caption passes `check_caption`;
  - the content scorer's average on the generated captions is reported;
  - the owner rates 10 idea sets (the score is recorded).

### P4.5: Connect anywhere
- Stateless Streamable HTTP with a bearer token, tailnet-only.
- A **CI contract test**: `langchain.mcp.MCPAdapter` loads every tool and calls one, in memory, with no LLM.
- Documented and tested example clients: a LangChain `create_agent` with a local Ollama model (€0), and configs
  for the OpenAI Agents SDK and Groq.
- **Done when:** the same tool contract passes over stdio, over HTTP and through LangChain.

### P4.6: Hardening
- Prometheus metrics per tool (calls, errors, p95), rate and size limits, and the golden-set evaluation in CI
  (it must not get worse: a ratchet).
- A results doc and an interview story.
- **Done when:** CI is green with the evaluation gate, and a Grafana panel shows tool usage.

### P4.7 (later): Move to the VPS
- Measure RAM on the VPS first (`free -h`).
- Then, either mount the HTTP MCP endpoint in intel-api or run a small separate container, plus the VPS adapter,
  the index shipped over door 2 and an additive intel-db migration.
- The owner runs the VPS commands.
- **Done when:** a tailnet client reaches the VPS server, and the smoke test and the SLO alerts cover it.

## 5. Order and size

| # | Milestone | Sessions | Depends on |
|---|---|---|---|
| P4.0 | Decisions + spikes | 1 | — |
| P4.1 | Capability core + model tools | 2 | P4.0 |
| P4.2 | Reviews RAG + golden set | 2 | P4.0 |
| P4.3 | Posts, trends, patterns | 1–2 | P4.2 |
| P4.4 | Content workflows | 1–2 | P4.1, P4.3 |
| P4.5 | HTTP + LangChain + example clients | 1 | P4.1 |
| P4.6 | Hardening | 1 | all above |
| P4.7 | VPS | 1 | P4.6, owner |

About 10–12 sessions. **Value arrives early:** after P4.1 you can already use the models from Claude Code.

## 6. Decisions in this plan (say if you disagree)
1. **The server never calls an LLM.** Generation happens in the client, so the platform stays at €0 and works with
   any model. Your client costs (Claude subscription, OpenAI, Groq) are your choice per client.
2. **FastMCP for the server, LangChain only on the client side.** No LangChain inside the server or the RAG
   engine: fewer moving parts and no beta API in the core.
3. **Read-only in Phase 4.** Writes (posting, ads, messages) wait for Phase 5 and the approval queue.
4. **RAG on pgvector, owned by dk-intelligence:** on the Mac first, then intel-db. Not the data platform's
   database, and not a hosted vector service.
5. **Redaction of private people's names and handles by default**, even though you allowed all VPS data.
6. **Not in Phase 4:** public internet exposure, GraphRAG, fine-tuning, and agents running on their own.

## 7. What I need from you

| When | What |
|---|---|
| Now | Approve or change this plan and the decisions in §6 |
| P4.2 | Spot-check the golden-set answers (about 30 minutes) |
| P4.4 | Rate 10 idea sets (caption, image, video) |
| P4.7 | On the VPS: `free -h`, then the copy-paste deploy steps |
