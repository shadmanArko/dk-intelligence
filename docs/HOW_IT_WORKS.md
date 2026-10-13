# How it works: the agents, the MCP servers, Claude Code, RAG and the schedule

A guided tour for showing someone (an interviewer, a colleague) exactly where each thing is, in the order you would explain it.
Every path is relative to this repo (`dk-intelligence`) unless it says otherwise. Every claim below can be shown live on the
console (`uv run python -m intel.studio.console`, then **Agent system**, **ML pipeline**, **Demo**).

## The 60-second picture

```
 launchd (macOS) ── every 30 min ──► intel studio run --if-due          the SCHEDULE (not Claude Code's cron)
                                       │ decides: finish a cut-off run, start a new one, or do nothing
                                       ▼
                      ┌─────────────────────────── ORCHESTRATOR: plain Python (studio/run.py) ───────────────────────────┐
                      │ inputs → strategist → director → production (per idea: copywriter → adapter → designer) → handover │
                      │   │          │            │                    │                                       │            │
                      │   │          ▼            ▼                    ▼                                       ▼            │
                      │   │      AGENT call   AGENT call          AGENT calls                          code → Publisher API │
                      │   │   (one `claude -p` process each, its own prompt, its own tools, its own model)  (drafts only)   │
                      │   ▼                                                                                                  │
                      │ code calls MCP servers directly: 0 tokens                                                            │
                      └──────────────────────────────────────────────────────────────────────────────────────────────────────┘
                              │ stdio                                   │ stdio
                              ▼                                         ▼
               dk_intel (this repo, Python)                  dk_reels (dhaka-kacchi-reels, TypeScript)
               planner / writer profiles                      creative / curator profiles
               brand rules, facts, scorers, RAG search        templates, footage search, music, preview, render
                              │
                              ▼
               KNOWLEDGE PACK (RAG): brand files + 16k chunks of reviews, our posts, creator posts, pgvector index
```

**The one idea to say first:** the orchestrator is ordinary code; the agents only *judge*. An agent never decides what happens
next, never writes to the Calendar, and only sees the tools on its own list.

## 1. Where the agents are defined

An agent is **one Markdown file**: `src/intel/studio/agents/<name>.md`. The front matter is its configuration; the body is its
system prompt.

| Agent | File | Model | Tools it may use | Called |
|---|---|---|---|---|
| strategist | `src/intel/studio/agents/strategist.md` | sonnet | none (a pure judgment) | once per chunk of 10 ideas |
| director | `agents/director.md` | sonnet | none | once per day |
| copywriter | `agents/copywriter.md` | sonnet | `dk_intel_writer.evaluate_drafts` | once per idea |
| adapter | `agents/adapter.md` | sonnet | none | once per idea |
| designer | `agents/designer.md` | sonnet | `dk_reels_creative.search_assets`, `search_music`, `preview_spec` | once per idea |
| curator | `agents/curator.md` | sonnet | built-in `Read` only (it looks at contact sheets of the footage; the `dk_reels_curator` server is used by the code that makes the sheets) | on demand (footage knowledge) |
| analyst | `agents/analyst.md` | sonnet | none (it reads the playbook inside its task) | the weekly note |

Show: `cat src/intel/studio/agents/copywriter.md` (the first 6 lines are the whole configuration), then the **Agent system** page,
click **copywriter**: it lists the same tools, the model and the system prompt (**show**).

The code that reads the file is `agent_spec()` in `src/intel/studio/agents.py:28`. Which model each agent uses can be overridden
in `config/studio/models.yaml` or with `--model designer=haiku` (this is how the designer was tested on a cheaper model).

## 2. Where the orchestrator is

`src/intel/studio/run.py`, class `Studio` (line 64), method `_run` (line 152). Read the five `with self.phase(...)` blocks in
order: `inputs` (181), `strategist` (204), `director` (219), `production` (234), `handover` (238). That *is* the workflow.

| Piece | File | What it does |
|---|---|---|
| The phases | `studio/run.py` | runs the five phases; state saved after every step so a run can resume |
| One idea's crew | `studio/crew.py` (`Crew`, line 25) | copywriter → adapter → designer for one idea, with the code checks between them |
| Angles | `studio/plan.py` (`plan_angles`, 87) | the strategist's call, and `normalise` (63) which drops anything unsupported |
| The look | `studio/director.py` (`plan_treatments`, 158) | the director's call, then code enforces variety |
| The seams | `studio/ports.py` | four protocols: `ToolPort`, `RunnerPort`, `RendererPort`, `PublisherPort`. The orchestrator talks to nothing else |
| Settings as data | `config/studio/*.yaml` | plan, posting windows, routes, budgets, craft, schedule, models |
| Budget and safety | `studio/budget.py`, `studio/checks.py` | token cap per run and per idea, the brand check of every text |

Show: the **Agent system** page, the orchestrator box on the left with its five phases lighting up as a run goes (or **Demo**).

## 3. How an agent is connected to Claude Code and to MCP (the exact mechanism)

Each agent call is **one headless Claude Code process**: `src/intel/studio/agents.py`, class `ClaudeRunner` (line 64), method
`run` (line 79), the command at line 89. In words, flag by flag:

```
claude -p "<the task as JSON>"            run once, non-interactive, and print
  --output-format stream-json --verbose   stream every step so we can log it (the trace on the Agent system page)
  --mcp-config <file>.mcp.json            a config written for THIS call: only the MCP servers this agent's tools need
  --strict-mcp-config                     ignore every other MCP config on the machine
  --tools "<built-ins>"                   built-in tools allowed (almost always none)
  --system-prompt "<the agent file body>" the agent's instructions
  --allowedTools "mcp__dk_intel_writer__evaluate_drafts"   exactly its own tools
  --disallowedTools "<the rest of that server's tools>"    everything else on those servers is hidden from it
  --model sonnet   --max-turns 3          model and a turn limit from budgets.yaml
  --no-session-persistence                no memory between calls
(cwd = an empty temporary folder: no CLAUDE.md, no repo, nothing to read)
```

So "connecting an agent to an MCP server" is: list the tool in the agent's front matter (`tools: mcp__dk_intel_writer__evaluate_drafts`),
and `ClaudeRunner` writes a one-off `--mcp-config` containing only the server that tool belongs to. The server names and how
they start are in one place: `server_specs()` in `src/intel/studio/mcp.py:23`:

| Name | Starts | Lives in |
|---|---|---|
| `dk_intel_planner`, `dk_intel_writer` | `uv run intel mcp serve --profile planner / writer` | this repo: `src/intel/mcp_server/` |
| `dk_reels_creative`, `dk_reels_curator` | `npm run mcp -- --profile creative / curator` | the reels repo (TypeScript): `dhaka-kacchi-reels/src/mcp/server.ts` |

A **profile** decides which tools a client sees: `src/intel/mcp_server/profiles.yaml` (planner, writer, producer, checker...). The
checker profile, for example, has no tool that returns third-party text, so a hostile review cannot steer it.

The orchestrator's **own** calls to the same servers (no agent, no tokens) go through `McpHub` (`studio/mcp.py:36`), one persistent
stdio connection per server. Since ADR 0040 every one of those calls is recorded too: on a real one-day run, 17 MCP calls were made by
code and 6 by agents.

**Using the same servers interactively in Claude Code:** the repo has a committed `.mcp.json` (server `dk_intel`, profile `owner`).
Open Claude Code in this folder and the `mcp__dk_intel__*` tools are there (this is how `grounding_pack`, `evaluate_drafts`,
`get_facts` can be tried by hand). Other clients: `docs/mcp/CLIENTS.md`; every tool: `docs/mcp/CATALOG.md`; the design:
`docs/PHASE4_MCP_ARCHITECTURE.md`; the threat model: `docs/mcp/THREAT_MODEL.md`.

## 4. Where the agent system uses RAG

RAG here means: **retrieval from a knowledge pack, with citations, and code that checks them**. The agents do **not** call a
search tool themselves; the orchestrator retrieves once, the agents read cited evidence in their task. (Show this on the
**Agent system** page, section "Where the agents use RAG", which has the live numbers of the last run.)

1. **Build (weekly, ML run).** Step "Rebuild the knowledge pack" of the Sunday run (`src/intel/rag/lifecycle.py:124`,
   `weekly_refresh`): clean the text and mask personal data (emails, handles, links, phones), chunk it, embed it
   (`onnx:harrier-0.6b`, 1024 dimensions), run a quality gate, activate. Code: `src/intel/rag/` (`clean.py`, `chunk.py`,
   `embed.py`, `build.py`, `gate.py`). The pack on this Mac: collections `reviews` 13,589 · `creator_posts` 2,210 · `own_posts` 471 ·
   `brand` 9 · `rules` 10, plus the brand files and a trends table.
2. **Store and search.** Postgres + pgvector (`src/intel/rag/store.py`, `search` at line 232): hybrid search (vector similarity and
   words), then a cross-encoder reranker (`bge-reranker-v2-m3`, `src/intel/rag/rerank.py`). The MCP tools on top are in
   `src/intel/mcp_server/servers/evidence.py` (`search_posts`, `search_reviews`, `search_brand`, `get_sources`, `verify_citations`).
3. **Retrieve (the orchestrator).** In the **inputs** phase, `src/intel/studio/inputs.py:59` calls
   `dk_intel_planner.grounding_pack` once. The tool (`src/intel/capabilities/studio.py:115`) returns, in one answer: competitor
   gaps (from `reviews`), our best posts (from `own_posts`), opportunities, what works (trends), occasions, brand reminders. Every
   quote or post carries a citation id (`R-...` for a review, `P-...` for a post).
4. **Agents read.** The **strategist** gets the whole pack and writes each idea's angle with the citation ids it rests on
   (`sources`). The **copywriter** gets the text of exactly those citations plus our best past posts as voice examples
   (`studio/crew.py:63`); the **adapter** and the **designer** get the same cited evidence with the angle and the brand rules.
5. **Code checks.** `normalise` (`studio/plan.py:63`) keeps only citations that exist in the pack, so an invented source is dropped.
   Prices, hours and claims come only from `get_facts`; every caption goes through `evaluate_drafts` (blocked words, facts,
   format, the production scorer).

**Why the code retrieves instead of the agents:** retrieval is deterministic and costs no tokens; review and post text is
third-party and untrusted, so it reaches an agent only as quoted data with an id; and the citation check can be done in code
instead of trusting a model. Agents *could* be given `search_posts` (the writer profile has it); the studio chooses not to.

## 5. How it runs on time (this is not Claude Code's cron)

Claude Code has scheduling tools, and they are **not** what runs this. The schedule is a plain macOS **launchd** job on the owner's
Mac, so it does not depend on a Claude Code session being open.

- The job: `~/Library/LaunchAgents/com.dhakakacchi.studio.plist`, made by `plist_for()` (`src/intel/studio/schedule.py:172`) and
  installed with `uv run intel studio schedule install`. It runs **at login and every 30 minutes**:
  `uv run intel studio run --if-due --trigger schedule`, logging to `data/studio/schedule.log`.
- The check is cheap (one Calendar read, no AI) and **decides** (`decide()`, `schedule.py:149`): do nothing; **resume** a run that was
  cut off (a crash, a power loss, a closed lid); or start a new one, but only after 09:30, only when the studio's own planned posts
  are about to run out (fewer than 4 days ahead), nothing ran in the last 6 days, and fewer than 40 drafts wait for approval.
- A Mac that was off simply catches up at its first check after it is on. A run that is cut off is recognised (its log has no end
  and its lock names a dead process) and finished from where it stopped: finished calls are not made again.
- Nothing scheduled ever approves or posts: every run makes drafts that a person approves.
- The ML side has its own launchd job (`com.dhakakacchi.intel.weekly`, Sundays plus catch-up).

Show: `launchctl list | grep dhakakacchi`, `plutil -p ~/Library/LaunchAgents/com.dhakakacchi.studio.plist`,
`tail -3 data/studio/schedule.log`, `uv run intel studio schedule check` (it says what a check would do right now). Design:
ADR 0037 and ADR 0039; the measured proof (a real run killed mid-render and finished by the next check): `docs/results/S6b_catch_up_and_resume.md`.

## 6. A ten-minute demonstration

1. **Demo** page: the whole operation plays (ML then agents). Say the one idea (code orchestrates, agents judge).
2. **Agent system** page on the last real run: click **copywriter** → a call → what it was given, the tool it used, its answer.
3. Same page, **RAG** section: build → retrieve → agents read → code checks; point at "3 of 3 angles cite a source".
4. `cat src/intel/studio/agents/copywriter.md` and show the `claude -p` command in `studio/agents.py:89`.
5. **ML pipeline** page: the 15 steps and a model's window (candidates, new vs current, judge gates).
6. **Operations**: Run, Stop, Retrain one model. Then `launchctl list | grep dhakakacchi` for the schedule.

## 7. Questions you will be asked, and the honest answers

- **"Is this really agentic if the code decides the flow?"** Yes, deliberately: the judgments (what to say, how it should look) are
  agents; the flow, the checks, the budget and the publishing are code. That is why it can be tested, resumed and trusted.
- **"What stops an agent from doing something wrong?"** It has only its own tools (`--allowedTools`, the rest hidden), a turn limit,
  an empty working folder, no memory, and everything it writes is checked by code before it leaves; the only output is a draft a
  person approves.
- **"How do you know it works?"** Real runs, not demos: a 10-day run made 103 posts in 13 minutes (27 of 30 ideas, 3 held by the brand
  check); results with measured numbers are in `docs/results/`.
- **"How much does it cost?"** About 650,000 tokens per 10-day plan; capped at 4 million per run with a warning at 80%.
- **"What is not done?"** Auto-approval (deliberately off), TikTok API posting (waiting for API approval), the market-data refresh
  button, pausing a run.
