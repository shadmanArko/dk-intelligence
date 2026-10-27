# dk-intelligence — conventions

The ML platform of Dhaka Kacchi Berlin (an online kacchi biryani kitchen run by the owner, Shadman Arko). Seven models
help decide what to post, when and where. **Train on the Mac (Sundays) → judge → package → upload-only inbox → owner
approves on Telegram → serve and measure on the VPS.** Built and run like a production system; it is also the owner's
interview showcase (ML-engineering leadership).

**Before designing or building any agent or agentic system, read [docs/AGENTIC_PLAYBOOK.md](docs/AGENTIC_PLAYBOOK.md)**
and apply it (applied practices by default; the "not yet" list where its trigger is met); add what each measured
change teaches.

**New session, new account or new chat? Read [docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md) first, then
[docs/PHASE5_BUILD_GUIDE.md](docs/PHASE5_BUILD_GUIDE.md)** (both repos,
where we stopped, what comes next). **Then read [docs/HANDOFF.md](docs/HANDOFF.md)**: current state, open items, decisions waiting
for the owner, and the next task (Phase 5: agents). Then the [architecture](docs/ARCHITECTURE.md) and the
[decision records](docs/adr/README.md) as needed.

## Phases
| Phase | What | Status |
|---|---|---|
| 1 (M0–M8) | walking skeleton: pull → train → judge → package → approve → serve → ledger | done |
| 2 (2.1–2.6) | models that help sell: reach, weakness miner, community map, occasions, funnel, creators | done |
| 3 (H0–H11) | MLOps hardening ([plan](docs/PHASE3_MLOPS_PLAN.md)) | done, except **H10 (GDPR) on PR #4, unmerged by the owner's choice** |
| 4 (P4.0–P4.7) | MCP + RAG ([architecture](docs/PHASE4_MCP_ARCHITECTURE.md), ADR 0025–0030) | done; dk_intel live on the VPS since 2026-10-12 |
| **M8** | the lab makes the media: designer agent → render → drafts at dk-publishing ([results](docs/results/M8_media_pipeline.md), ADR 0031) | done (merged; first live draft 2026-10-12) |
| **5** | **the Content Studio**: one agent system across 4 repos, 150 posts per 10 days ([plan](docs/PHASE5_CONTENT_STUDIO_PLAN.md), approved 2026-10-12): S0–S6 | **S0–S6 done** ([how to run](docs/STUDIO.md); S3 live in dk-publishing; results: [S2](docs/results/S2_studio.md), [S4](docs/results/S4_performance_learning.md), [S5](docs/results/S5_creative_director.md), [S6](docs/results/S6_unattended.md)); what is left is the owner's: install the schedule, approve drafts, accept curator proposals ([build guide](docs/PHASE5_BUILD_GUIDE.md)) |

Older docs (PHASE1_PLAN.md, PHASE2_PLAN.md) say "MCP is Phase 3". That was renumbered on 2026-10-08: Phase 3 became
MLOps hardening.

## Run it (Mac)
```bash
uv sync
uv run intel doctor                     # settings, tunnel, read-only login
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run pre-commit run --all-files       # the same checks CI runs (lint, types, secrets, sealed-code lock, ...)
INTEL_TEST_PG_DSN=postgresql://postgres:ci@127.0.0.1:55432/postgres uv run pytest   # + the Postgres tests
```
The Postgres tests skip without `INTEL_TEST_PG_DSN`. For a throwaway database:
`docker run -d --name pgtest -e POSTGRES_PASSWORD=ci -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16`, and
`docker rm -f pgtest` afterwards. Docker-gated tests: `tests/test_infra.py` (the Ansible playbook in ubuntu:24.04)
and `tests/test_restore_drill.py`. Monitoring as code: `bash deploy/monitoring/check.sh` (promtool, amtool).

## Rules that must not be broken
1. **Claude never gets a shell on the VPS.** VPS changes are copy-paste commands the owner runs as `deploy` and
   pastes back, or the CI deploy, or the Ansible playbook the owner runs ON the VPS (`--check --diff` first). The
   Mac reaches production only through the three doors and intel-api's tailnet URL (`INTEL_API_URL`).
2. **Three narrow doors, never widened** (ADR 0003): door 1 is a read-only tunnel (user `intel`), door 2 is an
   upload-only inbox (user `intelinbox`, rrsync -wo), door 3 is the CI forced-command deploy.
3. **Read-only on the warehouse:** only the `intelligence_reader` role through the tunnel. Never write to the
   harness databases, never touch the ordering DB or the harness's shared Postgres image.
4. **Secrets only in `.env`** (git-ignored, filled in by the owner). Never print or commit secret values; name keys
   only. DSNs are printed only via `settings.redact`. When asking the owner for logs, give a command that redacts
   tokens. The Telegram bot token lives on the VPS only.
5. **Only the VPS polls the Telegram bot** (getUpdates). The Mac never reads bot updates.
6. **A scorer reaches production only through the judge gates plus the owner's approval.** An insights model needs
   the owner's *first* approval; after that, weekly versions that pass every gate apply automatically with a rollback
   offer on Telegram (ADR 0008). Monitoring may trigger retraining; the guardrail may roll back to an earlier approved
   version; nothing automatic ever promotes a scorer.
7. **Pulls are immutable and checksummed**; every model records its pull and market snapshot ids.
8. **Sealed model code** (ADR 0011): the files pinned in `sealed-code.lock.json` (what each plug-in lists, under
   `src/intel/models/` and `src/intel/champions/`) change only together with the model, then
   `uv run python -m intel.sealed --update`. "Cleaning up" sealed files breaks the skew guarantee.
9. **Only pre-posting information as features** for the scorers. The content_scorer and reach_scorer test sets have
   each been used twice: **no more tuning against them.**
10. **Content and fairness:** no religious ritual or deity words (blocked words in the content playbook); ads by area
    and interest, never by ethnicity or religion. The community map (area-level) and the occasion planner (city-level)
    use official statistics on origin as **owner-reviewed** exceptions (2026-10-08 and 2026-10-09); don't reuse
    origin or citizenship data anywhere else without asking.
11. **€0 running cost, no paid AI APIs** (ADR 0012), unless the owner decides otherwise for Phase 4; ask.
12. **Migrations are additive** (ADR 0015, `tests/test_migration_policy.py`). Accepted ADRs are never edited; a
    changed decision gets a new ADR. Next free ADR number: **0044** (0024 is on the H10 branch).
13. **Ratchets only move one way** (ADR 0010): the strict-mypy module list only grows; the coverage floor
    (`fail_under = 80`, precision 2) only rises; 0 fixable HIGH/CRITICAL vulnerabilities.
14. **Never touch harness PR #2** ("RAG System…", by another contributor): the owner said "DO not accept the rag
    push". Do not merge, rebase, resolve or reuse it. Phase 4 RAG is our own, separate work.
15. **Never permanently delete the owner's data without explicit consent** (e.g. `intel privacy minimise`, which
    arrives with H10 / PR #4).
16. **Never commit the owner's own uncommitted edits** (e.g. IDE files, brand-book drafts) or touch their branches.
17. The data platform and harness repos are **public on purpose**: never put IPs, tailnet names or secrets there.
    This repo is private.
18. Times are UTC `timestamptz`; money stays exact.

## Git and CI
- **Pushing to `main` deploys:** CI runs test → image (GHCR, scanned) → deploy by digest to the VPS (smoke test,
  automatic rollback) → Telegram alert on failure. Pushes to `main` don't need the owner's approval; the owner
  delegated "push, check CI yourself, report back". Pull requests run tests and build without deploying.
- **Parallel sessions share this folder.** Background or parallel Claude sessions have switched branches, rebased and
  opened PRs here (postmortem 2026-10-08). At the start of each task: `git status -sb`, `git log --oneline -3`,
  `git fetch -q && git log --oneline -3 origin/main`; if anything is unexpected, read `git reflog` before touching
  files. Sync only with `git pull --ff-only` on a clean tree. Edit scripts must assert the text they replace.
- Rewriting history is allowed only on Claude's own branches (e.g. `feat/GDPR`), with `--force-with-lease`; never
  on `main` or on the owner's branches.
- **Where code goes:** Phase 4 and later code lives in this repo (private) unless the owner decides otherwise. Don't
  edit the harness or the data-platform repos without the owner's go; their own CLAUDE.md rules apply there.
- Commit messages end with the `Co-Authored-By` line the system gives. Free GitHub plan: no branch protection on
  this private repo; CI gates the deploy.

## Delivery standard (every milestone)
Code + tests + an ADR (`docs/adr/`, template 0000) + a results doc (`docs/results/`) with real measured numbers + an
interview story (`docs/interview/STORIES.md`: situation, task, action, result, trade-off). Real incidents get a
postmortem (`docs/postmortems/`). Plans are written as a doc for the owner's approval before building
(see `docs/PHASE3_MLOPS_PLAN.md` for the format).

## How the owner works
- Voice-typed, informal English ("rack" or "rag" means RAG). Read for intent. Short go-aheads: "go", "start", "yes".
- Wants senior-level judgement: a clear recommendation and order, not a menu. Asks "what next?" often.
- At the end of a task: verify yourself (tests, CI, deploy), then report the final result plus **only** what needs
  their decision or input.
- VPS work: step-by-step copy-paste blocks that start with an explicit `cd` (`/opt/dhaka-kacchi/dk-intelligence/deploy`
  for compose commands, the checkout root `/opt/dhaka-kacchi/dk-intelligence` for Ansible), the expected output, and
  what to paste back. Secrets are typed with `read -rsp`, never echoed.
- Dislikes long blocking waits: don't poll CI for many minutes in the foreground; report and move on.
- Likes visual, simple explanations and pages they can see (VPS pages, Control Room, published artifacts).

## Layout
- `src/intel/`:
  - the Sunday pipeline: `weekly.py`, `pipeline.py` (the one step list), `pull.py`, `market.py`, `judge.py`,
    `package.py`, `sealed.py`, `plugins.py` (ModelPlugin, ADR 0009), `reproduce.py`, `loadtest.py`;
  - `quality/` (data contracts), `monitoring/` (drift);
  - `models/<7 models>/` (sealed);
  - `serve/` (intel-api on the VPS: app, registry, measure, monitor, guardrail, lifecycle, telegram, views,
    `migrations/`);
  - `control.py` (Mac Control Room);
  - Phase 5: `studio/today.py` + `studio/web/` (the owner's Today page and the living pipeline picture; guide: `docs/OWNER_GUIDE.md`), `studio/director.py` + `config/studio/craft.yaml` (S5: the creative director), `studio/curator.py`, `performance/` (S4: results per post, the playbook, `uv run intel performance build|join|analyst`), `studio/` (the Content Studio: ports, settings, strategist, crews, render and upload pipeline, console), settings in `config/studio/*.yaml`, run it with `uv run intel studio run` ([STUDIO.md](docs/STUDIO.md)); `lab/agentic-slice/` is the older lab (demo mode), it imports `media` and `handover` from `studio/`;
  - Phase 4: `capabilities/` (plain-Python tool logic), `adapters/` (intel-api, packs, pgvector), `rag/` (knowledge
    packs: clean, chunk, embed, rerank, store, gate, ship), `mcp_server/` (the dk_intel gateway, servers, kit,
    prompts, profiles, budgets, HTTP + tokens). Docs: `docs/mcp/` (catalog, clients, threat model, Phase 5 hand-over),
    `docs/runbooks/mcp.md`.
- `deploy/`: Dockerfile, compose (intel-api, intel-db), deploy.sh, smoke.py, backup.sh, restore_drill.sh,
  `monitoring/` (Prometheus, Alertmanager, Grafana, rules and their tests).
- `infra/`: Ansible pull-mode playbook for the VPS host (ADR 0022).
- `docs/`: ARCHITECTURE, PHASE plans, HANDOFF, QUALITY, SLO, RELIABILITY, SCALING, `adr/`, `results/`,
  `runbooks/`, `postmortems/`, `interview/`.
- `data/` (git-ignored): pulls, market, runs, packages, ledger, weekly, logs, `mlflow.db`.
- Sibling repos: `../dhaka_kacchi_ai_harness` (VPS stack, warehouse, ordering; read only when needed) and
  `../MarketIntelligence Data Platform` (market data, brand docs in `docs/brand/`).

## Adding a model
Create `src/intel/models/<name>/` with `__init__.py`, `spec.yaml`, the code and `plugin.py`
(`PLUGIN = <ModelPlugin subclass>()`, listing its sealed files), add a data contract for any new input table, run
`uv run python -m intel.sealed --update`, and commit both. The Sunday run, packaging, the pipeline diagram (Control
Room and the VPS Training page) and the Telegram texts pick it up. The VPS Insights page and the scorer serving path
name models in code: add a section in `serve/views.py` + `insights_page` in `serve/app.py` (insights), or a serving
path in `serve/app.py` (a new scorer).
