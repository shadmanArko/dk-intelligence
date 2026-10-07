# Phase 2.3 — Community map (2026-10-07)

**Question:** which of Berlin's 542 planning areas (LOR) are most promising for delivery, area-targeted ads,
flyers and pop-ups?

## Inputs (public statistics, via the market snapshot; versioned)
- **Residents by origin per area** (AfS SB A I 16, 2026-06-30). South Asian presence per area is published only for
  India; Bangladeshi and Pakistani residents are counted citywide only.
- **Census per area:** population, ages 20–39, 1–2 person households.
- **Area centres:** derived from 70k mapped listings; 540 of 542 areas are located.
- **Biryani sellers per area.**
- **Our own direct orders:** the postcode → area mapping is only counted, never shown.
- **Deliberately NOT used:** the census religion variable.

## Score (transparent; weights in `models/community_map/spec.yaml`)
- **audience** = 0.35 × South Asian origin + 0.25 × meat-and-rice cultures (Turkish, Syrian, Lebanese, Iraqi,
  Afghan, Iranian) + 0.15 × internationals (Anglo, French, Italian, Iberian) + 0.25 × young adults in small
  households. Each input is a citywide percentile rank.
- **score** = audience × reach factor (≤5 km 1.0 · ≤10 km 0.8 · ≤15 km 0.6 · further 0.3).
- **gap** = top 25% audience and no biryani seller in the area.
- The page also lists the biggest audiences regardless of distance (pop-ups, events, ads).

## First result
- **#1: Humboldthain Nordwest** (Wedding, 1 km), a gap. **3 of our 5 located orders come from it**, an independent
  sanity check, because orders are not part of the score.
- **Then:** Leopoldplatz, **Teichstraße (Reinickendorf, gap)**, Gesundbrunnen, Afrikanische Straße (gap),
  Stephankiez (gap), Kameruner Straße (gap).
- **Districts:** Mitte ≫ Friedrichshain-Kreuzberg > Charlottenburg-Wilmersdorf > Reinickendorf (only 16 biryani
  sellers, 6 km away).

## Judge (insights kind)
- **Data:** ≥500 areas, ≥95% located.
- **Stability:** ≥14 of last week's top-20 areas stay in the top 20.
- **Owner review:** the approval message lists the top areas; approving is the plausibility check.
- **Size.**

## Use
- **Use it for:** delivery-zone decisions, area-targeted ads (location + interest), flyers and pop-up locations.
- **Never** to target or exclude people by ethnicity or religion.
