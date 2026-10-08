# Trade-offs I made, and why

At VP level the interview is mostly about judgement. Each row is a choice I can defend: what I chose, what I gave up,
and **what would make me change my mind**. That last column is what separates a decision from a preference.

| # | Decision | Alternative rejected | What I gave up | Would change when |
|---|---|---|---|---|
| 1 | Train on a laptop, serve on a VPS (ADR 0002) | cloud training (SageMaker, Vertex) | elastic compute, managed runs | training takes > 1 h, or data no longer fits in memory |
| 2 | A human approves every scorer (ADR 0005) | auto-promote on a passing judge | speed: a model waits for a tap | the owner can't keep up, *and* the live gate has months of evidence |
| 3 | The guardrail ships **on**, not in dry-run (ADR 0020) | weeks of dry-run first | the chance to watch it first | it ever rolls back wrongly (switch: one setting) |
| 4 | Monitoring can trigger retraining, never promotion (ADR 0018) | auto-retrain-and-deploy on drift | fully hands-off operation | never, for scorers: drift says "the world changed", not "the new model is better" |
| 5 | PSI drift *plus* a significance test | plain PSI thresholds | some sensitivity at very small n | volumes grow so raw PSI is stable |
| 6 | Self-hosted Prometheus/Grafana (ADR 0016) | Grafana Cloud | ~300–500 MB of RAM on a shared box | memory becomes the constraint; metrics only, never data, would leave |
| 7 | SLO from an outside-in probe | request-based ratios | real-user latency | traffic reaches ~100s of requests/hour |
| 8 | 99.5%, not 99.9% | a "nicer" number | bragging rights | there's a second operator and a second host |
| 9 | Rollback to `:previous`, no blue/green (ADR 0014) | two live containers | a few seconds of deploy downtime | the SLO budget is being spent by deploys |
| 10 | Sealed model code, pinned by hash (ADR 0011) | "clean up" model code freely | typing fixes in 7 files wait for each model's next change | never: it's the skew guarantee |
| 11 | Hash-chained audit, not write-once storage (ADR 0019) | WORM storage, external ledger | prevention: a superuser can still edit, but it's *detected* | more than one operator, or a regulator asks; next step: anchor the head hash off-box |
| 12 | Ansible in **pull** mode (ADR 0022) | push from CI or the laptop | "one click from CI" | never while the trust model is three narrow doors |
| 13 | Restore drill on the local copy, not B2 (ADR 0021) | full off-site restore | proof the off-site copy restores | B2 credentials can be scoped to read-only restore |
| 14 | No Kubernetes / feature store / Kubeflow (ADR 0013) | the standard MLOps stack | CV keywords | the triggers in SCALING.md (> 1 host; a 2nd feature consumer) |
| 15 | Split API/worker as the *first* scaling step (ADR 0023) | "add uvicorn workers" | nothing: more workers would be a bug (two pollers, double counts) | n/a |
| 16 | Typing and coverage as ratchets (ADR 0010) | strict everywhere at once | some modules stay non-strict for now | each module joins the list when it's next touched |
| 17 | Make checks precise before strict (H2, H7, H10) | turn a noisy check on and tune later | a few days | never: a check that cries wolf gets switched off |
| 18 | Fairness by *lineage*, not field names (H10) | keyword lists only | some simplicity | n/a: names can always be innocent |

## What I deliberately left out (and where it's written down)
- **Distributed training, GPUs:** 300 training posts. The trigger is in SCALING.md row 6.
- **Online learning:** labels arrive 7 days after a post. Weekly retraining matches the label latency.
- **An LLM layer (MCP, RAG):** planned as Phase 4, after the platform is trustworthy. Retrieval over unreliable data
  would make confident nonsense.
- **A full DPIA:** not high-risk processing under Art. 35 (no profiling of individuals, no automated decisions with
  legal effect). The record of processing exists instead *(H10, merge pending)*.

---

## With a 10× team or 100× the data

### 100× the data (~30k posts, ~200M market rows)
1. **Split serving from workers** (SCALING row 1), then replicas behind a load balancer.
2. **Ledger writes through a queue** (row 3). A prediction must not wait 12.7 ms for audit-grade durability.
3. **Columnar, out-of-core training** (Polars / DuckDB), then Ray if a single node can't cope (row 6).
4. **A feature store (Feast) only when a second consumer needs the same online features** (row 7). Today
   sealed code gives one definition for training and serving without it.
5. **Evaluation at scale:** stratified, time-sliced test sets per platform and segment, and power analysis before
   experiments. With more posts, run real A/B tests of post timing instead of only shadow scoring.
6. **Managed Postgres** and PgBouncer; the restore drill against the managed service's point-in-time recovery.

### A 10× team (one person → ~10)
1. **Platform vs model teams.** The plug-in contract (ADR 0009) is already the seam: model teams own a folder, and
   the platform team owns the judge, packaging, serving and monitoring.
2. **The judge becomes a review culture.** Every promotion has a model card and a judge report as the PR artefact,
   reviewed by someone who didn't train the model.
3. **On-call with error budgets.** The SLOs exist; add a rotation and a rule: when the budget is spent, reliability
   work beats features.
4. **Postmortems stay blameless and public**, with the four existing ones as the template.
5. **Paved road:** `intel new-model <name>` scaffolds a plug-in with contracts, a card and tests. The ratchets keep
   quality from regressing as people join.
6. **Access:** the three doors become per-person, audited identities (Tailscale ACLs, short-lived certificates)
   instead of one owner's keys.

### What I'd measure to know it's working
Lead time from idea to approved model · share of challengers that pass the judge (too high means a weak judge) ·
error-budget burn · mean time to detect and to restore · incidents with a completed postmortem.
