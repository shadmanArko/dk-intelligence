# Phase 3 · H4 — model monitoring, drift and evidence-triggered retraining (2026-10-08)

## The loop
```
serve (VPS, hourly)  → log the model's input features with each shadow prediction
Mac (each training)  → reference profile of the version (training features, test scores, base rate, test metrics)
                       → inbox → model_reference
monitor (VPS, hourly)→ per active version: score PSI, feature PSI, live accuracy (+ by platform), calibration
                       → /v1/monitoring · Prometheus · Grafana "Model health" · alerts
                       → retrain_requested?
Mac (hourly launchd) → weekly --if-due: due on Sundays OR when monitoring asks (cool-down 3 days)
Sunday run           → skew check: served features vs features rebuilt from the fresh pull
judge + human        → unchanged: monitoring can trigger training, never approve a model
```

## Real-data checks
- Reference rebuilt for the model active today (`cs-20261008T071453-4ea997`) from its saved run: 300 training posts,
  base rate 46%, test balanced accuracy 0.688 (identical to its recorded report). The training set covers only
  Facebook and Instagram; test scores sit between 0.4 and 0.6 (55% in 0.5–0.6): weak discrimination, which is why
  calibration is watched.
- `intel send-references --dry-run` first missed that version: it had not scored a post since approval, so it was
  not in the ledger. Active versions now also come from the VPS's `/ready`.

## Found while testing
- **PSI's noise floor.** On 40 posts over 11 bins, two samples of the SAME distribution give PSI ≈ 0.25, the
  "significant" threshold. The first version flagged `caption_length` drift that did not exist. Drift now also needs
  a chi-square p < 0.01 at the actual sample sizes (`is_drift`); a test pins both directions (noisy same-distribution
  sample: not drift; real shift: p < 1e-6).
- **"500m" in an alert:** Prometheus `humanize` prints 0.5 as `500m`; the live-accuracy alert would have said "live
  balanced accuracy is 500m". Formatted with `printf "%.2f"` now (and the worker-errors alert as `%.0f`).
- Rule-test samples must be as dense as real scrapes: with 10-minute or hourly samples the series looks absent
  between them (5-minute lookback) and every `for:` timer resets.

## Tests
189 (+15): drift math and profiles (numeric, categorical, booleans, unseen, missing), the noise-floor test, the
monitor on a real database (no reference, insufficient, ok, drift, degraded, miscalibrated), reference intake from the
inbox, `/v1/monitoring` and metrics, served-feature logging in real shadow scoring, the skew check (clean and
tampered), reference delivery (new, serving, loaded-but-not-in-ledger, never twice), the drift trigger and its
cool-down; plus 2 promtool test cases for the model alerts. Coverage 79.1%; strict typing on 20 modules.
