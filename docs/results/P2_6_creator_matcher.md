# Phase 2.6 — Creator matcher (2026-10-08)

**Question:** which Berlin food creators fit Dhaka Kacchi best for collaborations?

## Data
- **Candidates:** 865 non-restaurant TikTok accounts with at least one Berlin post (market snapshot `creators`,
  aggregated in the database).
- **Example posts:** their 3 most-viewed Berlin videos (`creator_posts`).
- **TikTok gave no follower counts.** Engagement = median (likes + comments + shares) / views per video, and size =
  typical views per video. That is arguably a better reach measure than followers.
- **The scraped `is_business` flag is True for every account and is unusable.**

## Rules (spec.yaml, editable)
- **Not creators** (listed separately as "restaurants and brands", 122): business words in the handle, name or bio
  (kebap, burger, imbiss, restaurant, öffnungszeiten, festival, official, visit…) plus an owner-editable
  `exclude_handles` list.
- **Eligible:**
  - ≥5 measured videos;
  - a post within 120 days;
  - ≥10% of videos recognisably about food (the topic dictionary undercounts; the median account is only 3% food).
- **Score:** weighted percentile ranks × size factor.
  - Weights: Berlin focus 0.25 · topic fit 0.35 (biryani ×3, South Asian ×2, rice dishes, meat-and-rice cultures,
    halal, street food, reviews) · engagement 0.25 · activity 0.15.
  - Size factor: nano and micro 1.0, mid 0.7, macro and under-1k 0.4.

## First shortlist (110 eligible)
1. **@anandberlin**: micro, 97% Berlin, 29 South Asian videos;
2. **@marcel_travels**: food tours, 8% engagement;
3. **@naomisberlinfood**: rice dishes incl. biryani;
4. **@moisunrise**: 64 rice-dish videos;
5. **@blondminh**;
6. **@berlinfoodstories**;
7. **@hungryinberlinblog**;
8. **@bln_foodies**: halal.

There is also a South Asian specialists list (e.g. **@zusshuberlin**, 9 biryani videos).

Some noise remains (a BBQ restaurant, a newspaper account). The first approval is the owner's check, and
`exclude_handles` fixes any slip.

## Rules of use
- Public accounts only.
- Contacting creators is the owner's job.
- Gifted or paid posts must be labelled "Werbung" / "Anzeige".
