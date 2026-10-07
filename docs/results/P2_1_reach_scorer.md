# Phase 2.1 — Content scorer v2: the reach question (2026-10-07)

Pull `20261007T193446Z`-era data (461 posts; ~290 matured with a reach label for training, 73 newest for test).

## Questions tried (training data only, time-ordered CV, before any test look)
| Question / inputs | CV balanced accuracy | AUC |
|---|---|---|
| Engagement (v1 question), today's features | ≈ 0.59–0.60 | ≈ 0.60 |
| **Reach > median of our last 20 posts**, today's features | **0.657** | **0.698** |
| Reach + a TikTok-trained "market prior" score | 0.644 | 0.695 |

## Market transfer: not useful yet (honest negative result)
- **Setup:** a gradient-boosted model on **232k TikTok posts** (does a video beat its creator's own usual?), using
  only caption-level inputs measured exactly like the data platform (length, hashtags, emojis, mentions,
  question, language, topic cues, weekday, hour).
- **Result:** it reaches only **AUC 0.528 on newer TikTok posts**. Caption-level inputs barely explain success
  even on TikTok.
- **On our posts:** a slight signal for reach (AUC 0.58 on training posts) but **no gain on top of today's
  features** (0.644 vs 0.657).
- **Conclusion:** what makes a short video work is in the video. Revisit once the publishing tool stores the video
  (length, first-second motion, on-screen text, audio).

## Test on the newest 73 posts (reach question)
| | Balanced acc. | AUC | Brier |
|---|---|---|---|
| Today's predictor (engagement-trained), on the reach question | 0.629 | **0.755** | **0.199** |
| reach_scorer (chosen: today's features + first-line length + Bengali script) | **0.641** | 0.732 | 0.212 |

- **Verdict: FAIL** (gain +0.012 < +0.02; calibration worse).
- **Key finding:** today's predictor already answers the reach question well. Reach and engagement move together
  (r = 0.73–0.82).
- **Test-set use:** the test set was used twice for reach. The second time, a "today's features on the reach
  label" candidate was added; it was the best variant in training-only CV and had been forgotten. The selection
  still chose the other variant. No further tuning against these posts.

## What was built (so the loop keeps trying, with the live ledger as the judge)
- **Reach label** (`datasets/content.py: reach_label`): reach > median of the previous 20 posts on the same
  platform (rolling, because the accounts grew about 5×; at least 5 earlier posts).
- **One run, two questions:** `intel train content-scorer --target reach`. A `reach_scorer` package and spec,
  approved and served like `content_scorer`. `/v1/predict` stays on engagement (the admin page);
  **`/v1/score` returns every active model**.
- **Weekly run:** trains and judges both. Any model that passes is packaged and offered for approval. The run
  report and the Telegram message list each model.
- **Ledger:**
  - every active model scores every post;
  - outcomes get `reach_median` and `label_reach`;
  - live accuracy per model on its own question, plus every model on the reach question;
  - shown on the Posts page and in `/status`.
- 66 tests.
