# M3 — Content scorer v1 vs champion (2026-10-07)

Pull `20261007T155754Z`, market snapshot `20261007T163638Z`. Same split as the M2 benchmark: train 298 posts
(26 Apr–24 Aug), test the newest 75 matured posts. The champion recipe is retrained on the same 298 posts.

Run: `uv run intel market pull && uv run intel train content-scorer` → `data/runs/<id>_content_scorer/`
(model.joblib, report.json, test_predictions.csv) + MLflow (`data/mlflow.db`).

## Result: the judge said FAIL, twice, and was right

| Run | Chosen on training CV | CV (train only) | Test balanced acc. | AUC | Brier | Verdict |
|---|---|---|---|---|---|---|
| Champion recipe | — | 0.596 | **0.625** | 0.696 | 0.213 | stays |
| 1: candidates = champion + market/occasion; all features; small tree | logreg, champion + market (C=0.001) | 0.566 | 0.623 | 0.724 | 0.247 | FAIL (gain −0.002, Instagram −0.03, Brier worse) |
| 2: + "champion + first-line length + Bengali script" | that set (C=31.6) | **0.608** | 0.560 | 0.626 | 0.237 | FAIL (gain −0.065, Instagram −0.13) |

Run 2 is textbook overfitting: it looked better than the champion on training CV and was clearly worse on unseen
posts. The gates exist for exactly this, and they caught it automatically. **The test set has now been used twice;
no further variants are tried against it.** From here, only new posts (next pulls) and the live ledger (M6)
judge new ideas.

## What we learned
- **298 posts are too few for more features.** In a one-feature-at-a-time check (training CV only), only
  first-line length and Bengali script helped, by about +0.012 each, which is within noise. Topic cues, market lift,
  hashtag bands and occasion closeness all made it worse at this size.
- **TikTok/YouTube market lift does not transfer to our FB/IG engagement label** (market_cue_lift −0.016).
  Reasons: a different platform, a different audience size (creators with followings vs. a 5-month-old account),
  and a different label (peer lift on views vs. our likes + comments + shares).
- **The champion is simple and hard to beat now.** Its signal is mostly timing and format (Saturday, Tuesday,
  night, Reels).

## What changes because of this
1. **The weekly loop keeps trying automatically.** Every Sunday a new pull adds ~15–25 posts. The same candidates
   are retrained and judged on the newest posts, with no human tuning.
2. **The ledger (M6) is the real judge.** Every new post is scored by both models before its results exist. After
   30–50 posts we know which one is better live, without the reuse problem.
3. **Better label, Phase 2:** reach relative to the account's recent posts (the "who sees it" question) is closer
   to what market data measures, and may let market knowledge help.
4. **More data per post, Phase 2:** the video itself (length, first-second hook, on-screen text). The caption alone
   carries little signal.

## Pipeline status (what M3 was really for)
Done and tested (31 tests):
- market snapshot from the data platform (versioned, checksummed);
- a plug-in model folder (`spec.yaml`, `features.py` shared by training and serving, `train.py`, `predict.py`);
- time-ordered CV candidate selection with a simplicity tie-break;
- the judge with all 7 gates, plus a paired bootstrap of the gain;
- MLflow logging;
- a run folder with the model bundle (0.01 MB, 8 ms per post).
