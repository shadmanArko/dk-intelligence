# 0028. Generate in the client: workflow tools, prompts as code, and a bounded score-and-rewrite loop

- **Status:** Accepted
- **Date:** 2026-10-11

## Context
- The owner wants content plans and caption, image and video ideas built from our data (P4.4).
- Constraints:
  - €0 running cost and no paid AI APIs (ADR 0012);
  - the platform must work with any client model: Claude Code now; LangChain, OpenAI, Groq and Ollama later
    (ADR 0025);
  - token cost matters to the owner ("spend very less amount of token").
- **Goodhart risk:** a writer that rewrites until the content scorer is happy can learn to please the scorer instead
  of customers.

## Decision
1. **The server never calls a language model.** Generation happens in the client, with the client's model and the
   client's cost. `dk_studio` returns data and judgements only.
2. **Workflow tools replace chains of primitive calls.**
   - `grounding_pack(platform, days)`: occasions, posting times, competitor gaps with a cited quote, our best
     posts, what lifts engagement, the hidden patterns and brand reminders. One call, parts fetched in parallel; a
     broken source is named in `unavailable`, never fatal.
   - `evaluate_drafts(drafts ≤20)`: brand checks plus the content scorer, one verdict per draft: **blocked** (any
     block rule) > **rewrite** (a warning, or a probability under the threshold) > **keep**. A missing score alone
     (no scorer for the platform, no posting time, scorer down) never means rewrite.
3. **Prompts are code.** `mcp_server/prompts/*.md` with front matter (name, version, args, `tools_used`,
   `max_tool_calls`), a shared rules block, rendered by the server and linted in CI:
   - every tool a prompt uses exists and is held by every profile that may see the prompt;
   - no blocked words, at most 6,000 characters, single-token arguments.
   - Workflow prompts: `content_plan`, `caption_ideas`, `image_ideas`, `video_ideas`.
   - Role prompts (Phase 5 agents and Claude Code subagents): `role_planner`, `role_writer`, `role_producer`,
     `role_checker`. The brand card is rendered into each.
4. **The loop is bounded.** At most 2 rewrite rounds. The scorer advises; it never decides alone, and nothing
   promotes a scorer automatically (rule 6). The shadow ledger keeps measuring posted captions' real outcomes.
   No tuning against any test set (rule 9).
5. **Token budgets are enforced.** `budgets.yaml` sets a cap per tool (estimated as bytes ÷ 3). An over-budget
   result loses items from its longest list and says so; FastMCP's 32 KB response limit is the last backstop.
   Budgets only fall unless an ADR says otherwise.
6. **The tool contract is measured with a free local model.** `python -m intel.mcp_server.tool_eval` drives the
   real gateway with Ollama `gpt-oss:20b` (26 tasks: 20 dev, 6 held out): success, calls and tokens per task. The
   floor is ≥80% on dev; below it, descriptions or prompts are fixed. LangChain joins in P4.5.

## Consequences
- **Good:**
  - €0 platform. Any client works, and the owner chooses its cost.
  - A 14-day Instagram plan takes 6 tool calls, and every kept caption passes the checks
    (`docs/results/P4_4_instagram_plan_2026-10-11.md`).
  - Prompts are reviewed like code, and their versions appear in every rendered prompt.
- **Cost / risk:**
  - Quality depends on the client's model; the tool-use eval makes that visible per model.
  - **The scorer reacts mostly to format and posting time.** In the first plan, caption rewrites moved nothing;
    a format change did. The loop can therefore push every post into one format. Mitigations: the playbook's
    cadence stays the writer's frame, the 2-round cap, and the owner's review.

## Alternatives considered
- **Generation inside the server with a hosted LLM:** breaks ADR 0012, ties every client to one model and one
  cost, and puts a model behind a read-only contract.
- **Only primitive tools:** every client would re-learn the same 5–6 call chain and pay for it in tokens.
- **An unbounded rewrite loop until "keep":** optimises the scorer, not sales (Goodhart).
