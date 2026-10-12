# Lab: an agentic vertical slice

A small, readable multi-agent system, built to show how the pieces fit together before the real one is designed.
It uses one orchestrator, three kinds of agent and two MCP servers, and has a console where you can watch a run and
start one yourself. **Read-only on everything real.** The only write goes to the local `proposals.json`. Nothing
here is deployed: the production image copies only `src/`.

```
            ┌───────────── Orchestrator ─────────────┐   run.py (code)  or  Claude Code + /plan-week (LLM)
            │                                        │
   planner ─┴─► helper-1 ┐                            │
   (1 agent)    helper-2 ├─ in parallel ─► writer-lead ─► report (runs/<id>.md)
                helper-3 ┘                 (1 agent)
                (3 writers)
      │             │                          │
      ▼             ▼                          ▼
 dk_intel        dk_intel                 dk_intel (writer) + demo_publisher.propose_post
 (planner        (writer profile)
  profile)       evaluate_drafts, get_facts, brand_rules
 + demo_publisher.list_slots
```

## The pieces (each file in one sentence)
| File | What it is |
|---|---|
| `demo_publisher/server.py` | **MCP server #2**, a toy "second product" with `list_slots` (reads `calendar.json`) and `propose_post` (appends to `proposals.json`). It shows how any product exposes tools. |
| `.mcp.json` | **How servers connect:** three stdio commands. dk_intel appears twice, once per profile, so **the server** decides what each role may call. |
| `.claude/agents/*.md` | **The agents:** front matter (`tools:` allowlist, `model:`) and a prompt. `planner`, `writer` and `writer-lead`. |
| `run.py` | **The orchestrator as code:** planner → 3 writers in parallel (threads) → writer-lead → report. Each agent is one headless `claude -p` process with only its own MCP tools and **no built-in tools** (`--tools ""`: no shell, web or files). Every event goes to `runs/<id>.jsonl`. |
| `CLAUDE.md` + `.claude/commands/plan-week.md` | **The orchestrator as an LLM:** the same agents, driven by Claude Code itself. |
| `console.py` + `console.html` | **The Agent Console:** a live graph, click-for-details, an event feed, run history, dk_intel's audit, and a Run button. |
| `trace.py` | The same run as text in the terminal, plus dk_intel's own audit record. |
| `testing/fake_claude.py` | A stand-in `claude` for the tests: the same event stream, no model. |

## Run it
Once only: the headless `claude` must be signed in. Run `claude` in a terminal, then `/login`, then quit.

**With the console (recommended):**
```bash
uv run python lab/agentic-slice/console.py
```
Open http://127.0.0.1:8770 (this Mac only), pick a platform and press **▶ Run now**. The boxes go
waiting → running → done. The three helpers run at the same moment, and the dashed server boxes flash on each MCP
call. Click any box to see its prompt, tools, every call and result, its tokens and its answer. A run takes about
2–4 minutes and is billed to your Claude subscription (no API key).

**From the terminal:**
```bash
uv run python lab/agentic-slice/run.py --platform instagram
```
```bash
uv run python lab/agentic-slice/trace.py
```

**The LLM as orchestrator (to compare):** start `claude` inside `lab/agentic-slice/`. `/mcp` should show 3 servers
and `/agents` should show 3 agents. Then type `/plan-week instagram`.

## On a schedule
The same engine with `--trigger schedule`, so history shows which runs came from the schedule. For Mondays at 09:00
(the Mac must be awake), run `crontab -e` and add:
```
0 9 * * 1 cd "/Users/shadman.arko/Documents/Work_DoNotTouch/Dhaka Kacchi/dk-intelligence" && mkdir -p lab/agentic-slice/runs && PATH=/opt/homebrew/bin:$HOME/.local/bin:$PATH uv run python lab/agentic-slice/run.py --trigger schedule >> lab/agentic-slice/runs/cron.log 2>&1
```
Only one run happens at a time (`runs/.running`). If the button and the schedule collide, the second one exits with
"a run is already in progress".

## Two real runs (2026-10-12, Sonnet, on the subscription)
| Run | Trigger | Wall time | Agents | Tool calls | Tokens | Proposed / skipped |
|---|---|---|---|---|---|---|
| instagram | terminal | 71 s | 5 | 15 | 377k | 2 / 1 (s3: still `rewrite` after 2 rewrites, 0.46) |
| facebook | Run button | 68 s | 5 | 16 | 420k | 1 / 2 (both still `rewrite`) |

- The three helpers ran side by side, for 20 s, 17 s and 38 s. One after another they would have taken 75 s instead
  of 38 s.
- dk_intel's audit chain recorded the same MCP calls as the engine.
- Twice an agent tried a tool outside its allowlist: a writer tried `search_posts`, and the writer-lead tried
  `list_slots`. Claude Code refused both (shown as ERROR in the feed), and the agents carried on without them.
- The gate held: nothing with verdict `rewrite` was proposed.
- Found on the first real run: with `--tools ""` the agent saw **no** tools, because MCP tools are deferred behind
  ToolSearch, which is a built-in. The engine now sets `ENABLE_TOOL_SEARCH=false`, and a test pins it.

## What to look at (the lessons for the real design)
1. **The boundary lives in the server, not in the prompt.** The planner connects with dk_intel's `planner` profile.
   Asked to call `evaluate_drafts`, it is refused with `NOT_ALLOWED_FOR_PROFILE`.
2. **Agents get tools, not a computer:** no shell, no web, no file writes; MCP tools only.
3. **Small JSON between agents** (slot ids, citation ids), never long texts. That keeps each context small.
4. **Orchestrator as code vs. as an LLM.** `run.py` is deterministic, testable and cheap to reason about.
   `/plan-week` is flexible but can vary from run to run. The real system will likely use code for the workflow and
   LLMs inside the steps.
5. **Failures stay local.** If one helper fails, its box turns red and the run still finishes with the others
   (tested).

## Adding product #3 (the pattern)
1. Its MCP server: one entry in `SERVERS` in `run.py` (and in `.mcp.json` for interactive use).
2. One agent file in `.claude/agents/` that lists `mcp__<server>__<tool>` in `tools:`.
3. One step in `orchestrate()` that calls that agent and passes its JSON on.
