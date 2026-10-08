# 0008. Insights refresh automatically after a first approval; scorers never

- **Status:** Accepted
- **Date:** 2026-10-07 (back-filled)

## Context
Analytical models (weakness miner, community map, occasion planner, funnel, creator matcher) produce new content every
week (new dates, new reviews). Asking for approval every Sunday trains the owner to click without reading. Scorers
change decisions about every post.

## Decision
- An insights model needs a human spot-check before its **first** approval.
- After that, a weekly version that passed every gate is applied automatically; Telegram says so and offers rollback.
- Scorers are never auto-approved.

## Consequences
- Good: approvals stay rare and meaningful.
- Risk: a large weekly change slips through. Planned mitigation (H6): diff against the last approved version; a large
  diff needs a fresh approval.
