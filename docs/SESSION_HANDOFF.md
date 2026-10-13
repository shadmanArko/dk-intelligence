# Session handoff (updated 2026-10-13, after S2 and S3): read this first

For a new Claude session, a **different Claude account** or another chat. Everything needed to continue lives in
the repos: this page (the state and the owner), [PHASE5_BUILD_GUIDE.md](PHASE5_BUILD_GUIDE.md) (how to finish every
remaining milestone), [PHASE5_CONTENT_STUDIO_PLAN.md](PHASE5_CONTENT_STUDIO_PLAN.md) (the approved plan), each repo's
`CLAUDE.md` (rules), and the ADRs. Nothing here is secret. Keys, tokens and server addresses are never written in
docs or chat.

**If you are picking this up: read this page, then the build guide, then start S4 (build guide §4); S2 and S3 are done.** The owner
approved the whole plan ("go, full permission"). Don't re-ask what is decided; report at milestone boundaries.

## 1. The owner and how to work with them
- Shadman Arko runs **Dhaka Kacchi Berlin**, an online kacchi biryani kitchen, and builds all of its tech. This is also
  their interview showcase for leading ML engineering: build it like a production system, with measured results.
- They voice-type in informal English. Read for intent: "rack" or "rag" means RAG; "go", "yes" or "start" means go
  ahead.
- They ask you to "think like a VP of engineering / the best MLOps engineer": senior judgement, a production
  system, the best social media content engine for the business. They also build the ordering system, the market
  data platform, a link builder and the publishing tool themselves.
- They want **a clear recommendation and an order, not a menu**, short answers in plain words with no abbreviations,
  and visual pages they can see (published artifacts, the Agent Console).
- At the end of a task: verify it yourself (tests, CI, the deploy), then report the result plus **only** what needs
  their decision. Don't make them wait on long foreground polls: watch CI in the background.
- Server work is copy-paste steps **they** run (Claude never gets a shell on the VPS). Each step starts with a `cd`
  and says what output to expect and what to paste back. Secrets are typed with `read -rs`, never pasted in chat.
- When they ask "where is X?", show it: send the file (for example the rendered video) or give the exact path.

## 2. The four projects (sibling folders under `Dhaka Kacchi/`)
| Folder | Repo | Visibility | Owns |
|---|---|---|---|
| `dk-intelligence` | shadmanArko/dk-intelligence | **private** | 7 ML models, the Sunday training run, intel-api on the VPS, the **dk_intel MCP server** (18 tools: knowledge, brand rules and facts, the post checker), the **agent lab / studio**, the contracts registry. **Pushing to `main` deploys** (CI). |
| `dk-publishing` | shadmanArko/dk-publishing | **public** | publishing to Facebook, Instagram, Threads and YouTube (live), TikTok (assisted: a Telegram card to the owner); the Calendar approval; the API and MCP below. Deploys are manual (`make deploy`, run by the owner). |
| `dhaka-kacchi-reels` | shadmanArko/dhaka-kacchi-reels | **private** (created 2026-10-12) | rendering on the Mac (Remotion): one JSON ContentSpec → reel or slides; footage and music indexes; the **dk_reels MCP server** (creative and ops profiles). |
| `dhaka_kacchi_ai_harness` | (public) | public | the live ordering system and the warehouse (orders, per-post social metrics, tracked links). Read only. **Never touch its PR #2.** |

The most important rules (full lists in each `CLAUDE.md`):
- **dk-intelligence:** no VPS shell; read-only on the warehouse; secrets only in `.env`; next free ADR **0034**;
  migrations additive; ratchets only rise.
- **dk-publishing:** `make check` before every commit (read its exit code; don't pipe it through `tail`); never post
  twice; platform names only in `adapters/platforms/` and `config/`; next free ADR **0025**; public repo, so no
  addresses or secrets. Read its `CLAUDE.md`, `HANDOFF.md` and `docs/CHANGE-MAP.md` before changing it.
- **reels:** Remotion pinned 4.0.528; `npx tsc -p .`, `npm test` and `npm run regress` before claiming success;
  never commit media; the two `index/*.schema.json` files are contracts (add fields only).
- **All:** €0 running cost, no paid AI APIs; never delete the owner's data without consent; never commit the owner's
  uncommitted edits.

### dk-publishing for agents (ADR 0024 there; live since 2026-10-12)
- `GET /v1/spec`: every platform rule, versioned (ETag), with an example and a JSON Schema per format.
- `GET /v1/overview` and `GET /v1/calendar`: status of every post, read only.
- `POST /v1/posts/batch`: up to 20 posts, always drafts, safe to resend.
- **MCP at `https://publish.dhakakacchi.com/mcp/`** with the same keys:
  - a read key gets `publishing_overview`, `list_posts`, `get_post`, `publishing_spec`;
  - a draft key also gets `submit_posts`, `cancel_post`;
  - there is **no approve tool**.
- `dk api mcp-config --client …` prints client settings.
- Eval (its `docs/evals/`): code hands over 6 posts for 0 AI tokens; an agent over MCP did 6 of 6 jobs right.
- Its own open items (its `HANDOFF.md`): the next content batch, which new platform, a performance report.

## 3. Where things stand

### Phases 1–4: done
dk_intel (MCP + RAG) has been live on the VPS since 2026-10-12; detail is in [HANDOFF.md](HANDOFF.md).

### Phase 5: the Content Studio (approved 2026-10-12)
The target: **3 ideas a day × 5 platforms = 150 posts every 10 days**, one idea adapted natively per platform,
approved on one review page a day, the owner's own footage indexed by the system. Visual version of the plan:
https://claude.ai/artifact/PqPo1TGGVciq3EfrukRhTc.

| Milestone | Status | Where |
|---|---|---|
| M8 media pipeline (designer agent → reels render → drafts at dk-publishing) | **done**, merged (PR #19) | ADR 0031, [results](results/M8_media_pipeline.md) |
| **S0** safety and one truth | **done** | ADR 0032; PRs #19 and #24 |
| **S1** contracts between the projects | **done** | ADR 0033; PR #20; `contracts/` |
| **S2** the studio at full scale | **done** (branch `feat/s2-studio`, PR to `main`) | ADR 0034, [STUDIO.md](STUDIO.md), [results](results/S2_studio.md) |
| **S3** the daily review page (dk-publishing) | **done and merged there, NOT deployed** (dk-publishing PR #2; the owner runs `make deploy`) | dk-publishing ADR 0025, `docs/runbooks/review-page.md` |
| **S4** the learning loop | **next** (can run beside S5) | build guide §4 S4 |
| S5 production breadth (templates, footage curator, template engineer) | to do | build guide §4 S5 |
| S6 unattended (schedule, earned autonomy, model choice) | to do | build guide §4 S6 |

**What S0 did:**
- reels got a private remote;
- dk_intel rules 1.1 (`UNVERIFIED_CLAIM` for invented process claims, cooking times and "new in Berlin";
  `PORTION_CLAIM`);
- the designer prompt holds no copy of the rules;
- reels price slides show only prices code fills from confirmed facts (before, three hard-coded prices skipped the
  checker);
- **the first live run.**

The live run (2026-10-12) designed, checked and rendered 2 reels on live data. It found 3 bugs, all fixed with tests:
1. streamed uploads (the owner's line does 224 KB/s, so a reel takes 80 s; a single write timed out);
2. no silent drops when the handover fails;
3. the publisher is checked before any AI.

**One real draft, `2026w42-ig-tue1700-s1` (Instagram reel, 13.10.2026 17:00 Berlin), waits for the owner in the
Calendar tab.** The video is in `dhaka-kacchi-reels/out/jobs/2026w42-ig-tue1700-s1/`. The second reel was held on
purpose: it claims "smoky lamb kacchi is back", but the lamb box's availability is unknown in facts.yaml. A rule for
that is part of S2.

**What S2 did** (`uv run intel studio run`, see [STUDIO.md](STUDIO.md)):
- `src/intel/studio/`: ports for tools, agent runner, renderer and publisher; settings in `config/studio/*.yaml`; the
  scorer picks each day's windows; strategist in chunks; per idea a copywriter, one adapter call for five platforms and
  a designer; one render at a time with uploads overlapping; token and call budgets; resume; a console on port 8771.
- dk_intel rules 1.2: `UNAVAILABLE_ITEM` (from facts.yaml data) and a wider German-first-line check.
- Three real 10-day dry runs on frozen inputs: **26 of 26 plannable ideas, 128 of 130 posts valid at the real publisher,
  541k tokens (4.2k per post), 14 minutes.** The scorer ruled out 4 of 30 slots (decision for the owner, in the results).
- **No drafts have been created from a 10-day run.** `--publish drafts` is the owner's go, after the review page is live.

**What S3 did** (in dk-publishing, public repo): a `/review` page with approve and reject per day, idea and platform
through the Calendar's own `review_drafts`; signed 24 h link from the 08:00 Telegram digest plus a session cookie; a
`watch` column in the Calendar tab; owner steps in dk-publishing `docs/runbooks/review-page.md`.

**What S1 did:**
- `contracts/registry.yaml` with pinned copies (reels content spec and manifest, dk-publishing's API);
- `uv run intel contracts sync [--check]`;
- `tests/test_contracts.py`;
- run-time version checks (dk_reels `list_templates.contracts`, the publisher's `spec`).

reels validates its manifest before every write. Both owner repos document "add fields only".

### Measured so far (for planning and the interview story)
- **The lab:** 377k → 21k tokens per run (demo mode, v1 → v6), every passable post proposed.
- **The media pipeline:** ~24k tokens and ~58 s per finished post. Renders take 22–33 s per reel.
- **dk-publishing MCP eval:** code hands over 6 posts for 0 AI tokens; an agent over MCP did 6 of 6 jobs right, 51k
  tokens in all.

### Local state that isn't in git
- dk-intelligence `.env` holds `DK_PUBLISH_URL` and `DK_PUBLISH_KEY` (a **draft** key named `content-agent`, made by the
  owner 2026-10-12). Never print it.
- `data/lab/` holds the ledgers and the cached publishing spec.
- `lab/agentic-slice/runs/` holds run logs.
- Renders are in `dhaka-kacchi-reels/out/jobs/<job_id>/`.

## 4. The owner's open items
Full list: build guide §5.
1. Approve or reject the first draft in the Calendar.
2. Confirm the facts: the mutton box price, and whether the lamb and taster boxes are available (now they also stop
   agents and the checker from mentioning those two boxes).
3. **Deploy the review page** (dk-publishing runbook `docs/runbooks/review-page.md`: `dk review init`, `make deploy-secrets`,
   `make deploy`, first link), then say go for the first `--publish drafts` run.
4. The four ruled-out slots (results doc, "Decisions").
5. The token budget per run (4M is set and 4x what is used) and the TikTok privacy default (`only_me`).

## 5. How to start a new session
1. Open the session in `dk-intelligence`. Read this page, then [PHASE5_BUILD_GUIDE.md](PHASE5_BUILD_GUIDE.md), then
   `CLAUDE.md`.
2. Run the git checks in **all four** repos: `git status -sb`, `git log --oneline -3`, `git fetch -q`. Parallel
   sessions have switched branches here before. If anything is unexpected, read `git reflog` before touching files.
3. Checks before any commit:
   - dk-intelligence: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`, then
     `uv run pre-commit run --all-files`;
   - dk-publishing: `make check`;
   - reels: `npx tsc -p .`, `npm test` and `npm run regress`.
4. Read [AGENTIC_PLAYBOOK.md](AGENTIC_PLAYBOOK.md) before building any agent part (`CLAUDE.md` requires it).
5. Start **S4** from build guide §4. Work in a branch, open a PR, check CI, merge (that deploys dk-intelligence),
   report.
