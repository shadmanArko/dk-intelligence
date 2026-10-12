# Phase 5 hand-over: what agents get from Phase 4, and what must be decided first

Phase 5 builds agents (planner → writer → checker, and later ads). Phase 4 leaves them a read-only, tested
contract. This page lists what is ready, what is missing, and the decisions only the owner can make.

## Ready to use
| For | What | Where |
|---|---|---|
| Every agent | one HTTP endpoint per role, a signed token per agent, a sensitivity ceiling | `/mcp/<role>/`, `intel mcp token issue`, [CLIENTS.md](CLIENTS.md) |
| Planner | `grounding_pack` (one call), occasions, competitor gaps, community gap areas, trends | profile `planner`; prompt `role_planner` |
| Writer | `grounding_pack`, `evaluate_drafts` (checks + score + fixes), brand rules, facts | profile `writer`; prompts `role_writer`, `caption_ideas` |
| Producer | shot ideas from our own posts only (forced) | profile `producer`; prompts `role_producer`, `image_ideas`, `video_ideas` |
| Checker | verdicts and fact checks without ever reading third-party text | profile `checker`; prompt `role_checker` |
| Cited answers | search over 13.6k reviews, 459 own posts, 2.2k creator posts, the brand book; `verify_citations` | profile `analyst`; prompt `cited_answer` |
| Measuring agents | the tool-use eval (any Ollama or OpenAI-compatible model) | `python -m intel.mcp_server.tool_eval` |
| Operating | audit chain, stats, metrics, kill switch, runbook | [runbook](../runbooks/mcp.md), [threat model](THREAT_MODEL.md) |

## Not built (by design: Phase 4 is read-only)
- **Any write.** Posting, ads, messages: a `dk_propose` server whose tools create *proposals* that a human approves
  (architecture §2.8). Contract fixed:
  - an idempotency key per proposal;
  - the server re-runs the checks and the scorer itself;
  - the autonomy tier comes from code, never from a prompt;
  - every draft-tier action needs the owner's approval;
  - a separate process and token scope;
  - never held by an LLM directly: deterministic orchestrator code submits after the checker passes.
- **The VPS deployment of dk_intel** (P4.7): needs the RAM measurement and the owner's copy-paste steps.
- **`content_brief`** (the data platform's deterministic brief): off until decision D2 is revisited (it carries
  origin-derived targeting fields, which are always stripped).

## Decisions only the owner can make (before Phase 5 code)
| # | Decision | Recommendation |
|---|---|---|
| D9 | How proposals reach the harness: (a) the harness owns a small proposal endpoint that writes `agent_action` itself, or (b) an INSERT-only role for dk-intelligence (amends rule 3 and ADR 0003) | **(a)**: rule 3 and the three doors stay exactly as they are |
| D10 | Which agent framework runs the roles (LangChain `create_agent`, the OpenAI Agents SDK, or Claude Code subagents first) | start with **Claude Code subagents** on the existing subscription (€0 extra), then the same roles on LangChain + Ollama for unattended runs |
| D11 | Which model runs unattended agents: local `gpt-oss:20b` (€0, slower, captions needed rewrites in P4.5) or a paid API (needs an ADR on rule 11) | local first; measure with the tool-use eval and the checker's verdicts before paying for anything |
| D5 | Write `.claude/agents/dk-{planner,writer,checker}.md` from the role prompts (no WebFetch, no Bash) | yes, as the first Phase 5 step |

## Known limits to design around
- **The content scorer ranks format and posting time, not caption wording** (P4.4). A writer agent can't improve
  a score by rewording, so don't let an agent loop on the score. The 2-round cap stays.
- **Search runs on CPU,** about 1.3 s per query (warm). Agents should search once per topic and pass citation ids
  between roles, not text.
- **Local models stop at "good enough".** The P4.5 writer agent returned captions still marked *rewrite*. The
  orchestrator, not the model, must enforce "nothing blocked or rewrite reaches the owner".
