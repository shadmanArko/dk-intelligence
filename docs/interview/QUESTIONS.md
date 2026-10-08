# Likely questions, with answers

Short answers first. Lead with the answer, then the reason, then the number. Expand only if they lean in.

## The sceptical ones (expect at least two)

**"Isn't this massively over-engineered for a small kitchen?"**
Parts of it would be, so I wrote down the line. Everything here costs €0 a month and protects against a failure that
already happened or is cheap to cause: a bad deploy, bad data, a degraded model, a lost database. What I *didn't*
build (Kubernetes, a feature store, blue/green, Kubeflow) has a written trigger in SCALING.md. And the project's
purpose was also to practise production ML at full rigour on a real system with real stakes, which I say openly.

**"75 test posts? That's not a meaningful evaluation."**
Agreed that it's small, which is why the judge doesn't trust a point estimate:
- it uses a paired bootstrap CI on the gain, a no-platform-worse rule and a calibration check;
- the production model's 95% CI is 0.60–0.78 vs the old system's 0.53–0.74, so I describe it as *likely* better,
  not proven;
- the real evidence accumulates in the shadow ledger: every post scored before its outcome, by both systems. The
  guardrail's McNemar test on those paired outcomes needs at least 40 before it acts.

I also stopped tuning against the test set after it had been seen twice. That's how you avoid fooling yourself at
small n.

**"How do you know the models create business value?"**
Honestly: not causally yet. The ledger shows predictive value on real posts, but at a few posts a week a proper A/B
test of the owner's decisions would take months. What I can show:
- the scorers beat the previous predictor on held-out data;
- the insight models produce concrete, checkable findings: the funnel showed 277 visits → 3 real orders, with 25
  of 30 checkouts abandoned, and 88% of traffic of unknown source because no links were tracked.

With more volume, I'd run timing experiments.

**"Did you write all of this yourself?"**
I designed it and made every decision, and I built it with an AI coding assistant as a pair programmer. That's how I
work now, and it's part of what I'd bring. The ADRs, trade-offs and incident analyses are the evidence of judgement.
I can walk through any file and explain why it's there. The assistant also never had a shell on production: the
same three-door model applies to it as to the laptop. *(Say it plainly and early if asked; it's a strength, not a
confession.)*

**"Why Telegram for approvals? That's not enterprise."**
The approver is one person on a phone. The channel matters less than the controls:
- only the owner's chat id is accepted;
- every decision is in the DB-enforced lifecycle and the hash-chained audit trail;
- the same decision is available on a tailnet-only web page with CSRF protection.

In a company it would be a PR review or a Slack approval wired to the same API.

## ML

**Leakage?** The test set is the newest posts after a cutoff, never shuffled. Features are only what's known before
posting (platform, format, planned time, caption). Market priors come from the snapshot taken *at training time*,
and the champion is scored on the identical rows.

**Why logistic regression / small trees?** 300 training rows. The judge decides between candidates, and simple
models won. A deep model on 300 posts would memorise.

**Class balance and metrics?** Balanced accuracy is the main metric because the label is "above our usual post",
roughly 50/50 but drifting. AUC and Brier are reported, and calibration is a gate because the owner reads the
probability.

**Training/serving skew?** Removed by construction. The feature code is sealed into the package, and the server
refuses a package whose code hash differs from what it serves. A skew check also compares served features with
training features.

**Drift?** PSI per feature and on the score, compared against a reference profile from training, *plus* a chi-square
test, because at small n PSI alone flags noise. A real shift is detected at p < 1e-6, and same-distribution noise
stays quiet. Drift triggers early retraining with a 3-day cool-down.

**Why McNemar for the guardrail?** Both systems predict the *same* posts, so the outcomes are paired. McNemar uses only
the posts where they disagree, which is the right test for "is A worse than B on the same items", and it's exact
(binomial) at small counts.

**The negative result?** A TikTok-trained market prior had AUC 0.528 on newer posts. Caption-level signals don't
transfer across platforms. I documented it and left it out instead of hiding it in an ensemble.

## MLOps and platform

**Walk me through a deploy.** Push to main → tests, types, lint, secret scan → build the image once → trivy +
pip-audit (fixable HIGH blocks it) → push to GHCR → the VPS pulls *that digest* through a forced-command SSH key →
smoke test inside the new container → on failure, automatic rollback to `:previous` (~40 s).

**Walk me through a model release.** That's a separate path from the code deploy. Sunday run → judge → package →
upload-only inbox → the VPS verifies hashes and code match → Telegram approval → active. One-tap rollback, or the
guardrail.

**What happens if the VPS dies?** Prometheus's always-firing Watchdog stops pinging healthchecks.io, which alerts
from outside within 10 minutes. Recovery follows the disaster runbook: rebuild the host with the playbook, restore
the latest backup (rehearsed weekly; RPO ≤ 24 h, RTO ≤ 30 min), redeploy the last digest.

**How do you test alerts?** promtool unit tests: 30 checks across the SLO, ops and model alerts, including the exact
alert text, because a test once caught "500m" accuracy that would have reached the phone.

**Why is the audit trail trustworthy?** Each row's hash covers the previous row's. UPDATE, DELETE and TRUNCATE are
refused by triggers. The chain is verified continuously (a metric that pages) and in every restored backup. A superuser could still
edit it, but would be *detected*. Anchoring the head off-box is the next step.

**How would you scale it?** SCALING.md: split the API from the singleton worker first, then batch and cache features,
queue the ledger writes, pool the DB, add a second host. Each step has a trigger metric. Today's load is ~50,000×
below capacity.

## Security and privacy

**Attack surface?** No public port: the pages are tailnet-only. There are three single-purpose SSH doors: a read-only tunnel and a write-only inbox (both only from the
laptop's Tailscale IP), and a forced-command deploy key for GitHub Actions. Images are scanned,
actions are pinned by SHA, there's no secret in git (gitleaks over full history), and packages are hash-verified and
only approved ones are loaded.

**Personal data?** *(H10, built, merge pending)* A policy file declares every table, its purpose, its level and its
retention; tests keep it true. Pulls copy only declared tables (41 → 5). A precise scan fails the pull on
undeclared PII, retention is audited, and a fairness gate checks protected attributes by name and by lineage.

**Fairness?** No model may use or output religion, ethnicity, nationality and similar fields. Two insight models use
*area-level* official statistics on residents' origin. That's a documented, owner-reviewed exception with an expiry
date, and it's used to choose areas and occasions, never to target or exclude people.

## Leadership

**How do you decide what not to build?** Every "no" gets a trigger. If I can't name the measurement that would turn
it into a "yes", I don't understand the trade-off well enough yet.

**How do you handle incidents?** Mitigate first, then a blameless postmortem whose actions are typed prevent,
detect or mitigate, with at least one systemic fix. Four are written up; the token-in-logs one changed how I treat
library defaults.

**How do you keep quality up without slowing down?** Ratchets: numbers that only move one way, enforced in CI.
Nobody has to be the quality police, and nobody is blocked by a big-bang cleanup.

**What would you do in your first 90 days here?** Listen and map: the model inventory, how models get to production,
what's monitored, the last five incidents. Then pick the one gap that already caused pain, usually evaluation or
rollback, and fix it visibly. Then set the ratchets.
