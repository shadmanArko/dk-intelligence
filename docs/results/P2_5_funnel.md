# Phase 2.5 — Funnel and attribution plumbing (2026-10-08)

**Question:** where do website visitors come from, how far do they get, what stops them, and which posts and platforms
bring orders?

## Data and joins (warehouse pull)
- **PostHog sessions** (`$session_id`). Entry source = UTM source, else the entry referring domain, classified into
  facebook, instagram, threads, whatsapp, youtube, tiktok, google, other search, other site, direct/unknown.
  Test hosts (localhost) and debug events are excluded.
- **Steps per session:** visit → menu/order page → cart started → checkout (clicked, fulfillment, fee quoted, date) →
  order submitted.
- **Orders:** `order_submitted` ↔ `raw_orders_direct`, matched on the same total (subtotal + delivery fee) within
  3 minutes. All 5 live submissions matched to the second. Cancelled orders do not count as orders.
  - The site's own `event` table uses different session ids (0 overlap) and carries no source, so PostHog is the base.
- **Per post (estimate):** visits from the same platform within 48 h after a post, cut at the next post.
- **Privacy:** only counts leave the step. An automatic gate scans the output for ids, emails and postcodes.

## First result (2026-09-14 to 2026-10-07)
| Visits | Menu | Cart | Checkout | Submitted | Real orders |
|---|---|---|---|---|---|
| 277 | 78 | 28 | 30 | 5 | 3 |

- **Checkout leak:** 25 of 30 visits that reached checkout left.
  - 5 of them show an error: 2 registration failures, 1 login failure, 1 "delivery not available", 1 rage-click.
  - **20 left silently.** Action: test the checkout on a phone and consider ordering without an account.
- **Sources: 88% unknown.**
  - All 3 real orders came from unknown sources.
  - Facebook brought 13 visits (9 reached the menu, 4 started a cart, 0 orders).
  - Google 7, WhatsApp 2, Instagram 2, Threads 2.
- **Tracked links: 0.** The link builder (admin) is ready but unused. One link per post makes attribution exact.

## Next for attribution (when the data exists)
- **Model:** at ~50 orders with a known source, model which platform / format / occasion brings orders (not likes).
- **Untagged traffic:** lift vs a posting-free baseline.
- **Where links are not allowed:** codes or "comment KACCHI".

## Where it shows
- **The Insights page:** a funnel bar chart, "What to do", checkout friction, by source, and per-post visits.
- **The Control Room:** a Funnel box (fed by the warehouse pull).
- **Telegram:** a first approval, then auto-refresh.
