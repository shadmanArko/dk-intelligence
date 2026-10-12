# Phase 4 · P4.4: dk_studio, content prompts, token budgets and the Sunday pack step (2026-10-11)

Any MCP client can now plan content and judge drafts in a handful of calls, through versioned prompts. Decision:
[ADR 0028](../adr/0028-generation-in-the-client.md) (generation stays in the client; €0 platform).

## What was built
| Part | Where | What it does |
|---|---|---|
| `grounding_pack` | `capabilities/studio.py`, `servers/studio.py` | one call: occasions with tone and respectful days, posting times, competitor gaps with a cited quote (≤140 characters, untrusted), our best posts by relative performance, what lifts engagement (Instagram and Facebook borrow TikTok's market data), the hidden patterns (`opportunities` from `patterns.py`), gap areas and brand reminders. Parts run in parallel; a broken source lands in `unavailable` and the rest returns |
| `evaluate_drafts` | same | brand checks + content scorer in one call; verdict **blocked** > **rewrite** > **keep**; blocking fixes first; a missing score (platform without a scorer, no time, scorer down) never means rewrite |
| 8 prompts | `mcp_server/prompts/*.md` | `content_plan`, `caption_ideas`, `image_ideas` (real photos only), `video_ideas` (original sound or the platform's library), and `role_planner`, `role_writer`, `role_producer`, `role_checker` (brand card rendered in). Front matter: version, args, `tools_used`, `max_tool_calls`; one shared rules block |
| Prompt lint | `tests/test_mcp_studio.py` | every tool a prompt uses exists and is held by every profile that sees the prompt; no blocked words; ≤6,000 characters; single-token args; the rules block is always in |
| Token budgets | `budgets.yaml`, `BudgetMiddleware` | a cap per tool (bytes ÷ 3); an over-budget result loses items from its longest list and says so (valid JSON, never cut mid-way); FastMCP's 32 KB limit as the backstop; budgets only fall |
| Case-insensitive enums | `NormaliseArgs` middleware | "TikTok" and "Instagram " are accepted (the eval saw 11 refusals and a retry each) |
| The Sunday pack step | `rag/lifecycle.py`, `pipeline.py`, `weekly.py` | a soft `knowledge` step after packaging: build from this week's pull and snapshot (cached embeddings), load staged, gate, activate or keep last week's. Shown in the Control Room and VPS Training diagrams; its failure never fails the model run. `intel pack load` uses the same code |
| Tool-use eval | `mcp_server/tool_eval.py`, `evals/tools/tasks.jsonl` | 20 dev + 6 held-out tasks, Ollama `gpt-oss:20b` on the Mac (€0) driving the real gateway through Ollama's native tool calling: success, calls, tokens and tool errors per task |
| Checker fixes found by using it | `checks.py` | the German-first check missed common words (*warum, weil, oder, ohne, uns…*) and flagged correct captions; widened, with tests ("was" deliberately not added: it's English too) |

## Measured
### A 14-day Instagram plan from Claude Code ([the plan](P4_4_instagram_plan_2026-10-11.md))
- 6 tool calls in total; 14 posts with sources; both respectful days quiet; only `confirmed` facts used.
- **100% of kept captions pass the checks.** Round 1: 12 keep, 2 rewrite, 0 blocked.
- **Mean content-scorer probability 0.565 → 0.589 after the loop.** The gain came from moving two carousels at
  11:30 to reels at 17:00. Rewriting their captions moved nothing (0.394 → 0.382).

### The scorer barely reads the caption (an important finding)
Three different Instagram captions in the same slot scored **exactly 0.567**, and three Facebook ones 0.557. The
content scorer ranks *when and in what format*, not *what is written*. Consequences:
- the rewrite loop is really a timing and format advisor; the brand checks do the caption work;
- the 2-round cap and the owner's review stay essential (ADR 0028, Goodhart);
- the next content-scorer retrain should be judged on whether caption features earn any weight. That's a model
  question for the weekly judge, not something to tune here (rule 9).

### Tokens per task
| | Calls | Result tokens (est.) |
|---|---|---|
| The grounding for a plan, primitive tools | 8 | 5,485 |
| The same with `grounding_pack` | 1 | 1,721 (**−69%**) |

Plus 7 fewer model turns, each of which would resend the whole conversation. The full tool list costs ≈5.5k
tokens per conversation for the owner profile (18 tools); the role profiles see 3–11 tools.

### Tool-use eval (gpt-oss:20b, local, temperature 0)
| Run | dev success (20) | held out (6) | calls / task | tokens / task | tool errors |
|---|---|---|---|---|---|
| 1: before the fixes | **90%** | 100% | 2.15 | 9,259 | 18 dev + 4 held out |
| 2: after the fixes | 90% as scored → **100%** with the corrected judge | 83% → **100%** | **1.5** | **7,706** (−17%) | 10 dev + 3 held out |

- **Floor ≥80% met in both runs.**
- Run 1's two misses were contract problems:
  - `district` had no description, so "Neukölln" went into `area`;
  - "everything I need to plan Instagram posts" chose `upcoming_occasions`, because `grounding_pack` didn't say it
    replaces those calls.
- Most tool errors were capitalised enums ("TikTok"), now accepted.
- The fixes were made on what the dev tasks showed; the held-out tasks were never looked at to make them.
- **Run 2's three "misses" were the eval's own bug.** The model sent "TikTok", "Instagram" and "YouTube"; the
  server accepted them, but the judge compared arguments case-sensitively. The model's calls and answers were
  right. The judge now compares as the server does (a test guards it), and run 2 was re-judged from its saved calls.
- After the fixes the model picks the workflow tools by itself: `grounding_pack` for planning and `evaluate_drafts`
  for drafts (run 1 used neither). The remaining tool errors are mostly invalid filter values (e.g.
  `tag_type: "topic"`); the server's message names the valid ones and the model recovers in one retry.

### Ideas for the owner to rate
10 idea sets (4 caption, 3 photo, 3 video) are in `data/evals/idea_sets.html` (local). All 12 caption ideas pass
`evaluate_drafts`; 5 needed a rewrite (TikTok hashtags pushed the first line over 60 characters; two Banglish
community captions now open with a German line). **The owner's rating (2026-10-12): 4 of 5 for all 10 sets** (mean
4.0; no set below 4, none at 5; no notes). Good enough to use, not yet "I'd post this as is": the next round should aim
at 5s with the owner's own words and photos.

## Tests
`tests/test_mcp_studio.py`:
- `grounding_pack`: all parts present, and survives a broken intel-api and index;
- the verdict table (blocked, rewrite, keep, missing score, scorer down);
- the prompt lint, and prompts through the gateway with per-profile visibility;
- budgets for every tool, and a valid trimmed result;
- case-insensitive enums, and the eval's judge.

`tests/test_rag_posts.py`: the shared lifecycle activates a good pack and keeps a refused one staged, and the Sunday
step skips cleanly without a knowledge database. Plugin and diagram tests pass with the new box. Full suite green;
branch coverage 82.2%.

## Not done (on purpose)
- `content_brief` stays off (D2; the brief carries origin-derived targeting).
- Claude Code subagent files are not written into `.claude/` (the owner decides, D5); the role prompts are ready
  for them.
- The first live Sunday pack step runs on Sun 2026-10-18.
