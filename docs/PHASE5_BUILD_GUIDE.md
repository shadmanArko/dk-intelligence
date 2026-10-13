# Phase 5 build guide: finishing the Content Studio (S2 → S6)

For the session or account that continues this work. Read [SESSION_HANDOFF.md](SESSION_HANDOFF.md) first for who the
owner is and where things stand. This page is the **how**: every remaining milestone, with the decisions already
made, the exact places in the code, the tests and the "done when". Don't re-decide what is marked **decided**; ask
the owner only about what is marked **owner**.

The plan this implements: [PHASE5_CONTENT_STUDIO_PLAN.md](PHASE5_CONTENT_STUDIO_PLAN.md), approved by the owner on
2026-10-12. Visual version: https://claude.ai/artifact/PqPo1TGGVciq3EfrukRhTc (the owner's account).

---

## 0. The target, in one paragraph
Every 10 days, or on a button or a schedule:
1. the system reads what works (the models, dk_intel, and later the measured performance);
2. it plans **3 ideas a day for 10 days (30 ideas)**;
3. each idea becomes **one master media file plus a native version per platform** (Instagram, Facebook, TikTok,
   YouTube, Threads): its own caption, format and options. That is **150 posts**;
4. it checks every word with dk_intel and renders with reels;
5. it hands everything to dk-publishing as **drafts**, which the owner approves on **one review page per day**;
6. it measures what each post did, and the next plan uses that.

**Owner decisions (2026-10-12):**
- one idea per slot, adapted per platform;
- one review page a day;
- the owner shoots more footage and the system indexes it;
- €0 running cost (subscription only, no paid AI API: rule 11).

---

## 1. Architecture you must keep (decided; ADRs 0031–0033 and the playbook)
1. **Code orchestrates; AI only judges.** Agents are headless `claude -p` processes started by code
   (`lab/agentic-slice/run.py: call_agent`). Each has its own prompt (`--system-prompt`), runs in an empty temp folder
   (no CLAUDE.md), has `--tools ""` (no built-ins), `ENABLE_TOOL_SEARCH=false` (or MCP tools hide behind the removed
   ToolSearch), `--allowedTools` set to its own MCP tools, and `--disallowedTools` set to every other tool on its
   servers. Deeper levels (strategist → crews → adapters) are code fan-out: Claude Code subagents can't nest.
2. **One owner per truth** (ADR 0032):
   - brand rules, facts and prices: dk_intel (the source is `docs/brand/facts.yaml` in the data-platform repo, packed
     into the knowledge pack);
   - platform rules: dk-publishing `/v1/spec`;
   - rendering: reels;
   - performance: the ordering system's warehouse.

   **Never copy a rule into a prompt or into reels code.**
3. **Contracts** (ADR 0033): `contracts/registry.yaml`, pinned copies, `tests/test_contracts.py`,
   `uv run intel contracts sync [--check]`, and version checks at run start. When you change what crosses a repo
   boundary, update the owner's schema, re-pin, and keep the tests green.
4. **Writes are drafts**, idempotent by `post_key` = `job_id` (e.g. `2026w42-ig-tue1700-s1`). Nothing reaches a
   platform without the owner's approval. There is no approve tool for agents.
5. **Measure every change** on frozen inputs (`lab/agentic-slice/eval.py`): quality (posts that pass, scores) next to
   cost (tokens, seconds). Add each lesson to [AGENTIC_PLAYBOOK.md](AGENTIC_PLAYBOOK.md).
6. **Every milestone ships:** code + tests + an ADR (`docs/adr/`, next free **0034**) + a results doc with measured
   numbers (`docs/results/`) + an interview story (`docs/interview/STORIES.md`). Push to `main` (it deploys; the owner
   delegated this) via a PR. Check CI yourself, then report.

## 2. The code as it is today (what S2 starts from)
`lab/agentic-slice/` (about 1,900 lines; 80 tests in `tests/test_lab_*.py` and `tests/test_contracts.py`):

| File | What it does |
|---|---|
| `run.py` | The orchestrator. `orchestrate(platform, trigger, inputs, save_inputs, publish, suffix)`. Modes: `demo` (toy calendar), `render`, `dry-run`, `drafts`. Steps: start checks (reels contracts via `list_templates`, the publisher via `client.spec(fresh=True)`) → `fetch_inputs` (slots from the **toy** `demo_publisher/calendar.json` + dk_intel `grounding_pack`) → `screen` (probes slots with a fixed caption and drops slots that can't pass; checks example posts) → **planner** (AI, 1 turn) → `_brief` per slot → the "already exists" pre-check → **writers** (AI, parallel, `HELPERS=3`) → **designers** (AI, after each writer) → `make_media`: `recheck_captions`, `checked_prices` (get_facts), `media.finalize_spec`, a reels dry run, `check_texts` (every on-screen word through `evaluate_drafts`), sequential renders, thumbnails, `handover.submit`. Run log in `runs/<id>.jsonl`; the report in `runs/<id>.md`. |
| `media.py` | `job_id`, `kind_for`, `designer_task`, `price_items` (confirmed facts only), `finalize_spec` (code sets id, formats, captions, publish, avoid list, price rows), `reels_job` (`npm run -s job` CLI; exit codes 0/2/3/4; the last stdout line is JSON), `AssetLedger` (avoids clips from the last 12 jobs), `contract_problem`. |
| `handover.py` | `PublishClient` (streamed uploads, retries 429/502–504, `spec()` cached by ETag), `MediaLedger`, `plan_post` (manifest → post with variants per platform via `ROUTES`; holds with a reason: `qa_failed`, `needs_review`, `contract`, …), `submit` (dry run, then real; batches of 20). CLI: `--manifest … [--dry-run]`, `--status`. |
| `.claude/agents/{planner,writer,designer,writer-lead}.md` | The prompts, tools and model (sonnet; writer-lead haiku). `writer-lead` is for the interactive `/plan-week` only. |
| `console.py` + `console.html` | Agent Console on 127.0.0.1:8770: live graph, result cards with thumbnails, a Run button (**demo mode only today**). |
| `eval.py` | freeze / run / add / show. **Demo mode only today.** |
| `handover.json` | `reviewed_languages`, per-platform option defaults (TikTok `privacy_level: only_me`, YouTube `made_for_kids: no`). |

**Hard-coded today (S2 makes them data):** `HELPERS=3`, the planner prompt's "up to 3", `--platform`
instagram|facebook only, `_dates_next_week` (weekday names, so 10 days can't be expressed), `grounding_pack(days=7)`,
`PROBE`, `ROUTES`, `BATCH_MAX`, `MEDIA_FRESH_DAYS=5` (shorter than a 10-day horizon), `RECENT_JOBS=12`, timeouts.

**Known limits that break at 150 posts (fix in S2):**
- `evaluate_drafts` takes at most **20 drafts** (`src/intel/mcp_server/servers/studio.py`);
- `screen()` cuts at `drafts[:20]`, and 6 of those are examples;
- `recheck_captions()` sends every kept caption in one call (it fails above 20).

`check_texts` and handover already chunk by 20.

## 3. Measured facts to plan with
| Item | Value |
|---|---|
| Tokens and time per finished post (planner + writer + designer) | ~24k tokens, ~58 s (M8) |
| Reel render | ~22–33 s each, one at a time, all cores; a cached identical spec takes 0.5 s |
| 3 stills | ~4 s |
| Reel file size | 18–20 MB (h264, CRF 18) |
| Owner's upload speed (Mac → VPS) | **224 KB/s**, so ~80 s per reel. 30 masters ≈ 45 min of upload: run overnight |
| One AI call through MCP (simple question) | ~5k tokens |
| Content scorer | Facebook and Instagram only. It reads format and time, **not caption words** (finding: different captions for one slot score the same) |
| Slot Sat 12:00 carousel (Instagram) | scores 46% with any caption: a calendar problem |
| Platform quotas | YouTube ~6 uploads a day; Threads 250 per 24 h; Instagram has a rolling API limit; TikTok is **assisted** (the owner posts by hand from a Telegram card) |
| dk-publishing batch | 20 posts per call; MCP rate limit 5 per second, burst 20; **no** per-key HTTP rate limit |

## 4. Milestones

### S2: the studio at full scale (DONE 2026-10-12: ADR 0034, [STUDIO.md](STUDIO.md), [results](results/S2_studio.md))
**Goal:** one command (and the console button) produces 150 valid drafts for a 10-day window: 30 ideas × 5
platforms, with budgets, measured on frozen inputs.

**Decided:**
- **Graduate the lab into a package:** `src/intel/studio/`. Keep `lab/agentic-slice/` working, or replace it, but
  keep its tests passing or move them. Suggested modules:
  - `config.py` (loads the YAML files below);
  - `slots.py` (the slot builder);
  - `agents.py` (`call_agent` behind a **RunnerPort**, so Claude Code headless can later be swapped for the Agent SDK
    or a local model);
  - `mcp.py` (code calls to MCP servers);
  - `plan.py`, `crew.py`, `media.py`, `render.py` (a **RendererPort**: the local reels CLI today, a worker later);
  - `handover.py` (a **PublisherPort**);
  - `budget.py`;
  - `run.py` (the orchestrator);
  - `cli.py`, which adds `uv run intel studio run|status|eval`.

  Add the new modules to the strict-mypy list (ratchets only grow).
- **Settings as data:** `config/studio/*.yaml` in dk-intelligence, each line commented in plain words:
  - `plan.yaml`: `horizon_days: 10`, `ideas_per_day: 3`, `platforms: [instagram, facebook, tiktok, youtube, threads]`,
    the mix of formats per idea (e.g. reel / carousel / image shares), the pillars, and `max_ideas_per_run`;
  - `slots.yaml`: posting times per platform per weekday. Take the default from the dk_intel posting-times model
    (`grounding_pack.posting_times`); the owner can override. **No weekday-name mapping:** real dates from today + N;
  - `routes.yaml`: (master kind, format) → the platform format plus default options. This moves `ROUTES` and
    `handover.json` defaults here;
  - `budgets.yaml`: max tokens per run (owner decision pending; recommended 4,000,000, alert at 80%), max turns per
    agent, max AI calls per idea, render and upload timeouts.
- **One idea → five native variants (cross-posting):**
  - per idea: one copywriter call (the core message) and **one designer call → one master render** (9:16 video, or
    slides);
  - then **one platform-adapter call per idea** that writes all five platform captions and options in one JSON
    answer. This is cheaper than 5 calls: batch the judgments (playbook A9).

  The adapter gets each platform's `/v1/spec` slice (from code) and the platform playbook (S4; until then, the
  `brand_reminders` and the posting notes). Platform rules it must respect come from the spec, never from the
  prompt:
  - YouTube: `title` and `made_for_kids`;
  - TikTok: `privacy_level` (default `only_me` until the owner decides);
  - Threads: 500 characters;
  - Instagram: hashtags at most 30, and the playbook range.

  `handover.plan_post` already maps one manifest to variants for several platforms via `publish.platforms`. Set
  `finalize_spec`'s `publish.platforms` to all five, and put per-platform captions in `spec.captions[platform]`.
- **The strategist (planner) at scale:** call it in chunks of about 10 ideas (3–4 days each) to keep the context
  small. Each chunk gets the previous chunks' angles, so ideas don't repeat. Code dedupes and spreads angles over
  pillars.
- **Chunking everywhere by 20:** `screen`, `recheck_captions`, `check_texts`, handover. Write one helper,
  `chunks(items, 20)`, and a test that 150 items pass through every step.
- **Budgets:** `budget.py` counts tokens from every `call_agent` end event. When the cap is hit, the run stops
  starting new AI calls, finishes what is in flight, and reports. A test uses the fake claude with token counts.
- **Render queue:** sequential and resumable. A job already rendered (same spec hash) is a cache hit in reels.
  Persist the queue state in `runs/<id>/queue.json`, so a stopped run resumes with `intel studio run --resume <id>`.
  Keep the Mac awake during a run (`caffeinate -dims` around the render and upload phase, macOS only).
- **Uploads:** already streamed (S0). For 30 masters × ~19 MB at 224 KB/s that is ~45 min, so upload as each render
  finishes (a pipeline), not all at the end. `MEDIA_FRESH_DAYS` must cover the horizon plus margin
  (e.g. `horizon_days + 4`).
- **Run modes stay the same** (render / dry-run / drafts). **The console Run button gets a mode picker** (default
  render; drafts asks to confirm). `eval.py` learns the media modes.
- **The "item not confirmed available" rule** (found in S0: a caption said "smoky lamb kacchi is back", but the lamb
  box's availability is unknown). Implement it in dk_intel `checks.py` **from facts.yaml data, not hard-coded
  names**. Recommended: add an optional `mentions: [lamb, lamm]` list per menu item in facts.yaml (the owner's file in
  the data-platform repo; ask before editing that public repo, or add a dk-intelligence overlay). Then block a
  caption that mentions an item whose `available` isn't confirmed `true`. Add a failing and a passing example to
  `tests/test_mcp_brand.py` (the test requires one per rule).

**Done when:**
- `uv run intel studio run --publish dry-run` on a frozen 10-day input produces 30 ideas → 150 variants, all
  validated by the real publisher (dry run), within the budget;
- results doc: tokens per idea and per post, minutes per phase, the share kept, held reasons;
- ADR 0034 (studio package, ports, settings as data, cross-posting);
- the console shows the run; tests cover chunking, budgets, resume and five-platform routing.

### S3: approval at scale (DONE 2026-10-13 in dk-publishing, merged, not deployed; ADR 0025 there)
**Goal:** the owner approves a day's 15 posts in under 2 minutes, watching each video.

**Decided:** a **daily review page** in dk-publishing that reuses the same `approve` and `cancel` use cases (the
API's `approve_post`, `cancel_post`, `review_drafts`).
- **Owner decision, recommended:** owner-only access through a **signed, expiring link** sent by Telegram each morning
  (dk-publishing already sends a 08:00 digest: `alerts.send_digest`), plus a session cookie.
- The page lists the day's drafts grouped by idea (`post_key` prefix): the media preview from a tokenised URL (the
  existing public-media mechanism), each platform's caption, and approve-all, approve or reject per post.
- Add a **"watch" link column** to the Calendar tab rows for agent posts (`config/sheet.yaml` + `dk sheet init`). The
  owner asked "where is the media?": today the Calendar shows text only, and the file is only on the Mac
  (`dhaka-kacchi-reels/out/jobs/<job_id>/`).
- Follow dk-publishing's CLAUDE.md and CHANGE-MAP:
  - ADR 0025 there;
  - guide 02 and `llms.txt`, plus `make guides` (needs Chrome);
  - `make check` (read its exit code; don't pipe it through `tail`);
  - it doesn't deploy on push. The owner runs `make deploy` from their Mac, so give them the exact steps.

**Done when:** the owner approves a real day of posts on the page. Measure the time.

### S4: the learning loop (DONE 2026-10-13: ADR 0035, [results](results/S4_performance_learning.md))
**Goal:** each plan uses what actually performed per platform.
- **Data:**
  - the ordering system's warehouse has `social_post`, `social_metrics_snapshot` (Instagram, Facebook, Threads,
    YouTube), `social_daily_analytics`, `tracked_link` (per-post website attribution) and the `social_share`
    database;
  - dk-intelligence already pulls the warehouse read-only (door 1, the `intelligence_reader` role, `src/intel/pull.py`
    + data contracts in `quality/`);
  - add the needed tables to the pull with **new data contracts**.
- **The join:** dk-publishing knows `post_key` → `live_url` (`GET /v1/calendar?state=published`); the warehouse
  knows `social_post.permalink`. Normalise the URLs and join. Store per post: `post_key` / `job_id`, platform, format,
  slot time, the spec features (template, hook length, music mood and culture, shot categories, number of slides),
  caption features, and the metrics. Add the join to `contracts/registry.yaml` (`warehouse.performance`).
- **New dk_intel tools** (read-only, with profiles, snapshots, budgets and catalog entries; see
  `src/intel/mcp_server/`):
  - `platform_playbook(platform)`: what works, with evidence and sample sizes;
  - `performance_by_feature(platform, feature)`;
  - `post_performance(post_key)`.

  The strategist and the adapters get the playbook in their task from code.
- Models follow the existing rules: new or retrained scorers go through the judge gates plus the owner's approval
  (rule 6). Never tune against the content and reach test sets again (rule 9). Start with plain statistics with
  confidence intervals, not new models.
- **A weekly performance analyst** (AI, analyst profile) writes a short "what worked" note and proposes playbook
  changes for the owner.

**Done when:** a plan cites what worked last time; every platform has performance rows.

### S5: production breadth (DONE 2026-10-13: ADR 0036, [results](results/S5_creative_director.md))
**Goal:** 10 days without heavy clip repeats; new looks without code per post.
- **reels:** a **template registry**, one folder per template: schema, composition, resolver, `meta.json`. The
  `list_templates` tool reads it. Then add templates:
  - collage (2–4 photos);
  - square 1:1 video;
  - a story variant;
  - a text-first card.

  Follow reels' CLAUDE.md:
  - Remotion pinned 4.0.528;
  - `npx tsc -p .`, `npm test` and `npm run regress`;
  - never commit media;
  - **bump the contract version or add fields only, then `npm run job:schema`**.

  Then re-pin in dk-intelligence.
- **An asset curator agent** for new footage the owner films:
  - code finds unindexed files in `incoming/` and grades them (the LUT scripts);
  - the agent writes the description, category and rating (`dk_reels` gets a new `curator` profile with
    `list_unindexed` and `propose_asset_entry`);
  - code writes the index (`npm run index`).

  Today the catalogue is hand-written in `scripts/build-catalogue.py`: move those entries to a data file first.
- **A template engineer (milestone 9):** an agent that writes a new template as TypeScript in a git worktree, behind
  the reels gates. It opens a PR with a preview render; the owner approves. Fed by the designer's `style_request`s
  and `upcoming_occasions`.
- **Footage reuse:** the `AssetLedger` window becomes horizon-aware (the 10-day plan avoids repeats across all 30
  ideas), with a measured repeat count in the results.

### S6: unattended (next)
- **The schedule:** a Claude Code scheduled task, or launchd on the Mac (the line is in the lab README), running
  `intel studio run --publish drafts --trigger schedule` every 10 days, plus the console button.
- **Earned autonomy** (only if the owner allows it, as policy data): a kind of post (e.g. "reel from the meat
  pillar") that the owner approved ≥ 80% of the time over 2 weeks may skip review. It is enforced in code (the
  dk-publishing approve right for a dedicated key), never in a prompt.
- **The model choice (D11):** measure with the frozen eval before moving any step to a cheaper or local model.
- **Monitoring:** a run summary to Telegram (via dk-publishing's notifier or intel-api's), and the budget alert.

## 5. Open items for the owner (ask once, briefly; never block on them)
1. **Approve or reject the first real draft**, `2026w42-ig-tue1700-s1` (Instagram reel, 13.10.2026 17:00), in the
   Calendar tab. The video is at `dhaka-kacchi-reels/out/jobs/2026w42-ig-tue1700-s1/`. If approved, it really posts.
2. **Facts** (`docs/brand/facts.yaml` in the data-platform repo):
   - the mutton Regular Box price;
   - whether the lamb box and the Taster box are still available.

   Until then, price slides show only Borhani €6.00.
3. **Where the review page lives and how to log in** (S3; recommended: dk-publishing, a signed Telegram link).
4. **The budget cap per 10-day run** (recommended 4M tokens, alert at 80%).
5. **TikTok privacy default:** `only_me` today (safe). Change it to `public` once the owner trusts the output.
6. **Old campaign slides say "100% halal"** (the owner's hand-made content); dk_intel blocks it without a certifier.
   Name the certifier in facts.yaml, or leave it.
7. Phase 3 H10 (GDPR) is on PR #4, unmerged by the owner's choice; don't merge it. The Dependabot PRs #21–#23 are
   open: review and merge them when CI is green (#21 moves Python to 3.14: check the image and tests first).

## 6. Gotchas learned the hard way (don't rediscover them)
- `claude -p` with `--tools ""` hides MCP tools unless `ENABLE_TOOL_SEARCH=false`. Fixed cost per agent turn went
  from ~20k to ~2k tokens by using `--system-prompt`, an empty temp folder, and `--disallowedTools` for unused tools.
- AI answers are data: normalise field names in code (a planner once wrote `id` for `slot_id`) and never drop a
  post silently; report every skip with a reason.
- Big uploads must stream (an 18 MB reel at 224 KB/s takes 80 s; a single write hit the 60 s timeout).
- Check the publisher **before** spending AI (`client.spec(fresh=True)`).
- Test stand-ins drift from reality: contract tests now check every fixture manifest and the fake spec.
- CI has no sibling repos: tests must bring their own reels folder (`monkeypatch media.REELS`).
- reels renders need the Mac awake; `npm run regress` must pass (stills bit-identical, videos within PSNR bands).
  An intentional visual change means updating `out/reference/` after looking at the frame.
- dk-publishing: `make check` takes ~100 s; guides need `make guides`; the Calendar tab is rewritten by the sync
  (≤ 2 min after a change, 15 min fallback). An agent post shows `source=agent`, `by=<key name>`, and approve and
  reject boxes in the far-right columns.
- Parallel sessions share these folders: run the git checks at the start of every task. Never commit the owner's
  uncommitted edits.
- Never put server addresses, tailnet names or secrets in dk-publishing or the harness (public repos). The draft key
  is in dk-intelligence `.env` as `DK_PUBLISH_KEY` (`DK_PUBLISH_URL=https://publish.dhakakacchi.com`). Never print
  it.
- Server commands: dk-publishing's `dk` CLI runs **inside the api container**
  (`cd /srv/dk/app/deploy && docker compose -f compose.prod.yaml --env-file .env exec api dk …`); dk-intelligence
  compose is under `/opt/dhaka-kacchi/dk-intelligence/deploy`. The owner runs them; Claude never gets a shell on the
  VPS.
