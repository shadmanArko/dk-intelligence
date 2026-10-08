# Phase 2.4 — Occasion planner (2026-10-08)

**Question:** which occasions in the next 30 days matter to which Berlin audience, when should posting start, and when
do our own posts reach the most people?

## Inputs (market snapshot, versioned)
- **`occasions_next`:** the data platform's occasion calendar for the next 60 days. It has audience sizes, food role,
  tone, lead days, greetings, bridge dishes and Berlin events.
- **`content_rules.yaml`:** from the playbook. Neutral presentation of religious occasions, blocked words, home
  community, audience shares.
- **`afs_citizenship` + `community_countries.yaml`:** per-country Berlin counts. Canadian Thanksgiving counts
  Canadians, not every British or American Berliner.
- **`content_feature_performance`:** TikTok lift per occasion vs the usual.
- **The warehouse pull:** which weekdays and times our own posts reached more people than our recent posts (with n).

## Method
- **Score** = food-role weight × √(Berliners who celebrate it). This is the same idea as the platform's content
  briefs.
- **Thresholds:** ≥5,000 people; national days ≥20,000.
- **Solemn or remembrance days** become "respectful note, no sales post".
- **Presentation:** neutral labels, greetings and audience per the content rules. Blocked words are removed from
  names and greetings, and raw internal codes never appear in the output.

## Gates
- **Data:** the calendar reaches at least 30 days ahead.
- **Content rules:** an automatic scan of EVERY string in the output. Any blocked word means fail. On the first run it
  caught the raw code `durga_puja`; fixed.
- **Size.**

## First plan (from 2026-10-08)
| Occasion | Audience | Day | Start |
|---|---|---|---|
| Indian festive season | Indian, 49,504 | 18–20 Oct | now |
| Turkish Republic Day | Turkish, 107,051 | 29 Oct | 25 Oct |
| Italian national holiday | Italian, 33,925 | 1 Nov | 29 Oct |
| Russian Unity Day | Russian citizens, 33,908 | 4 Nov | 1 Nov |

- 5 respectful notes (remembrance days).
- Berlin events for pop-ups, e.g. the Street Food Festival at Hauptbahnhof.

## Also in this step: auto-refresh of insights
- **Why:** insights (map, miner, planner) produce a new version every week.
- **Rule:** after the owner approved an insight ONCE (a human channel), later versions that pass every gate are applied
  automatically. Telegram says "🔄 refreshed automatically"; rollback stays one tap.
- **Prediction models (scorers) always need approval.** Both cases are tested.

## Where it shows
- The Insights page: the plan table, respectful notes, events, and own timing tables.
- The Monday report: "Coming up (next 2 weeks)".
- Control Room: an "Occasion planner" box.
