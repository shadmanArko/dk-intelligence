# Handoff: state of dk-intelligence and what comes next

Written 2026-10-09 (00:45 Berlin) at the end of the Phase 3 session, for the next Claude Code session in this folder.
That session's notes are not available to you: they live in another project's memory. **Everything you need is
here, in [CLAUDE.md](../CLAUDE.md) and in the docs this page links.** Facts marked *(as of 2026-10-09)* can drift:
re-check them before acting.

> **Start here**
> 1. Run the git checks in CLAUDE.md (parallel sessions share this folder).
> 2. Owner says "Phase 4" / "MCP" / "RAG" → §4: ask the 4 questions, then write `docs/PHASE4_PLAN.md` for approval.
>    **No code before the plan is approved.**
> 3. H10 / PR #4: **the owner merges it themselves.** Don't touch it unless asked; if asked, see §3.
> 4. Never: touch harness PR #2, run VPS commands yourself, delete the owner's data without consent.
> 5. At the end of each task, update this file (or replace its status with the Phase 4 plan's status line).

---

## 1. Where things stand *(as of 2026-10-09)*

**Repository**
- `main` = `origin/main`: `2c4429d` ("Phase 3 H11: interview kit", CI green and deployed), plus the docs commit
  that added this file, `CLAUDE.md`'s rewrite and a test fix.
- Branches:
  - `feat/GDPR` (`68fa9f6`, H10, [PR #4](https://github.com/shadmanArko/dk-intelligence/pull/4), open);
  - `claude/afs-citizenship-no-aggregates`: already merged (PR #3), so it is stale and the owner may delete it.
- No Dependabot PRs open. Dependabot runs on Mondays (uv, actions, docker); the owner merges its PRs.
- `main` has 232 tests; strict mypy covers 24 modules; branch coverage 82.6–82.9% (floor 80).

**Production (VPS)**
- intel-api runs `2c4429d` with **4 active models**:

  | Model | Active version |
  |---|---|
  | content_scorer | `cs-20261008T071453-4ea997` |
  | community_map | `cm-20261008T071522-6aebe5` |
  | occasion_planner | `op-20261008T071525-98f5e3` |
  | funnel | `fn-20261008T071528-b07a77` |

- The guardrail is `on`, the audit chain is OK (28 entries), and no approvals are pending.
- Not live:
  - **creator_matcher** has never been uploaded; it was only in a dry run.
  - **weakness_miner** waits for the owner's label check.
  - **reach_scorer** fails the judge and is never shipped.
- Shadow ledger: 3 posts and 6 predictions, 0 matured outcomes so far. Evidence is per model version.

**Phase 3 (MLOps hardening, [plan](PHASE3_MLOPS_PLAN.md))**

| Milestone | Topic | Status |
|---|---|---|
| H0 | plug-ins, typing ratchet | done |
| H1 | build once, deploy by digest, supply chain | done |
| H2 | data contracts | done |
| H3 | SLOs, alerting | done |
| H4 | drift monitoring | done |
| H5 | governance and audit | done |
| H6 | guardrail | done |
| H7 | restore drill, dead-man's switch | done |
| H8 | Ansible; applied on the VPS, 2nd run `changed=0` | done |
| H9 | performance | done |
| **H10** | GDPR and responsible AI | **built, PR #4 unmerged** (the owner merges later) |
| H11 | interview kit (`docs/interview/`) | done |

**Scheduled next** *(Sunday 2026-10-11)*
- 04:30 VPS: the restore drill. It now also verifies the audit chain in the restored copy.
- From 01:00: the data platform's weekly refresh on the Mac.
- No Sunday training this week: this week's real run already happened on 2026-10-09 (`20261009T063936Z`, PASS).
  - It uploaded weakness_miner and creator_matcher for their first approvals on Telegram, and refreshed community
    map, occasion planner and funnel.
  - It **held** the content scorer: 0.688 vs 0.688 against the active version, so decision B worked live.
  - The next run is due Sunday 2026-10-18 at 12:00 (launchd `com.dhakakacchi.intel.weekly`, catch-up if the Mac was
    off).
- Monday 09:00: the weekly Telegram report.

---

## 2. Decisions the owner made on 2026-10-09
**A. Occasion-planner fairness exception: CONFIRMED by the owner.**
- Recorded on `feat/GDPR` in commit `84e0a00`. ADR 0024 now says the community map was decided on 2026-10-08 and the
  occasion planner confirmed on 2026-10-09, matching `policy.yaml`. Counts in the H10 docs were also made precise.
- Not merged: see C.

**B. Ask for a scorer approval only when the new version clearly beats the ACTIVE one: BUILT.**
- `src/intel/incumbent.py` plus `hold_if_not_better` in `weekly.py` (platform code, not sealed).
- After the judge passes a scorer, the Sunday run scores the **active** version (from the VPS `/ready`) on the
  challenger's test posts. It uses only posts newer than the active version's own training cutoff.
- The new version is uploaded for approval only if its balanced accuracy is higher by at least the judge's
  `min_gain_balanced_accuracy` (0.02) on at least 30 such posts. Otherwise it is reported as `kept_active` and you
  aren't asked.
- If the VPS is unreachable or the active version's run isn't on the Mac, the old behaviour applies (you're asked).
- Real check: Thursday's dry-run challenger scored 0.688 vs 0.688 against the active version on 75 posts, so it
  would now be held.
- Tests: `tests/test_incumbent.py`.
- Effect: the active content scorer keeps collecting evidence. The guardrail can act once it has 40 matured 7-day
  outcomes, probably in November.

**C. H10 / PR #4: the owner merges it themselves, later. Do NOT merge, rebase or push it unless asked.** The merge
procedure below is for when the owner asks for help. Without the merge:
- every pull copies all readable warehouse tables to the Mac (40 in the latest pull). These include `customer` and
  `review` (empty today), `orders` (9 rows) and raw Facebook payloads with 66 comments by 3 private commenters;
- there is no retention job, and Docker and job logs aren't capped;
- there is no PII scan or fairness gate on packages;
- the interview kit says "merge pending";
- a Phase 4 corpus can't be declared in the H10 policy file yet, so declare it in the Phase 4 plan instead and move
  it into `policy.yaml` when H10 lands.

## 3. Open items

| # | Item | Who | Next action |
|---|---|---|---|
| 1 | **H10 / PR #4 merge prep** | Claude, when the owner says "merge" | See *How to merge H10* below |
| 2 | weakness_miner check | **done by Claude** (owner delegated it, 2026-10-09) | 198 of 200 clauses labelled **blind**: topic names only, never the dictionary's phrases or its predictions. Each label is marked `labelled_by: claude` in `data/checks/weakness_miner_labels.jsonl`. Result: aspect precision 0.844, tone accuracy 0.818 (both ≥ 0.80), recall 0.732. The owner still gives the first approval on Telegram, and can relabel at http://127.0.0.1:8765/check (Control Room running) |
| 3 | creator_matcher and weakness_miner first approvals | owner | A real training run was started on 2026-10-09 to deliver both. Approve them in Telegram; it doubles as a plausibility check (noise accounts go in creator_matcher's `exclude_handles`) |
| 4 | Decision B | done | See §2 |
| 5 | 24 h outcomes often missed | Claude (investigate) | Warehouse metric snapshots arrive once a day (~02:00–03:15 UTC), but `measure.py` accepts a 24 h outcome only within 12 h after the mark. Posts outside ~14:00–02:00 UTC never get one. The 72 h and 7 d outcomes are fine (the guardrail, the live gate and drift monitoring use 7 d only). Probably widen the 24 h window, or label it "first snapshot after 24 h" |
| 6 | Old GitHub token "Hello 2" | done | The owner deleted it (2026-10-09) |
| 7 | GHCR read-only token on the VPS (1-year expiry) | owner | Renew before ~2027-10-08 |
| 8 | YouTube OAuth credentials were pasted in chat (2026-10-03) | owner, when convenient | Steps given 2026-10-09: reset the OAuth client secret in Google Cloud Console, revoke the app at myaccount.google.com/permissions, re-authorise with the read-only scope `youtube.readonly`, and put the new values in the data platform's `.env`. Low risk, but the old values are in a chat transcript |
| 9 | Interview rehearsal | owner, optional | Offer to play the interviewer (`docs/interview/`) |
| 10 | Doc drift on `main` (small) | Claude, with the next change nearby | `QUALITY.md` counts 14 promtool test *groups*, but the interview kit says "30 alert-rule tests" (29 alert assertions + 1 PromQL test): pick one unit. `docs/results/M5_serving.md` "Open" section is stale (door 3 and backups are done). "No swap" (2 GB swap since 2026-10-08) appears in the `HostMemoryLow` rule (ops.yml comment and summary), the Grafana vps-host panel and `SLO.md` |
| 11 | Data-platform leftovers (other repo) | owner, optional | Unload launchd `com.dhakakacchi.mip.youtube-daily` (no-ops now). Optional ruleset on the data platform's `main` (block force pushes and deletions) |

### How to merge H10 (when the owner says so)
PR #4's only CI run was cancelled, and GitHub marks the PR as conflicting until it's rebased.

0. Decision A is already confirmed and recorded (`84e0a00`); nothing to do.
1. **Rebase** `feat/GDPR` onto `main`. As of `2c4429d`, exactly one file conflicts, `docs/PHASE3_MLOPS_PLAN.md`
   (the Status line and the H10/H11 rows of the table). Anything added to `docs/adr/README.md` or
   `docs/interview/STORIES.md` on `main` before the merge adds more conflicts. Resolve to "H10 done ([results](results/P3_H10_privacy.md)) · H11 done
   ([interview kit](interview/README.md))" and mark both rows ✅.
2. **Update the interview kit:** "merge pending" appears in `docs/interview/README.md`, `ONE_PAGER.md`,
   `TALK_TRACKS.md`, `TRADEOFFS.md` and `QUESTIONS.md`. README.md's last paragraph also says "on the `feat/GDPR`
   branch, with the pull request open". Make each one done.
3. **Fix `docs/results/P3_H10_privacy.md`:**
   - "12 existing pulls" and "× 12 kept pulls" should be **9** (`data/pulls` has 9). `minimise` also re-checks the
     12 market snapshots, but has nothing to remove there;
   - "13 commenter identities" should say 66 comments by 3 private commenters (13 author fields);
   - "all 7 production models pass" should say all 7 models' latest outputs pass, of which 4 are active.
4. **Push the rebased branch** with `git push --force-with-lease origin feat/GDPR`. It's Claude's own branch, so
   rewriting it is allowed. Wait for the PR's CI to go green; the owner merges on GitHub.
5. **After the deploy**, give the owner two steps:
   - on the VPS, `cd /opt/dhaka-kacchi/dk-intelligence && ansible-playbook -i infra/inventory.yml infra/site.yml
     --check --diff -K`, review the diff (expect only `/etc/logrotate.d/dk-intelligence` and the `logrotate`
     package), then apply, then run again and expect `changed=0`;
   - on the Mac, `uv run intel privacy minimise` deletes undeclared tables from old pulls **permanently**. Explain
     that first and only run it with explicit consent; the 5 model tables stay byte-identical. Note that the deploy
     recreates intel-api and intel-db once, because their logging config changed.

---

## 4. Next task: Phase 4, MCP + RAG
The owner wants this next ("tomorrow maybe"). **Nothing is built and there's no plan yet.** Process, as for Phase 3:
**ask the open questions → write `docs/PHASE4_PLAN.md` for approval → build milestone by milestone**, each with
code, tests, an ADR (from 0025), a results doc and an interview story.

**Where it lives:** Phase 4 code goes in **this repo** (private) unless the owner decides otherwise. The harness
design puts MCP servers in its public `tools/` folder, next to PR #2. Don't build there without the owner's go.
**Access:** the MCP server reaches data only through what the Mac already uses: door 1 (warehouse, read-only),
intel-api over the tailnet (`INTEL_API_URL`), and the data platform's read-only `mip_reader` role on localhost.
**No door is widened.**

### What's already agreed or known
- **The owner's vision:** a self-improving system where agents call the models cheaply through MCP ("spend very less
  amount of token") and generate 10–30-day content plans per platform, regenerating drafts the scorer rates badly.
  The build order agreed on 2026-10-07: data → MLOps/models → **MCP** → agents.
- **Proposed in the last session (not yet approved):**
  - a **read-only** MCP server first, going through the existing doors, with tools like "score this draft",
    "competitors' top complaints", "upcoming occasions", "monitoring / ledger status";
  - RAG over review texts, market posts and our own posts, with **cited sources**.
- **RAG design discussed 2026-10-06:**
  - one engine (Postgres + pgvector + one local multilingual embedding model; hybrid with full-text search and rank
    fusion) holding several collections: brand (public-safe), rules, own_content, market_examples, customer_voice
    (sensitive), competitor_intel;
  - each agent gets its own DB role;
  - the brand card always sits in the prompt;
  - changing facts (prices, hours) come from tools or `facts.yaml`, not from retrieved text;
  - no GraphRAG.

### Facts the plan must respect *(as of 2026-10-09)*
- **API surface** (`src/intel/serve/app.py`):
  - JSON that never writes the ledger: `POST /v1/score` (one draft, all active scorers), `POST /v1/score/batch`
    (≤ 100 drafts), `GET /v1/monitoring` (computes and caches in `kv` on its first call after a restart), `/v1/ledger/summary`, `/v1/ledger/export`, `/v1/audit`, `/ready`, `/health/deep`;
  - **`/v1/predict` and the HTML form `POST /try` write to the ledger**, and `POST /approvals/{id}/approve|reject`
    and `POST /models/{name}/rollback` change production. **An MCP server may call only an allowlist:** the GET
    routes plus `POST /v1/score` and `POST /v1/score/batch`;
  - **there's no JSON endpoint for insights**: insight bundles are rendered only as HTML (`views.py`). An MCP needs
    new read-only routes such as `/v1/insights/{name}`;
  - `/v1/*` has no per-user auth beyond tailnet-only exposure.
- **pgvector:** intel-db runs the `pgvector/pgvector:pg16` image, but no vector extension, column or table is in use.
  The data platform's Postgres has the extension installed but unused.
- **No deep-learning, embedding, LLM or MCP libraries** in dependencies today. The ML stack is scikit-learn, plus
  mlflow and pandera in the Mac-only `train` group; there's no sentence-transformers, torch, mcp or ollama. The
  weakness miner is a dictionary method, not embeddings.
- **Compute:**
  - Mac: M3 Pro, 18 GB, with **Ollama installed** and `gpt-oss:20b` pulled; the Hugging Face cache has only an
    English embedding model.
  - VPS: ~7.8 GB, shared with the ordering system. Container limits are intel-db 384 MB, intel-api 512 MB and
    monitoring ~800 MB. A bge-m3-class model (~2 GB RAM) or an LLM likely doesn't fit there without measuring
    (`free -h`).
- **Corpora and sizes:**

  | Corpus | Rows |
  |---|---|
  | market snapshot `biryani_reviews` | 13,698 (85% German) |
  | `creators` / `creator_posts` | 865 / 2,210 |
  | `occasion_day` | 25k |
  | data platform `core.review` | ~587k Lieferando reviews (~236k with text) |
  | data platform `core.post` | ~1.96M |
  | our own posts | 466 |
  | brand docs | in the data platform's `docs/brand/` (brand-book, voice-and-rules, `facts.yaml`, all DRAFT with [CONFIRM] items) |

  Also, in the data platform repo, `config/content/playbook.yaml` (blocked words; it reaches us as
  `content_rules.yaml` in each market snapshot) and `docs/content-agent-guide.md` (Planner → Writer → Producer →
  Checker → Human).
- **Harness design to plug into, not duplicate** (`../dhaka_kacchi_ai_harness/ARCHITECTURE.md`):
  - MCP inventory: one server per external system, read tools separated from write tools, every write takes an
    `idempotency_key` and returns a `reversal_token` (or declares `reversible: false`), every call logged;
  - autonomy tiers (read / draft / act / never), enforced in code;
  - an `agent_action` approval queue (warehouse table, empty) for the CEO cockpit;
  - the "company brain" is designed as **git, not a vector DB**, so RAG must justify itself on the large corpora
    (reviews, posts), not the brand docs;
  - its `tools/`, `brain/` and `agents/` folders are empty. **Harness PR #2 (RAG, another contributor): never touch
    it.**
- **Rules that apply:** ADR 0012 (€0, no paid AI APIs; it says it will be revisited in Phase 4, so the owner decides)
  · ADR 0003 (three doors; no VPS shell) · tailnet-only exposure · approvals by the owner · the audit trail
  (`audit_log`, hash-chained) · H10's policy file (any new corpus needs a declared purpose and retention, once H10 is
  merged) · blocked words and no ethnic or religious targeting in any generated text.

### Questions to ask the owner first
Ask these four in **one** AskUserQuestion call, each with the recommended option first:
1. **Which corpora first, and which stay internal?** *Recommend* competitor reviews + our own posts + market examples
   first, all internal (tailnet/Mac only). Brand docs stay in the prompt, not in RAG. H10 isn't merged (the owner
   merges it later), so declare each corpus's purpose and retention in the plan itself.
2. **Generation in Phase 4?** *Recommend no:* tools + retrieval only, so ADR 0012 (€0, no paid APIs) holds. Text
   generation (captions) waits for the agents phase. Alternatives: a local model via Ollama, or Claude via the
   owner's subscription (needs an ADR superseding 0012).
3. **Client and place:** *Recommend Claude Code / Claude Desktop on the Mac, over stdio, read-only.* Server-side
   agents on the VPS come later (RAM limits; writes need the approval queue).
4. **First use case and "done when":** *Recommend* a cited answer to "why do biryani places in Wedding get 1-star
   reviews?" plus "score these 10 caption variants and explain". Done when the system passes a 20-question golden set
   the owner spot-checks (retrieval recall@k and citation faithfulness measured).

**Default the rest in the plan** for approval, instead of asking up front:
- embedding model: multilingual-e5-small or base, run on the Mac;
- vector store: a pgvector DB that dk-intelligence owns, either local on the Mac, or intel-db via an additive
  migration with vectors delivered as a package over door 2;
- **not** the data platform's Postgres: there we only have the read-only `mip_reader` role, and that repo excludes
  this code;
- evaluation method, and the tool-call audit log (intel-db `audit_log` or a new append-only table);
- milestone ids (e.g. P4.1…).

---

## 5. Production cheat sheet
Details: [deploy/README.md](../deploy/README.md), [ARCHITECTURE](ARCHITECTURE.md), [RELIABILITY](RELIABILITY.md),
[runbooks](runbooks/alerts.md).

| Thing | Where / how |
|---|---|
| VPS | host `vmd206122` (x86_64), reached over Tailscale. The owner logs in as `deploy` (sudo). Claude has no shell |
| Pages (tailnet only) | https://vmd206122.tailc055f3.ts.net (Approvals, Posts, Try a draft, Insights, Training, Audit) · Grafana on `:8443` |
| Checkout and config | `/opt/dhaka-kacchi/dk-intelligence` (`deploy/.env`, `deploy/monitoring/.env`, owner-managed) |
| Logs | `/opt/dhaka-kacchi/logs/` (`intel-deploys.log`, `intel-backup.log`, `intel-restore-drill.log`, `mip-collector.log`) |
| Containers | compose project `dk-intelligence`: `intel-api` (127.0.0.1:8710, 512 MB), `intel-db` (pgvector pg16, 384 MB, no port). Monitoring: `dk-monitoring` (Prometheus 9090, Grafana 3330, Alertmanager, node-exporter, blackbox) |
| Ports in use (loopback) | 3000 publishing tool · 5432 harness Postgres · 5442 collector DB · 8710 intel-api · 9090 Prometheus · 3330 Grafana. Check `sudo ss -ltnp` before choosing a new one |
| Deploy | push to `main` → CI → `deploy <sha> <digest>` over door 3 → `deploy/deploy.sh` (pull by digest, smoke test, roll back to `:previous` on failure) → `monitoring/up.sh` |
| Workers in intel-api | inbox (30 s), measure (hourly: ledger, monitoring, guardrail), reports (5 min: Monday report), telegram (long poll) |
| Telegram (owner only) | `/status`, `/training`, `/rollback`, `/keep`; approve/reject buttons; alerts with runbook links |
| Host cron (Ansible-managed) | 02:50 intel-db backup · Sun 04:30 restore drill · 10:15 collector daily · Sat 22:30 collector weekly. The harness's own cron lines are left alone |
| Playbook | owner runs ON the VPS: `ansible-playbook -i infra/inventory.yml infra/site.yml --check --diff -K`, then without `--check`; a second run must show `changed=0` |
| Everyday owner commands | in `deploy/`: `docker compose ps` · `curl -s 127.0.0.1:8710/ready` · `docker compose logs --tail 100 intel-api` · `tail /opt/dhaka-kacchi/logs/intel-deploys.log` |
| SLOs | 99.5% availability, 95% of successful probes < 300 ms (probe of `/health/deep` every 30 s). Burn-rate alerts; dead-man's switch via healthchecks.io |

**Mac:**
- launchd `com.dhakakacchi.intel.weekly` runs `scripts/weekly.sh --if-due` on Sundays at 12:00, hourly and at login.
- `uv run intel weekly-status` shows the run progress and history.
- Control Room: `DK Control Room.command`, at http://127.0.0.1:8765.
- MLflow: `uv run mlflow ui --backend-store-uri sqlite:///data/mlflow.db`.
- `.env` holds `DK_WAREHOUSE_*`, `DK_INBOX_*`, `MIP_READER_DSN`, `INTEL_API_URL`, `TELEGRAM_CHAT_ID`, `CI_TELEGRAM_*`,
  plus the settings `INTEL_DATA_DIR` and `INTEL_KEEP_PULLS`.

---

## 6. Lessons worth knowing (details in `docs/postmortems/` and `docs/interview/INCIDENTS.md`)
- **Library defaults leak secrets:** httpx logged the Telegram token, which lives in the URL. The httpx and httpcore
  loggers are now at WARNING (the rest stays at INFO), and a redaction filter for token-shaped strings runs on every
  log record.
- **Make a check precise before making it strict.** Examples: the restore drill checks each backup against *its own*
  schema; the PII scan ignores UUIDs and version numbers; the citizenship contract recognises subtotals.
- **"It started" isn't "it works":** deploys end with an end-to-end query (Grafana → Prometheus) and a smoke test.
- **Performance tests in CI must be invariants** (Little's law), never numbers from the Mac.
- **Ansible `--check` skips `command`/`shell` tasks:** read-only checks carry `check_mode: false`.
- **Small data:** a 75-post test set has a wide CI. Don't tune against a set that's already been seen; the shadow
  ledger is the honest evidence.
