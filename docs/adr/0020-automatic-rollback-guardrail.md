# 0020. Automatic rollback of a degraded production model (the guardrail)

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
H4 (ADR 0018) made degradation visible and lets monitoring ask for *retraining*. Retraining takes a Mac, a judge and
an owner's approval; meanwhile a production model that went bad keeps answering every draft and scoring every post.
The earlier approved version is one registry call away. But automation that acts on production can also be wrong,
so it needs stronger evidence than a request to retrain, and it must never flap between two versions.

## Decision
Hourly, after the monitor, the guardrail (`serve/guardrail.py`) evaluates every active scorer.

**Evidence** (any one; each needs ≥ 40 matured 7-day outcomes of THIS version):
- **worse than the previous system on the same posts.** Every post is also scored by the harness predictor (the
  shadow baseline). McNemar's exact test on the posts where exactly one of the two was right: the model must be
  wrong alone more often than right alone, with p < 0.05. Paired, so it is fair at small samples and immune to
  "the posts this month were harder";
- **collapsed** to chance: live balanced accuracy ≤ 0.5 although the test result was ≥ 0.6;
- **miscalibrated**: |mean predicted probability − observed share| > 25%.

**Safety rails, in this order:** mode `off` (nothing) / `dry_run` (tell, once per version, never act) / `on`; a pin
by the owner (`/keep`, 14 days, audited); at most one automatic rollback per model per 7 days (no ping-pong); only to
an earlier version a human approved (otherwise hold and tell).

**Acting:** the rollback goes through the registry, so the database lifecycle rules and the hash-chained audit trail
apply (actor `guardrail`, the reason recorded). The models reload at once; Telegram says why and how to undo
(`/rollback`) or prevent it (`/keep`). Metrics (`intel_guardrail_actions_total`, `intel_guardrail_mode`) and
`/v1/monitoring` show every decision.

**Default `on`.** The evidence bar is high and the action only returns to a version the owner already approved; with
a few posts a week, a dry-run period would delay protection by months. `INTEL_AUTO_ROLLBACK=dry_run` in
`deploy/.env` switches to report-only at any time.

## Consequences
- Good: a production model that is measurably worse than the previous system is withdrawn within the hour of the
  evidence being there, with an explanation, an audit entry and a one-tap undo.
- Good: no automated *promotion* exists anywhere: monitoring may trigger training, the guardrail may restore an
  approved version, only the judge plus a human can put a new model in production.
- Cost / limit: outcomes mature after 7 days and 40 are needed, so the guardrail is a slow, sure net, not a fast
  one. Fast symptoms (errors, latency, the service down) are H1's smoke test and H3's SLO alerts.

## Alternatives considered
- Thresholds on live accuracy alone: noisy at this sample size and blind to "every model would have struggled this
  month"; the paired test against the baseline on the same posts is both fairer and more sensitive.
- Canary / traffic splitting: no traffic to split; shadow scoring gives every post to both systems for free.
