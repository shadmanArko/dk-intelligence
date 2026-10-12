# Phase 5 plan: the Content Studio, one agentic system across four projects

Status: **approved by the owner on 2026-10-12** ("go"). S0 started the same day.

## 1. What we are building
An agent system that, every 10 days or on a button:
1. learns from the machine-learning models and from how past posts performed what works on each platform;
2. plans **3 content ideas a day** for 10 days (30 ideas);
3. turns each idea into a **native version for each of 5 platforms** (Instagram, Facebook, TikTok, YouTube, Threads):
   its own caption, length, cover and format;
4. makes the media (videos, slides, collages), checks every word against the brand rules, and hands all
   **150 posts** to dk-publishing as drafts;
5. lets the owner approve a day's posts on **one review page**;
6. measures what each post did and feeds that into the next plan.

**Owner decisions (2026-10-12):**
- one idea per slot, adapted per platform;
- one review page per day;
- the owner films more footage and the system indexes it;
- €0 running cost stays (rule 11).

## 2. Where we stand (researched 2026-10-12)
| Project | Owns | Agent-ready today | Main gap for this plan |
|---|---|---|---|
| **dk-intelligence** (private) | 7 models, knowledge server dk_intel (18 tools, 6 profiles), brand rules, the lab orchestrator | the lab runs planner, writers and designers in parallel. Measured: about 24k tokens and 58 s per finished post. Milestone 8 (designer + handover) is built on branch `feat/m8-reels-handover`, **not pushed** | 3 posts, 1 platform and 7 days per run; checks cut or fail above 20 items; dates by weekday; slots from a toy calendar; no budgets; the lab is not a package yet |
| **dhaka-kacchi-reels** (local only) | rendering: one JSON spec (ContentSpec v1) → video or slides; 129 clips and photos, 100 usable tracks; dk_reels server (creative profile 5 tools, ops profile 2) | the designer agent fills specs; code renders (about 22 s per reel, 4 s for 3 stills) | **no remote backup**; 1 video template, 4 slide templates, no collage or square video; prices and "100% halal" hard-coded in a slide; no template registry; footage indexing is hand-written |
| **dk-publishing** (public) | platform rules (`/v1/spec`), drafts, scheduling, the Calendar approval, the dk_publish server (6 tools) | the contract, batches of 20, status and the MCP endpoint are live | no performance data; approval is one tick per post; TikTok is posted by hand (assisted); YouTube allows about 6 uploads a day |
| **dhaka_kacchi_ai_harness** (public, the ordering system) | orders, the warehouse: per-post likes, reach, followers, website attribution by tracked link | no agents and no MCP; read-only database roles exist | nothing joins "what we posted" to "how it did" |

Not the gaps: the publishing engine, the models, the knowledge server and the render engine are solid. **The gaps are
scale, a learning loop, single sources of truth, cross-project contracts, production breadth, and operations.**

## 3. Architecture principles (the playbook, applied at full size)
1. **Each project owns one kind of truth; the others read it through its MCP server or API, never through a copy.**
2. **Every link between projects is a versioned contract**, with a schema file in the owning project and **contract
   tests in each consumer**. A change in reels fails a check in dk-intelligence before any run, not in production.
3. **The orchestrator is code; AI works only inside judgment steps.** Deeper levels (strategist → idea crews →
   platform adapters) are code fan-out, because Claude Code subagents can't nest.
4. **Each agent sees only its own small tool set** (≤ 8 tools, one MCP profile). The system may hold 50+ tools; no
   agent sees them all.
5. **No rule is copied into a prompt.** Prompt fragments (brand rules, platform limits) are fetched from their owner
   at run time.
6. **Everything a person may change is data** (YAML or JSON), in the project that owns it: the content plan, the
   platform mix, posting times, routes, budgets, templates.
7. **Budgets and gates live in code; people approve.** Every write is idempotent (`post_key` = `job_id`).
8. **The agent runner is replaceable.** Agents speak MCP, and the engine calls them through one port: Claude Code
   headless today (subscription, button or schedule), the Agent SDK or a local model later, without touching the
   agents or servers.

## 4. Who owns which truth
| Truth | Owner | Read by others through |
|---|---|---|
| Business facts, prices, menu, brand voice, claim rules ("halal", process claims), blocked words | dk-intelligence brand pack | dk_intel `get_facts`, `brand_rules`, `evaluate_drafts` (reels reads prices from here, not code) |
| What works per platform (formats, times, hooks, lengths, music moods) | dk-intelligence models + a new **platform playbook** | dk_intel `platform_playbook`, `performance_by_feature` (new) |
| Post performance and website attribution | harness warehouse | dk-intelligence's read-only pull (an existing door) → joined by `post_key` / live link |
| Platform technical rules, drafts, schedule, approval, status | dk-publishing | `/v1/spec`, batch, overview, dk_publish |
| Visual production: templates, footage, music, rendering | reels | dk_reels (creative, ops and a new curator profile), the ContentSpec schema, the render manifest |
| The content plan and the orchestration | dk-intelligence **studio** (the lab, graduated into `src/intel/studio/`) | the content-plan file, the run log, the console |

## 5. The agents (about 15 per run) and the code around them
**Code (0 tokens):**
- **Slot builder:** the content plan becomes 30 idea slots and 150 platform slots, with real dates and times from the
  posting-times model.
- **Fetcher:** the grounding pack, the platform playbook and the spec slices.
- **Pre-checker:** drops slots that can't pass.
- **Asset ledger:** limits clip repeats.
- **Render queue:** resumable and overnight.
- **Gate:** every word through `evaluate_drafts`.
- **Handover:** batches of 20.
- **Status tracker.**
- **Performance joiner.**

**AI roles:**
| Role | Count per run | Job | Tools (its own profile) |
|---|---|---|---|
| Strategist | 1 (in 3 chunks) | the 10-day mix: pillars, occasions, competitor gaps, what worked | dk_intel planner + platform_playbook |
| Copywriter | 1 per idea (parallel) | the core message of an idea | dk_intel writer |
| Video designer | per video idea | a beat-reel spec | dk_reels creative |
| Stills designer | per stills idea | slides, collages, price and compare cards | dk_reels creative |
| Platform adapter | 5 (one per platform, one agent file with a platform parameter) | the native version: caption, hashtags, length, cover, format per platform rules and playbook | dk_publish spec + dk_intel writer |
| Reviewer | only for borderline cases | judgment where the automatic check is unsure | dk_intel checker |
| Asset curator | when new footage arrives | describes, rates and indexes new clips and photos | dk_reels curator (new) |
| Template engineer | on request (milestone 9) | a new template as code, through a pull request the owner approves | the reels repo, in a git worktree |
| Performance analyst | weekly | what worked; proposes playbook updates | dk_intel analyst + performance tools |

**Tools across the servers:** 31 today (dk_intel 18, dk_reels 7, dk_publish 6), about 42 after this plan.
- New dk_intel tools: `platform_playbook`, `performance_by_feature`, `post_performance`, `content_plan_status`.
- New curator tools: `list_unindexed`, `propose_asset_entry`, `template_catalog`.
- Toward 50+ as templates and platforms grow. Each agent still sees ≤ 8.

## 6. How a change in one project reaches the others
1. **Contract registry** (`docs/contracts/` in dk-intelligence lists them all):
   - ContentSpec and the render manifest (owner: reels);
   - the publishing spec and batch (owner: dk-publishing);
   - the dk_intel catalog;
   - the content plan;
   - the performance join (owner: dk-intelligence).

   Each entry has a version, a schema file, an owner and its consumers.
2. **Consumer contract tests.** Each consumer keeps example messages and checks them against the producer's current
   schema. `uv run intel contracts sync` pulls the schemas from the sibling repos and fails on a breaking change.
3. **Runtime discovery.** Every run first reads the versions (`spec_version`, the ContentSpec version, the catalog
   version) and records them in the run log. A version the engine doesn't know stops the run with a clear reason; it
   never guesses.
4. **Generated prompt fragments.** Limits and rules come from their owner at run time, so editing a brand rule or a
   platform limit needs no prompt change.

## 7. Milestones (each: code + tests + ADR + measured results + interview story)
| # | Milestone | What it delivers | Done when |
|---|---|---|---|
| **S0** | Safety and one truth | reels gets a private remote; milestone 8 is verified live once (dry run, then one real draft) and pushed; "halal" and process claims are decided in one place (dk_intel); the rule copies leave the designer prompt and the reels slide (prices read from dk_intel) | nothing is unbacked-up or duplicated |
| **S1** | Contracts and configuration as data | the contract registry, schemas, consumer tests in 3 repos, `contracts sync`; the content plan, platform mix, routes and budgets as data files | changing a reels field fails a dk-intelligence test |
| **S2** | The studio at full scale | the lab becomes `src/intel/studio/` (ports for the runner, renderer and publisher); 10 days × 3 ideas × 5 platforms; idea → 5 native variants; chunking everywhere (the limit of 20); budgets (tokens, turns, € per run); a resumable render queue (overnight, the Mac kept awake); the console Run button for the full pipeline | a frozen 10-day run produces 150 valid drafts, with tokens, time and quality measured |
| **S3** | Approval at scale | in dk-publishing: a daily review page with previews (approve all or reject some), owner-only, the same `approve` use case; a Telegram link to it | a day's 15 posts are approved in under 2 minutes |
| **S4** | The learning loop | join post performance (warehouse) with `post_key`, spec and caption features; the per-platform playbook; the strategist and adapters use it; the weekly analyst | each plan cites what worked last time; TikTok, YouTube and Threads have performance data too |
| **S5** | Production breadth | a template registry in reels; collage, square and story templates; the asset curator for new footage; the template engineer (milestone 9) | 10 days with no clip used more than N times; a new template added through a pull request |
| **S6** | Unattended | a schedule (Claude Code scheduled task or launchd, plus the button); earned autonomy as policy data (a kind of post you approved ≥ 80% of the time for 2 weeks may skip review, only if you allow it); the model choice measured (D11) | a 10-day plan runs alone and waits only for your review |

**Order:** S0 → S1 → S2 → S3 run in sequence, because each one de-risks the next. S4 and S5 can run in parallel
after S2. S6 comes last.

## 8. Decisions still needed from the owner
1. **"Halal" and process claims:** one rule, in dk_intel. Recommended:
   - "halal" only with the certifier named;
   - no process claims that aren't in `facts.yaml`.
2. **Where the review page lives:** recommended dk-publishing, behind owner-only login (a signed link from Telegram).
3. **Budget per 10-day run:** recommended a cap of about 4M tokens (about €0 on the subscription), with an alert at
   80%.
4. **D5, D9–D11** ([Phase 5 hand-over](mcp/PHASE5_HANDOVER.md)):
   - D5: yes, agent files;
   - D9: answered for posting (drafts in dk-publishing);
   - D10: Claude Code now, behind the runner port;
   - D11: measured in S6.

## 9. Risks
- **Footage supply:** mitigated by the asset ledger, stills and collages, and the curator.
- **Platform quotas:** YouTube about 6 a day; TikTok by hand.
- **Subscription usage limits** at 150 posts: mitigated by budgets and by one idea for 5 platforms.
- **Brand mistakes at volume:** mitigated by code gates, the review page and earned autonomy only per kind of post.
- **The Mac must be awake for renders:** mitigated by an overnight queue with keep-awake, and a later VPS worker
  behind the same port.
