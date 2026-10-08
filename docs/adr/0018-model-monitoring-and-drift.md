# 0018. Model monitoring: served features, reference profiles, statistically honest drift

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
The shadow ledger (ADR 0006) recorded predictions and outcomes, and the weekly judge used them, but nothing watched
an approved model *between* trainings: whether today's posts look like the ones it learned from, whether its scores
moved, whether it is still right, whether its probabilities still mean what they say. Training was on a calendar
(Sundays), not on evidence. And the features a model actually saw at serving time were not recorded at all, so
training/serving skew could not be measured, only prevented by code hashes (ADR 0007).

## Decision
1. **Log served features.** Every shadow prediction stores the model's input features exactly as the bundle computed
   them (same sealed feature code, same frozen market context): `prediction.features` (additive migration 0005).
2. **Reference profile per model version**, built on the Mac from the saved training run by ONE code path (rebuild
   the training features from the recorded pull, the bundle's frozen context and the split cutoff; test scores from
   the run): per-feature distributions (deciles for numbers, shares for categories, plus missing / unseen),
   test-score distribution, base rate, test metrics. Written by every scorer run; the Sunday run (and
   `intel send-references`) delivers it through the upload-only inbox for every version that is active on the VPS,
   new, or in the ledger, each once.
3. **Hourly monitor on the VPS**, per active scorer version, over its last 100 predictions / matured outcomes:
   prediction drift and per-feature drift (PSI), live balanced accuracy vs the test result (and by platform),
   calibration (mean predicted probability vs observed share, Brier). Exposed at `/v1/monitoring`, as Prometheus
   metrics, on a "Model health" dashboard row, and as alerts (`ModelRetrainRequested`, `ModelLiveAccuracyLow`,
   `ModelMonitoringStale`, all with promtool unit tests).
4. **Statistically honest drift.** Nothing is judged under 30 samples. A shift counts only if it is LARGE (PSI > 0.25)
   **and** REAL: n_eff x PSI is roughly chi-square with (bins - 1) degrees of freedom, so the p-value for these sample
   sizes must be < 0.01. Without this, 40 posts over 11 bins produce PSI ≈ 0.25 from chance alone (found while
   testing): the monitor would have cried wolf every week.
5. **Evidence-triggered retraining.** A model with drifted inputs (≥ 2 features), shifted scores, live accuracy
   > 0.10 below test, or a calibration gap > 15% sets `retrain_requested`. The Mac's hourly `weekly --if-due` reads
   `/v1/monitoring` and trains early, at most once per 3 days. Promotion still goes through the judge and a human
   (ADR 0005): monitoring can trigger training, never approve a model.
6. **Training/serving skew check** in the Sunday run: for posts already scored, rebuild their features from the
   fresh pull with the same version's bundle and compare value by value with what was served; differences are
   reported in the run report and on Telegram.

## Consequences
- Good: the full loop (serve → log → compare → alert → retrain → judge → approve) is automated except the decision.
- Good: drift alarms are rare and meaningful by construction (unit-tested: same-distribution samples stay quiet,
  real shifts are found at p < 1e-6).
- Cost: with a few posts a week, judgements need weeks of data; the dashboard shows the sample size next to every
  number, and "insufficient" is a status, not an error.
- Limit: performance needs matured 7-day outcomes, so it lags a week by nature; drift on inputs and scores is the
  early signal.

## Alternatives considered
- Evidently / WhyLabs / a managed monitor: same ideas, more weight or data leaving the box; PSI + chi-square in ~150
  lines is auditable and enough at this scale (ADR 0013).
- KS test per numeric feature: fine for continuous data, but our inputs are mostly categorical and binned; one PSI
  method for all, with a matching significance test.
- Retrain on every drift signal without a cool-down: a persistent shift would retrain hourly.
