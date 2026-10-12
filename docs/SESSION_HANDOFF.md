# Session handoff (2026-10-12): dk-intelligence + dk-publishing, everything a new session needs

For a new Claude session, a different account or another chat. Read this page first, then each repo's `CLAUDE.md`.
Nothing here is secret; keys, tokens and server addresses are never written in docs or chat.

## 1. The owner and how to work with them
- Shadman Arko runs **Dhaka Kacchi Berlin**, an online kacchi biryani kitchen, and builds all of its tech. This is also
  their interview showcase for leading ML engineering.
- They voice-type in informal English. Read for intent: "rack" or "rag" means RAG, and "go", "yes" or "start" means
  go ahead.
- They want **a clear recommendation and an order, not a menu**, short answers in plain words (no abbreviations), and
  pages they can see.
- At the end of a task: verify it yourself (tests, CI, deploy), then report the result plus **only** what needs their
  decision.
- Server (VPS) work is copy-paste steps **they** run. Each step starts with a `cd` and says what output to expect and
  what to paste back. Secrets are typed with `read -rs`, never pasted into chat.

## 2. The three projects (sibling folders under `Dhaka Kacchi/`)
| Folder | Repo | Visibility | What it is |
|---|---|---|---|
| `dk-intelligence` | shadmanArko/dk-intelligence | **private** | the ML platform: 7 models, the Sunday training run, intel-api on the VPS, the **dk_intel MCP server** (read-only knowledge, RAG), the agent **lab** |
| `dk-publishing` | shadmanArko/dk-publishing | **public** | the social publishing pipeline: Google Sheet + HTTP API → Postgres → Dagster → platform adapters; never posts twice; **dk_publish MCP endpoint** |
| `dhaka-kacchi-reels` | local git only (no remote yet) | private (to be) | the content engine: Remotion renders reels and slides from a JSON ContentSpec on the Mac; the **`dk_reels` MCP server**; read its `HANDOFF.md`, `CLAUDE.md`, `JOBS.md` |
| `dhaka_kacchi_ai_harness` | (public) | public | the live ordering system on the same VPS. Read only, and only when needed. **Never touch its PR #2.** |

The rules that must not be broken are in each repo's `CLAUDE.md`. The most important:
- dk-intelligence: Claude never gets a shell on the VPS; read-only on the warehouse; pushing to `main` deploys
  (CI); next free ADR **0031**.
- dk-publishing: `make check` before every commit; deploys are manual (`make deploy` / `make deploy-secrets`,
  run by the owner); migrations only forward; never post twice; platform names only in `adapters/platforms/` and
  `config/`; the repo is **public**, so no server addresses, tailnet names or secrets; next free ADR **0025**.
- Both: €0 running cost, no paid AI APIs unless the owner decides; never delete the owner's data without consent.

## 3. Where things stand (2026-10-12, evening)

### dk-intelligence (`main` = `f1d5cee`; branch `feat/m8-reels-handover` = M8, local only)
- **Phases 1–4 are done.** Phase 4 (MCP + RAG): dk_intel has been live on the VPS since 2026-10-12. The full status
  line per milestone is in [HANDOFF.md](HANDOFF.md). Phase 3's H10 (GDPR) is on PR #4, unmerged by the owner's choice.
- **The agent lab** (`lab/agentic-slice/`, PR #18 merged) is a small multi-agent system:
  - code is the orchestrator: fetch (code) → planner (AI, one turn) → 3 writers in parallel (AI) → re-check and
    propose (code);
  - it uses two MCP servers: dk_intel, and a **toy** `demo_publisher`;
  - the Agent Console (`uv run python lab/agentic-slice/console.py` → http://127.0.0.1:8770) has a live graph,
    "What it produced" cards per post, and a Run button;
  - `eval.py` freezes a week and compares versions.

  Measured: **377k → 21k tokens per run**, 35 seconds, every post that can pass proposed. The full story is in
  [lab README](../lab/agentic-slice/README.md).
- **[AGENTIC_PLAYBOOK.md](AGENTIC_PLAYBOOK.md)** holds the cost and quality rules from the lab. `CLAUDE.md` says to apply
  it to **any** agent system, and to add each measured lesson.
- **Known finding:** the content scorer judges format and posting time, not the words of a caption. The Saturday
  12:00 carousel slot fails with any caption, which is a calendar problem.
- **M8 (2026-10-12, branch `feat/m8-reels-handover`):** `--publish render|dry-run|drafts`; the designer agent;
  `media.py`, `handover.py`; console cards with thumbnails. Real runs found and fixed a 3.5 GB-per-run disk leak, a
  dry-run cache bug, loud music failing QA and a missing outcome event. Not yet run against the live publisher.

### dk-publishing (`main` = `e0297dd`)
- Facebook, Instagram, Threads and YouTube are live. TikTok is assisted (a Telegram card to the owner). LinkedIn, X
  and Reddit are not built; the owner hasn't chosen.
- **New today (ADR 0024, PR #1 merged, deployed and smoke-tested by the owner):**
  - `GET /v1/spec`: the publishing contract. Every rule per platform and format, generated from the checking code,
    versioned (ETag), with an example and a JSON Schema per format.
  - `GET /v1/overview` and `GET /v1/calendar`: status of **every** post, the Sheet's too, read only.
  - `POST /v1/posts/batch`: up to 20 posts. Always drafts, safe to resend, media may be https links.
  - **MCP at `https://publish.dhakakacchi.com/mcp/`** with the same API keys:
    - a read key gets `publishing_overview`, `list_posts`, `get_post`, `publishing_spec`;
    - a draft key also gets `submit_posts`, `cancel_post`;
    - there is **no approve tool**: the owner approves in the Sheet's Calendar tab.

    It's opt-in through `"mcp": {"enabled": true}` in the owner's `dk.json`, and that is on.
  - `dk api mcp-config --client claude-code|claude-desktop|cursor|langchain|openai-agents` prints client settings.
    The key appears only as `DK_PUBLISH_KEY`.
  - Eval: [docs/evals/2026-10-12-publishing-mcp.md](../../dk-publishing/docs/evals/2026-10-12-publishing-mcp.md).
    Code hands over 6 posts for 0 AI tokens; an agent over MCP did 6 of 6 jobs right, 51k tokens in all.
- **Its own open items** are in `dk-publishing/HANDOFF.md`:
  - the content plan ends 15 Oct, so the owner needs the next batch;
  - which new platform to build;
  - a performance report.

## 4. What to do next, in this order
**Phase 5 = the Content Studio**, approved by the owner on 2026-10-12. Plan:
[PHASE5_CONTENT_STUDIO_PLAN.md](PHASE5_CONTENT_STUDIO_PLAN.md); visual version:
https://claude.ai/artifact/PqPo1TGGVciq3EfrukRhTc. The target is 3 ideas a day, each adapted natively to 5 platforms
(150 posts every 10 days), approved on one daily review page, using the owner's own footage, which the system indexes.
Milestones S0 → S1 → S2 → S3 run in order; S4 and S5 run in parallel after S2; S6 comes last.

**S0 (safety and one truth), status 2026-10-12:**
- done: reels backed up to the private repo `shadmanArko/dhaka-kacchi-reels`;
- done: brand rules have one owner (ADR 0032): `UNVERIFIED_CLAIM` and `PORTION_CLAIM` are in dk_intel, the designer
  prompt holds no rule list, and reels price slides show only confirmed prices filled by code;
- done: milestone 8 is merged;
- **left:** the first live check, which needs the owner's draft key in `.env` (`DK_PUBLISH_URL`, `DK_PUBLISH_KEY`):
  `run.py --publish dry-run`, then `drafts` for one slot, then the owner rejects or approves it in the Calendar.
  After that, S0 is done.

**Then S1:** the contract registry (`docs/contracts/`), schemas with consumer tests in 3 repos, `intel contracts sync`,
and the content plan, platform mix, routes and budgets as data files. See the plan, §6 and §7.

**Still open for the owner:**
- confirm the mutton box price and availability in `facts.yaml` (until then a price slide can show only Borhani);
- where the daily review page lives (recommended: dk-publishing, owner-only, opened from a Telegram link);
- the budget cap per 10-day run (recommended: about 4M tokens, alert at 80%).

## 5. How to start a new session
1. Open the session in `dk-intelligence`. Read this page, then `CLAUDE.md` and [HANDOFF.md](HANDOFF.md).
2. Run the git checks from `CLAUDE.md` in **both** repos (`git status -sb`, `git log --oneline -3`, `git fetch -q`).
   Parallel sessions have switched branches here before.
3. For dk-publishing work, also read `dk-publishing/CLAUDE.md`, `dk-publishing/HANDOFF.md` and
   `dk-publishing/docs/CHANGE-MAP.md`.
4. Checks before any commit:
   - dk-intelligence: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`, and
     `uv run pre-commit run --all-files`;
   - dk-publishing: `make check`. Read its exit code; don't pipe it through `tail`.
5. Then continue S0/S1 from §4, or follow what the owner asks.
