# 0002. Train on the Mac, serve on the VPS

- **Status:** Accepted
- **Date:** 2026-10-02 (back-filled)

## Context
- Training needs the market data platform (≈ 2M rows of public posts, reviews, menus in a local Postgres on the Mac)
  and several GB of RAM for a few minutes once a week.
- Serving needs to be up 24/7 next to the business warehouse and the ordering stack on a small VPS (7.8 GB RAM shared
  with production).
- Running cost target: €0 beyond the existing VPS.

## Decision
- The Mac trains every Sunday (launchd, with catch-up when it was off).
- The VPS only **serves and measures**: it loads approved packages, scores drafts and new posts, records outcomes.
- The only paths between them are the three doors of ADR 0003.

## Consequences
- Good: heavy, bursty work runs where the data and the RAM are; the production box keeps a small, steady footprint
  (`intel-api` 512 MB, `intel-db` 384 MB limits).
- Good: a broken training run cannot take the service down; the current model keeps serving.
- Cost: training depends on the Mac being on. Mitigated by catch-up (`weekly --if-due` hourly) and a 14-day
  Telegram reminder.

## Alternatives considered
- Train on the VPS: competes with production for RAM; the market database would have to move.
- Cloud training (managed notebooks, spot instances): recurring cost and moving data off our machines.
