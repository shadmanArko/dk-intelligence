# 0005. Champion vs challenger: fixed gates, then a human

- **Status:** Accepted
- **Date:** 2026-10-04 (back-filled)

## Context
~460 posts is a small, noisy dataset. A retrained model can look better by chance, be worse on one platform, or be
badly calibrated. Promoting on "the new number is higher" would make things worse about half the time.

## Decision
A challenger is compared with the current champion on the **same time-ordered test set** (the newest posts, never
seen in training) and must pass every gate:

| Gate | Rule |
|---|---|
| `data` | the pull is sane (row counts vs the previous pull, required tables) |
| `no_leakage` | no feature uses information from after posting time |
| `better` | balanced-accuracy gain ≥ the spec's minimum **and** AUC not lower; paired bootstrap 95% interval reported |
| `no_platform_worse` | no platform drops by more than the spec's limit |
| `calibrated` | Brier score not worse than the spec's tolerance |
| `live_evidence` | enough pre-registered live predictions (ADR 0006) once they exist |
| `works` | package size and per-post latency within limits |

Passing makes a model a *candidate*. Only the owner's approval (Telegram or web) makes it live; rollback is one tap.

## Consequences
- Good: promotion is a written, testable policy, the same every week.
- Good: the human sees the gate table, not just a score.
- Cost: real improvements smaller than the minimum gain are rejected (by design at this data size).

## Alternatives considered
- Auto-promote on a better metric: unsafe at this sample size.
- Only human judgement: inconsistent week to week.
