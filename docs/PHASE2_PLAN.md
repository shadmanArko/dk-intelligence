# dk-intelligence — Phase 2 plan: models that help sell

Status: DRAFT for owner approval · 2026-10-07

## 1. Goal

Phase 1 built the pipes: pull → train → judge → approve → serve → measure. Phase 2 puts **sales and marketing
models** through those pipes, answering the questions that grow orders:

1. **What should we post?** Content scorer v2 (a better question than v1).
2. **What do Berlin biryani buyers dislike about competitors?** Competitor weakness miner.
3. **Where are our customers, and where should we deliver and advertise?** Community map.
4. **When, and for whom (occasions)?** Timing and occasion planner.
5. **Which post brought which order?** Funnel and attribution, using the link builder.
6. **Which creators should we work with?** Creator matcher.

Every model uses the Phase 1 rules:
- one plug-in folder;
- the judge plus your approval;
- shown in the Control Room diagram and on the VPS;
- weekly retraining on the Mac.

## 2. Data we already have (checked 2026-10-07)

| Source | What | Size |
|---|---|---|
| VPS warehouse | own posts + metrics over time, web events, Search Console, orders | 461 posts, 6.9k snapshots, 2.4k events, 9 orders |
| Market platform: reviews | Wolt / Lieferando / Google reviews of the **513 listings that sell biryani** | 42.7k reviews, 13.7k with text, 3.4k negative with text |
| Market platform: menus | offerings incl. prices | 1.2M items, 2,261 biryani items |
| Market platform: people | population by district × citizenship (AfS), community ↔ country ↔ dish map | 21.6k rows |
| Market platform: social | public posts (TikTok, YouTube, Reddit), accounts | ~1.9M posts, 7.6k accounts |
| Market platform: calendar | occasions per community (Eid, Diwali, Christmas, Karneval, …) | 25k days |

## 3. The models, in recommended order

### 2.1 Content scorer v2 (1–2 sessions)
- **Why:** v1 asks "more likes + comments + shares than usual?". The answer depends on things outside the post, and
  ~300 posts cannot learn it.
- **v2 asks two questions instead:**
  1. Will this post reach more people than our recent posts (reach vs a rolling median)?
  2. Will it beat the market peer group? This uses the market platform's peer-lift label, so the 1.9M market posts
     can teach the model, not only our 300.
- **Judge:** the same gates, plus the live ledger: from now on every post has pre-registered predictions.
- **Later:** video features (length, first-second motion, on-screen text) once the publishing tool stores the
  video files.

### 2.2 Competitor weakness miner (2 sessions)
- **What:** for the 13.7k review texts of biryani sellers, it finds which **aspects** customers praise or complain
  about.
  - Aspects: taste and spice, rice texture, meat quantity and quality, portion size, price and value, delivery time,
    temperature on arrival, authenticity, hygiene, packaging, friendliness.
  - Breakdowns: per competitor, per district, per platform, and over time.
- **Output:**
  - "Top 5 complaints about biryani in Berlin" → content angles and promises we can keep (e.g. "rice never dry: dum
    sealed 6 h").
  - Competitors that are weak in districts we can deliver to.
- **How:**
  - A multilingual sentence-embedding model run locally (free) plus a seed phrase list per aspect in German and
    English.
  - Quality is checked on 200 reviews hand-checked by you (≈30 min, with a simple page).
  - No paid AI APIs.
- **Judge:** aspect accuracy on the hand-checked set ≥ 80%, and a stability check from week to week.

### 2.3 Community map (1–2 sessions)
- **What:** a score for each Berlin district (and planning area where available). It combines:
  - the size of our target communities (Bangladeshi, Indian, Pakistani, Arab, Turkish, Afghan, …, and German
    food-adventurous areas);
  - biryani demand (review volume and ratings);
  - competitor density and weakness (from 2.2);
  - distance from Wedding (the 5 / 10 / 15 km delivery fee bands).
- **Output:**
  - a map page and a ranked list: "where to advertise, where to deliver, where to do a pop-up";
  - suggested ad areas (by district, never by ethnicity or religion: platform rules and our own rule).
- **Check:** against where our website visits and orders come from (weak today with 9 orders; grows).

### 2.4 Timing and occasion planner (1 session)
- **What:** for the next 30 days, which occasions matter to which audience, when to start posting (lead days), and
  which posting windows work. It uses the occasion calendar, market timing lift, and our own ledger.
- **Output:**
  - a calendar on the VPS;
  - a weekly "next 2 weeks" Telegram note;
  - input for the content generation in Phase 4.
- Respects the content rules: neutral "festive season" wording, no religious ritual words.

### 2.5 Funnel and attribution (plumbing now, model later)
- **Now:**
  1. Join `tracked_link` clicks → website sessions (PostHog) → orders per post and per platform.
  2. Show it per post on the Posts page.
  3. Put the prediction id into `utm_content` when a link is created for a scored post.
- **Model (when ≥ ~50 orders with a known source):** which platform, format and occasion bring orders, not just
  likes. For posts without links: lift-based estimates (traffic on posting days vs a baseline).
- **Needs from you:** use the link builder on every post where links are allowed, and codes or "comment KACCHI"
  where they are not.

### 2.6 Creator matcher (1–2 sessions, after 2.3)
- **What:** ranks Berlin food creators from the 7.6k social accounts by:
  - topic fit (biryani, South Asian, halal, street food);
  - audience communities;
  - engagement vs peers;
  - posting consistency.
- **Output:** a shortlist with example posts, for collaborations. Contacting them stays with you; paid or gifted
  posts must be labelled "Werbung".

## 4. Platform changes Phase 2 needs
- **Judge types:**
  - classifier gates (as today) for scorers;
  - a "backtest + stability + human spot-check" gate for analytical models (2.2–2.4, 2.6). A score you cannot
    check against reality is never auto-approved.
- **More data in the weekly pull:** reviews, offerings and community tables from the market platform (versioned
  like the warehouse pulls).
- **Diagram and pages:** each model becomes a box in `pipeline.py`. A new **Insights** page on the VPS: district
  map, competitor weaknesses, occasion calendar, creator shortlist.
- **`/v1/score`:** returns every active model's answer for a draft, ready for MCP in Phase 3.

## 5. Decisions taken in this plan (say if you disagree)
1. **No paid AI APIs in Phase 2.** Local open models only; the cost stays €0.
2. **Ads and audiences by area and interest, never by ethnicity or religion.**
3. **Analytical models need a human spot-check before their first approval.**
4. **Order:** 2.1 → 2.2 → 2.3 → 2.4, with 2.5's plumbing alongside; 2.6 last.

## 6. What I need from you
| When | What |
|---|---|
| Now | Approve or change the order |
| 2.2 | ~30 min: confirm or correct the aspects on 200 reviews (simple page, one click each) |
| 2.5 | Use the link builder on every post with a link; codes where links are not allowed |
| Ongoing | Keep posting: every post now trains and tests the models |
