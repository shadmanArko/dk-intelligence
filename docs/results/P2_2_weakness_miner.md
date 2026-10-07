# Phase 2.2 — Competitor weakness miner (2026-10-07, awaiting the owner's check)

**Data:** 13,698 Lieferando review texts (>20 characters) of the 239 Berlin places with reviews among the 513 that
sell biryani; 2022-06 to 2026-10; 85% German, 14% English. They come via the market snapshot (`biryani_reviews`,
`biryani_listings`, versioned and checksummed).

## Method (transparent, €0)
- **Clauses:** split at . ! ? ; and at aber / but / jedoch / allerdings / however.
- **14 topics** from a readable phrase dictionary (`models/weakness_miner/aspects.yaml`).
  - Text is folded (ä→ae…); German endings are tolerated (kalt → kalte/kaltes); hyphens split words.
- **Tone per clause:** praise and complaint cue words, flipped by a negation within 3 words ("nicht lecker");
  "zu + adjective" counts as a complaint.
  - Without cue words, the review's stars decide.
  - Wrong/missing items and hygiene/health count as complaints when no clear praise is present.
- **Honest design:**
  - The dictionary was built only from a fixed 10% design slice (review id hash % 10 == 0).
  - The 200-clause owner check comes only from the other 90%: 10 per topic plus 60 with no topic, to measure misses.
  - The page shows no prediction (no anchoring).
  - The dictionary is frozen before the check.
- **Judge** (insights kind): data ≥ 5,000 reviews; owner check ≥ 150 sentences with aspect precision ≥ 0.80 and tone
  accuracy ≥ 0.80; top-5 stability week to week; size. Verdict `pending` until checked.

## First results (not yet approved: they depend on the check)
| # | Topic | Complaints | Negative when mentioned | Mentioned in |
|---|---|---|---|---|
| 1 | Delivery time | 851 | 20% | 32% of reviews |
| 2 | Taste | 728 | 9% | 57% |
| 3 | Wrong / missing items | 561 | 96% | 4% |
| 4 | Meat (dry, tough) | 548 | 45% | 9% |
| 5 | Portion size | 460 | 38% | 9% |
| – | Sauce / oil (watery, oily) | ~450 | 63% | 5% |
| – | Temperature (cold on arrival) | ~430 | 18% | 17% |

- **Meaning for Dhaka Kacchi:** the market's pain points are mostly operational: lateness, missing items, cold food,
  and dry or tough meat.
- **The kacchi model answers several of them directly:**
  - one dish, one cooking day;
  - personal confirmation;
  - meat cooked raw with the rice under dum;
  - free pickup.
- The Insights page lists a suggested "our angle" per topic; each one must be true before it is used.

## Owner steps
1. Control Room → **Review check** → 150–200 sentences (~30 min).
2. Control Room → **Run training now**. If the check passes, the package goes to the VPS and the approval comes in
   Telegram.
3. Approve → the **Insights** page on the VPS shows the topics, district and competitor tables.
