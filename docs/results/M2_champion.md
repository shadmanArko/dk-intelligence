# M2 — Champion reproduced (2026-10-07)

Champion: harness predictor `2026-09-23-logreg-v1` (logistic regression, 5 pre-posting features: platform,
format, weekday, hour band, caption length / hashtags). Pull `20261007T155754Z`.
Run: `uv run intel champion reproduce` → `data/runs/<id>_champion_harness_v1/report.json`.

| Check | Result | Meaning |
|---|---|---|
| A. Recipe on the original data and split | **0.6179** balanced accuracy = published 0.6179 | Our copy of the recipe is exact. |
| Label stability, 360 original posts | 98.6% same label today as on 2026-09-23 | The label is stable; old results still hold. |
| B. Deployed model on posts it never saw | 12 matured posts (32 total since 22 Sep): 0.83, 95% range 0.63–1.00 | Encouraging, but 12 posts is far too few to trust. The ledger (M6) fixes this. |
| **C. Benchmark for the challenger** | **0.625** balanced accuracy (95% range 0.52–0.73), AUC 0.70, Brier 0.213; Facebook 0.60, Instagram 0.67 | Trained on 298 posts (26 Apr–24 Aug), tested on the newest 75 matured posts. **M3 must beat this on the same split.** |

Dataset now: 393 Facebook + Instagram posts (250 / 143), 373 matured (latest snapshot ≥ 7 days after posting),
46% positive.

## Things found
- **The harness extract would now duplicate posts.** `ml/01-data/extract.py` joins every metrics snapshot. On
  2026-09-23 there was one per post; now there are 16. Re-running that script would make 16 rows per post. Our
  dataset takes the latest snapshot per post. Fix it in the harness before it retrains.
- **Post ids differ between the harness's local extract and the VPS warehouse,** so cross-system comparisons
  match on (platform, posted_at).
- **The 95% ranges are wide** (±0.1). With ~75 test posts, a challenger needs a clear margin and live evidence,
  which is why the judge requires +0.02 *and* the ledger gate.
