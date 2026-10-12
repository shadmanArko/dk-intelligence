# Lab: an agentic vertical slice

A small, readable multi-agent system, built to show how the pieces fit together before the real one is designed.
It uses one orchestrator, three kinds of agent and two MCP servers, and has a console where you can watch a run and
start one yourself. **By default it is read-only on everything real:** the only write goes to the local
`proposals.json`. With `--publish drafts` (below) it also creates *drafts* in the owner's publishing Calendar, never
anything that can post: there is no approve call here. Nothing is deployed: the production image copies only `src/`.

```
 CODE: fetch            AI: planner          AI: 3 writers, in parallel        CODE: re-check + propose
 list_slots      ──►   picks 3 slots   ──►  helper-1 ┐                  ──►   dk_intel evaluate_drafts (all, 1 call)
 grounding_pack        and angles           helper-2 ├ each: 3 captions,      demo_publisher propose_post (keep only)
 (+ screen examples)   (1 turn, no tools)   helper-3 ┘ 1 check, best wins     → report + proposals.json
```
**AI only where judgment is needed** (choosing angles, writing captions); **plain code for every fixed step**
(fetching, re-checking, proposing). Code calls the same MCP servers an agent would, and dk_intel's audit records
both, but those steps cost no tokens and can't be skipped or misreported.

## The pieces (each file in one sentence)
| File | What it is |
|---|---|
| `media.py`, `handover.py`, `.claude/agents/designer.md` | the media step and the hand-over to dk-publishing: see "Making the media" below |
| `demo_publisher/server.py` | **MCP server #2**, a toy "second product" with `list_slots` (reads `calendar.json`) and `propose_post` (appends to `proposals.json`). It shows how any product exposes tools. |
| `.mcp.json` | **How servers connect:** three stdio commands. dk_intel appears twice, once per profile, so **the server** decides what each role may call. |
| `.claude/agents/*.md` | **The agents:** front matter (`tools:` allowlist, `model:`) and a prompt. The engine uses `planner` and `writer`; `writer-lead` is for the interactive `/plan-week` only. |
| `run.py` | **The orchestrator as code:** fetch (code) → planner → 3 writers in parallel (threads) → re-check + propose (code) → report. Each agent is one headless `claude -p` process with only its own MCP tools and **no built-in tools** (`--tools ""`: no shell, web or files). Every event goes to `runs/<id>.jsonl`. |
| `eval.py` | **Measure, don't guess:** freeze one week's inputs, run any version on them, compare keep-rate, scores, tokens and time. |
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
Open http://127.0.0.1:8770 (this Mac only), pick a platform and press **▶ Run now**. At the top, **What it produced** shows
one card per post: when and where, the planner's idea, the writer's caption, dk_intel's check (chance to beat our
usual engagement) and whether it was proposed or skipped, and why. Below it, **How it got there**: The boxes go
waiting → running → done. The three helpers run at the same moment, and the dashed server boxes flash on each MCP
call. Click any box to see its prompt, tools, every call and result, its tokens and its answer. A run takes about
about a minute and ~25k tokens and is billed to your Claude subscription (no API key).

**From the terminal:**
```bash
uv run python lab/agentic-slice/run.py --platform instagram
```
```bash
uv run python lab/agentic-slice/trace.py
```

**The LLM as orchestrator (to compare):** start `claude` inside `lab/agentic-slice/`. `/mcp` should show 3 servers
and `/agents` should show 3 agents. Then type `/plan-week instagram`.

## Making the media (M8): from a kept caption to a draft in the Calendar
```
 … 3 writers (AI) ─ each caption that passed ─►  designer (AI, 3 read-only tools, preview only)
                                                          │ the creative part of a ContentSpec
 CODE: finalize (id, format, slot, captions, avoid-list are set by code) → dry-run → brand check of every on-screen word
       (dk_intel) → render, one at a time (reels engine CLI) → handover.py: upload once, dry-run batch, real batch → Calendar
```
```bash
uv run python lab/agentic-slice/run.py --publish render     # design + check + render. Nothing is sent anywhere, no key needed
uv run python lab/agentic-slice/run.py --publish dry-run    # + validate with dk-publishing, creating nothing
uv run python lab/agentic-slice/run.py --publish drafts     # + create drafts; the owner approves in the Calendar tab
uv run python lab/agentic-slice/run.py --publish drafts --suffix r2   # redo a slot whose post was rejected (new key)
uv run python lab/agentic-slice/handover.py --manifest <manifest.json> --dry-run   # hand over renders by hand
uv run python lab/agentic-slice/handover.py --status        # what was approved, published, failed
```
Needs the reels engine as a sibling folder (`../dhaka-kacchi-reels`, or `DK_REELS_DIR`). The publishing modes need
`DK_PUBLISH_URL` and `DK_PUBLISH_KEY` in `.env` (a key with the **draft** right only; never printed or logged).

| File | What it is |
|---|---|
| `.claude/agents/designer.md` | the **designer**: three `dk_reels` tools (`search_assets`, `search_music`, `preview_spec`), no render, no shell |
| `media.py` | what code decides around the designer: job ids, the spec envelope, the footage ledger, the reels CLI contract |
| `handover.py` | manifest → drafts: plans variants per platform, checks them against the live `GET /v1/spec`, holds what can't go out (with a reason), uploads once, one dry-run-then-real batch |
| `handover.json` | `reviewed_languages` (the owner adds a language once a native speaker has checked it) and conservative option defaults (TikTok privacy `only_me`) |

**Rules it keeps** (each is a test): drafts only; idempotent (`post_key` = job id; a slot that already has a post is
skipped before any AI is spent); nothing dropped silently (a held post says why); on-screen text that fails dk_intel's
brand check is never rendered; text in a language nobody has checked is held; an agent can never render.

**Measured, real runs on the frozen week** (`--publish render`, Instagram; details in
[docs/results/M8_media_pipeline.md](../../docs/results/M8_media_pipeline.md)):

| Version | Runs | Posts finished | Tokens per run | Time |
|---|---|---|---|---|
| v6 captions only (no media), for reference | 3 | 6 of 9: every post that can pass | 21k | 35 s |
| v7 + designer + checks + renders, run A | 1 | 1 of 2 (the other lost to a loudness bug, since fixed) | 40.7k | 101 s |
| v7, run B | 1 | **2 of 2** | 48.1k | 117 s |

About 24k tokens and 58 s per finished post. Not yet run against the live publisher (no draft key): the bridge is tested
against a fake that enforces the contract's rules.

## On a schedule
The same engine with `--trigger schedule`, so history shows which runs came from the schedule. For Mondays at 09:00
(the Mac must be awake), run `crontab -e` and add:
```
0 9 * * 1 cd "/Users/shadman.arko/Documents/Work_DoNotTouch/Dhaka Kacchi/dk-intelligence" && mkdir -p lab/agentic-slice/runs && PATH=/opt/homebrew/bin:$HOME/.local/bin:$PATH uv run python lab/agentic-slice/run.py --trigger schedule >> lab/agentic-slice/runs/cron.log 2>&1
```
Only one run happens at a time (`runs/.running`). If the button and the schedule collide, the second one exits with
"a run is already in progress".

## Measured: every version on the same week (2026-10-12, Instagram, real runs)
`eval.py` freezes one week's inputs (slots and grounding pack), so the versions are compared on the same data:

| Version | Runs | Posts proposed | Avg. score | Tokens per run | Time |
|---|---|---|---|---|---|
| v1 first (every agent a full Claude Code) | 1 | 2 of 3 | 55% | 377k | 71 s |
| v2 token-lean prompts and tools | 1 | 2 of 3 | 55% | 47k | 69 s |
| v3 code for fixed steps + evidence for writers | 2 | 4 of 6 | 55% | 37k | 48 s |
| v4 + slot pre-check, 1 caption per writer | 2 | 3 of 6 | 55% | 22k | 34 s |
| **v6 + 2 options per check, voice-only examples, id fix** | **3** | **6 of 9: every post that can pass** | **55%** | **21k** | **35 s** |

v1 and v2 used live inputs from the same day. A v5 run is left out: a bug in my code (below) voided it.

**What each step taught**
- **Where tokens go:** every turn re-sends the whole context. Claude Code's coding prompt (~9k), the repo's CLAUDE.md
  (~6k) and descriptions of tools an agent may not call (~3k) added up to ~20k per turn. Now it's ~2k (v2).
- **Code for fixed steps:** fetching, re-checking and proposing are plain code calling the same MCP tools. They cost
  0 tokens and can't be skipped (v3).
- **The content scorer reads a caption's shape, not its words:** different captions for one slot scored exactly
  the same. So "write 3 and keep the best score" selected nothing. Saturday 12:00 carousel scores 46% with any
  caption: a **calendar** problem. Code now probes every slot first, spends no writer on a slot that can't pass, and
  the result says to move it (v4).
- **One caption had no backup:** a single `LANG_ORDER` miss lost a post. Two options per check are cheap insurance.
  Our own best past posts break today's format rules (English first, no "Berlin"), so they are shown as **voice
  only** (v6).
- **AI answers are data:** the planner once wrote `id` instead of `slot_id`, and my code dropped both captions
  silently (v5). Code now normalises the answer, and anything unmatched is reported as skipped, never dropped. A
  test pins it.
- From the first real run: with `--tools ""` the agents saw **no** tools, because MCP tools are deferred behind
  ToolSearch, a built-in. The engine sets `ENABLE_TOOL_SEARCH=false`, and a test pins it.

**What still needs a human:** caption *quality* (wording, appeal) isn't measured by anything automatic here, because
the scorer can't see it. Your verdicts on proposals would be the next quality signal.

## What to look at (the lessons for the real design)
1. **The boundary lives in the server, not in the prompt.** Each caller connects with a dk_intel profile (the fetch
   with `planner`, the writers and the checker with `writer`). A tool outside the profile is refused with
   `NOT_ALLOWED_FOR_PROFILE`.
2. **Agents get tools, not a computer:** no shell, no web, no file writes; MCP tools only.
3. **The smallest context that carries the most signal.** Each writer gets only the evidence behind its own
   angle and two checked examples, not the whole pack, and no tools it won't use.
4. **Code for the workflow, AI inside the steps.** `run.py` is deterministic, testable and costs nothing where no
   judgment is needed. `/plan-week` (an LLM as orchestrator) is flexible but varies from run to run.
6. **Every change is measured** on the same frozen week (`eval.py`): cheaper only counts if quality holds.
5. **Failures stay local.** If one helper fails, its box turns red and the run still finishes with the others
   (tested).

## Adding product #3 (the pattern)
1. Its MCP server: one entry in `SERVERS` in `run.py` (and in `.mcp.json` for interactive use).
2. One agent file in `.claude/agents/` that lists `mcp__<server>__<tool>` in `tools:`.
3. One step in `orchestrate()`: an agent call if the step needs judgment, a `code_calls(...)` if it doesn't.
