# dk-intelligence — Phase 3 plan: a production-grade MLOps platform

Status: APPROVED 2026-10-08 · MCP + RAG move to Phase 4 · H0 done ([results](results/P3_H0_foundation.md)) · H1 done ([results](results/P3_H1_cicd_supply_chain.md)) · H3 done ([results](results/P3_H3_observability.md)) · H2 done ([results](results/P3_H2_data_quality.md)) · H4 done ([results](results/P3_H4_model_monitoring.md)) · H5 done ([results](results/P3_H5_governance.md)) · H6 done ([results](results/P3_H6_guardrail.md))

## 1. Goal

Phases 1–2 built the **pipes** (pull → train → judge → approve → serve → measure) and eight models. Phase 3 makes the
platform something a large company would accept in production:

- it **notices** problems before a person does;
- it **recovers** on its own;
- it **proves** every decision it took;
- it can be **rebuilt** from scratch with one command;
- it **scales** without a redesign.

Two rules from Phases 1–2 stay:
- **€0 running cost.**
- **Claude never gets a VPS shell.**

Every milestone ends with:
- code and tests;
- a short decision record (ADR);
- an **interview story**: the problem, the decision, the trade-off, and the result in numbers.

## 2. Where we stand: honest maturity check

Google's MLOps maturity levels:
- **Level 0:** manual training.
- **Level 1:** an automated training pipeline with continuous training.
- **Level 2:** CI/CD of the pipeline itself, plus monitoring that triggers retraining.

**We are at Level 1, with parts of Level 2.**

| Area | Today | Gap to close |
|---|---|---|
| Reproducible data | ✅ immutable Parquet pulls, manifest, SHA-256, REPEATABLE READ | data schemas checked on read (pandera) |
| Experiment tracking | ✅ MLflow per run | compare view, tags for every dataset version |
| Validation before release | ✅ judge: leakage, AUC gain + paired CI, per-platform, calibration, live evidence | slice report, minimum-sample rules written down |
| Registry + lineage | ✅ signed-by-hash packages, git commit, code hashes, runtime, model card | formal lifecycle states, audit log page, rebuild check |
| Release | ✅ human approval, shadow ledger, one-tap rollback | automatic rollback on live degradation |
| CI/CD | ✅ lint, format, tests, gated deploy | image built once in CI and deployed by digest, smoke test + auto-rollback, supply-chain scans, alerts on red CI |
| Monitoring | ⚠️ outcomes only (ledger) | feature drift, prediction drift, service metrics, SLOs, alerting |
| Data quality | ⚠️ upstream contracts + dbt tests (mip) | ML-side expectations, freshness SLAs, data cards |
| Reliability | ⚠️ nightly backups | tested restores, runbooks, health of every worker |
| Infrastructure | ❌ VPS set up by hand from guides | infrastructure as code |
| Performance | ❌ not measured | load test, latency budget, scaling plan |
| Engineering hygiene | ⚠️ ruff, tests | type checking, coverage gate, pre-commit, plug-in interface, ADRs |

## 3. Milestones

Each milestone: **what**, **why (the problem it solves)**, **done when**. They are ordered so that each one makes the
next safer.

### H0 — Engineering foundation (SOLID, typing, decisions on paper)
- **ModelPlugin interface:** one `ModelPlugin` protocol (`spec`, `train`, `evaluate`, `package`, `serve`, `explain`)
  that all 8 models implement.
  - `weekly.py`, `package.py`, `pipeline.py` and the Insights page discover models from a registry, instead of
    listing each one.
  - Adding model 9 becomes one folder and zero edits elsewhere (open/closed principle).
- **Type checking:** `mypy --strict` on `src/`.
- **Quality bars in CI:**
  - a coverage gate (≥ 80%, and it can only go up);
  - pre-commit hooks (ruff, mypy, a secret scan).
- **Docs:**
  - `docs/adr/`: one page per architectural decision (context → decision → consequences), backfilled for the
    big ones (train/serve split, three doors, judge gates, shadow ledger).
  - `docs/ARCHITECTURE.md`: C4-style diagrams (context, containers, components), plus the data-flow and trust
    boundaries.
- **Done when:**
  - CI enforces types and coverage;
  - a dummy model plugs in through the interface alone (test);
  - there are 8+ ADRs.

### H1 — CI/CD and software supply chain (both repos)
- **Build once, deploy everywhere:**
  - CI builds the `intel-api` image, tags it with the git SHA, and pushes it to GitHub's container registry (GHCR,
    free for private repos within limits).
  - The VPS **pulls by digest**: it never builds.
  - The exact bytes that were tested are the bytes that run.
- **Safe deploy:**
  - the deploy runs a **smoke test** (`/ready`, a known prediction, the DB migration state);
  - if the smoke test fails, the previous digest is restored automatically;
  - a deploy history is kept on the VPS.
- **Supply chain:**
  - Dependabot (Python, Actions, Docker);
  - `pip-audit`;
  - Trivy image scan (fails on HIGH/CRITICAL with a fix available);
  - an SBOM (CycloneDX) attached to each image;
  - Actions pinned to commit SHAs.
- **Alerts:** red CI, or a failed deploy, in either repo → Telegram (the 4-day red streak on 2026-10-04 is the
  motivating incident).
- **Branch protection on `main`:** required checks, no force-push. You switch these on in GitHub; I give the exact
  clicks.
- **Done when:**
  - a broken image is rolled back automatically (tested with a deliberate bad deploy);
  - scans run on every PR.

### H2 — Data quality for ML
- **Data schemas** (pandera) for every pulled table and the market snapshot: types, ranges, uniqueness, allowed
  values. A pull that breaks them stops the pipeline before training.
- **Freshness and volume SLAs** per source, for example:
  - social metrics newer than 26 h;
  - rows ≥ 80% of the last pull.

  A breach is reported, not silently trained on.
- **Data cards**, generated per pull: rows, time range, missingness, distributions, and changes vs the previous
  pull.
- **Done when:** a corrupted fixture pull is rejected with a readable reason (test), and data cards appear on the
  Training page.

### H3 — Service observability and SLOs
- **Prometheus `/metrics`** on `intel-api`:
  - request rate, latency histograms and errors per endpoint;
  - predictions per model and version;
  - worker heartbeats (inbox, Telegram, measurer, reports);
  - DB pool;
  - ledger lag.
- **Logs:** structured JSON with a request id, and secrets redacted (the bot-token incident is the reason).
- **Prometheus + Grafana on the VPS:**
  - tailnet-only, behind memory limits (≈ 300 MB total; to be confirmed against the VPS memory);
  - dashboards stored as code in the repo.
- **SLOs with error budgets:**
  - `/v1/predict` p95 < 300 ms;
  - 99.5% availability over 30 days;
  - ledger outcomes < 2 h late.
- **Alert rules → Telegram:**
  - SLO burn rate;
  - a stalled worker;
  - disk;
  - backup age;
  - certificate or tailnet problems.
- **Done when:**
  - killing a worker produces a Telegram alert within 5 minutes;
  - the dashboard shows the SLOs.

### H4 — Model monitoring and drift-triggered retraining
- **Log the serving-time features** of every scored post (already partly in the ledger), so training and serving
  distributions can be compared.
- **Drift statistics:**
  - **feature drift:** PSI and Kolmogorov–Smirnov tests per feature, live vs the training window;
  - **prediction drift:** score distribution shift;
  - **label drift:** base rate shift;
  - **calibration over time** (reliability by week).
- **Per-slice performance** (platform, content type, posting hour) in the ledger summary.
- **Policy as code:**
  - "drift above threshold on ≥ 2 key features, or live AUC below the floor" → retraining is triggered (next Mac
    window) and you get a Telegram note;
  - otherwise the weekly schedule stands.
- **Done when:** a synthetic shift in a test triggers the alert and the retrain flag; the drift charts are on the
  Training page.

### H5 — Model governance
- **Lifecycle states:** `candidate → staging (shadow) → production → archived`. Every transition is stored with who,
  when and why, in an append-only **audit log** with its own page.
- **Promotion policy:** a challenger must shadow for N posts (live gate) before it can be promoted. This is already
  the rule for scorers; now it is enforced by the state machine.
- **Model cards v2:** add intended use, out-of-scope uses, slice metrics, a calibration plot, known limitations,
  ethical notes (no ethnicity or religion targeting), data card link.
- **Reproducibility check in CI:** rebuild a model from its recorded lineage (data pull + code + seed) and assert the
  same metrics within tolerance. The claim "anyone can reproduce this model" is tested, not just stated.
- **Done when:** the audit log shows every past decision, and the rebuild test passes.

### H6 — Safer releases and automatic rollback
- **Guardrails on the live model:**
  - if the production model's live AUC or calibration drops below the floor over a rolling window, it rolls back
    to the previous production version **automatically**;
  - you are told why, and can override.
- **Interleaving for insights models:** changes (weekly) are diffed against the last approved version. Small diffs
  auto-refresh (as today); large diffs need a fresh approval.
- **Done when:** a simulated degradation rolls back with no human action (test).

### H7 — Reliability and disaster recovery
- **Backup restore drill**, weekly and automatic:
  - restore last night's `intel-db` dump into a scratch container;
  - check row counts and the latest ledger entry;
  - report the result.

  An untested backup is not a backup.
- **Targets written down:**
  - **RPO:** at most 24 h of lost data;
  - **RTO:** service back within 30 min.

  The drill measures both.
- **Runbooks** (`docs/runbooks/`): API down, DB full, Telegram dead, bad model live, Mac lost, VPS lost.
- A **postmortem template**, plus two real postmortems written up from incidents we already had:
  - the token in the logs;
  - the red CI.
- **Done when:** the restore drill has passed twice, and each runbook has been walked through once.

### H8 — Infrastructure as code
- **Ansible playbook in `infra/`, run *on the VPS by you* with `ansible-pull` (connection: local).** It describes
  everything I guided you through by hand:
  - users and the three doors (keys, forced commands, permitopen);
  - Docker networks;
  - compose projects;
  - cron;
  - `tailscale serve`;
  - backup jobs;
  - the mip collector.
- **Why local, not push from the Mac:** a push from the Mac would need an SSH key with a shell on the VPS on the
  machine Claude works on. That would break the three-doors rule. Pulling keeps the trust boundary.
- **Idempotent:** a second run changes nothing (checked with `--check --diff`).
- **Done when:** a fresh throwaway VM (or container) reaches the same state from the playbook alone.

### H9 — Performance and the scaling story
- **Load test** (k6, in CI against a local stack):
  - throughput and p95 at 1×, 10× and 100× today's load;
  - the latency budget split into parse / features / model / DB.
- **Batch scoring endpoint** and model warm-up at start. Measure cold vs warm start.
- **`docs/SCALING.md`:** what breaks first, and what replaces it. For example:
  - a feature store (Feast) when there are > 1 consumer and online features;
  - a queue (Redis or NATS) when the workers outgrow threads;
  - Kubernetes when there are > 1 host;
  - Ray or Spark when training data passes memory.

  Each with the trigger metric. This is how a VP of engineering answers "how would this scale?"
- **Done when:** the load test numbers are in the README, and the SLO holds at 10×.

### H10 — Responsible AI, privacy and compliance (GDPR)
- **Fairness and policy rules as tests:**
  - no ethnicity or religion targeting fields in any model output;
  - the blocked-words check on all generated text;
  - the per-platform "no slice worse" gate kept.
- **Data protection:**
  - a record of processing (Art. 30-style, one page);
  - a retention policy per table, with automatic deletion jobs (e.g. raw request logs 90 days);
  - a PII scan on pulls (emails, phones) that fails the pipeline;
  - the salted-hash rules documented.
- **Done when:** the policy tests run in CI, and the retention jobs are live.

### H11 — Interview kit (built alongside, finished last)
- **`docs/interview/`:**
  - a one-page architecture overview;
  - a 2-minute and a 10-minute talk track;
  - 8–10 STAR stories (one per milestone + the real incidents);
  - "trade-offs I made and why";
  - "what I would do with 10× team / 100× data";
  - likely interviewer questions with answers.
- **Done when:** you can give the 10-minute version from the one-pager alone.

## 4. Order and size

| # | Milestone | Sessions | Depends on |
|---|---|---|---|
| H0 ✅ | Engineering foundation | 2 | — |
| H1 ✅ | CI/CD + supply chain | 2 | H0 |
| H3 ✅ | Observability + SLOs | 2 | H1 (deploy by digest) |
| H2 ✅ | Data quality for ML | 1 | H0 |
| H4 ✅ | Model monitoring + drift retraining | 2 | H2, H3 |
| H5 ✅ | Governance | 1–2 | H0 |
| H6 ✅ | Auto-rollback | 1 | H4, H5 |
| H7 | Reliability + DR | 1 | H3 |
| H8 | Infrastructure as code | 2 | H1, H7 |
| H9 | Performance + scaling | 1 | H3 |
| H10 | Responsible AI + GDPR | 1 | H2 |
| H11 | Interview kit | ongoing | all |

About 16–18 sessions. The order above front-loads the parts that **protect the business** (a model can't silently
break, and a deploy can't take the service down).

## 5. Decisions in this plan (say if you disagree)
1. **Self-hosted Prometheus + Grafana on the VPS (tailnet-only), not a cloud monitoring service.**
   - Pros: no data leaves our servers, €0.
   - Con: ~300 MB of RAM. If the VPS can't spare it, the fallback is Grafana Cloud's free tier, which only ever
     receives metrics, never business data.
2. **GHCR for images (private), deployed by digest.** The VPS needs a read-only package token. You create it; the
   steps will be in the guide.
3. **Ansible pull-mode on the VPS** (not push from the Mac), to keep "Claude never gets a VPS shell".
4. **No Kubernetes, Kubeflow or feature store now.** They're written into `SCALING.md` with the trigger that would
   justify them. Choosing the right weight of tooling is part of the design.
5. **The judge stays the single gate.** Drift and guardrails can *trigger* retraining or *roll back* to a
   previously approved model. They never promote a new one without you.

## 6. What I need from you
| When | What |
|---|---|
| Now | Approve or change the plan and the decisions in §5 |
| Now | On the VPS: `free -h && df -h /` (to size the monitoring stack) |
| H1 | Create a GHCR read-only token on the VPS; switch on branch protection (exact clicks provided) |
| H8 | Install Ansible on the VPS (`sudo apt install ansible`) and run the playbook in check mode first |
| Each milestone | Push; read the interview story and ask me anything that isn't clear |
