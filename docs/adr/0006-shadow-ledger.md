# 0006. Shadow ledger: predictions registered before outcomes exist

- **Status:** Accepted
- **Date:** 2026-10-05 (back-filled)

## Context
Offline test sets are small and age. The question that matters is "does the model predict *new* posts well?", and
it must be answered without hindsight.

## Decision
The VPS scores every new post hourly with every active model **and** the production predictor, and stores the
prediction with its time. Outcomes are added at 24 h, 72 h and 7 days; a 7-day outcome may only use a metric snapshot
taken close to that horizon. The ledger feeds the `live_evidence` gate (ADR 0005) and the Posts page.

## Consequences
- Good: an honest, pre-registered online evaluation (a continuous A/B comparison) at zero traffic risk; nothing is
  shown to customers.
- Good: outages are back-filled only where the outcome is not known yet (a prediction after the fact is refused).
- Cost: evidence accrues at the posting rate (a few posts a week).

## Alternatives considered
- Online A/B with real traffic split: no traffic to split; the decisions here are internal.
