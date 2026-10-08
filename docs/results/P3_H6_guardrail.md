# Phase 3 · H6 — automatic rollback on live degradation (2026-10-08)

## What exists now
| | |
|---|---|
| Evidence | paired McNemar test vs the shadow baseline on the same posts (p < 0.05, model wrong alone more often); collapse to chance; calibration gap > 25%; all need ≥ 40 matured outcomes of the version |
| Safety rails | mode on / dry_run / off · owner pin (`/keep`, 14 days, audited) · one automatic rollback per model per 7 days · only to an earlier human-approved version |
| Action | registry rollback (lifecycle triggers + audit trail, actor `guardrail`, reason recorded) → models reload → Telegram: why, `/rollback` to undo, `/keep` to stop |
| Visibility | `/v1/monitoring` → `guardrail.decisions`; `intel_guardrail_actions_total{model,action}`, `intel_guardrail_mode`; Grafana panel |

## Before → after
| A production model gets measurably worse | Before | After |
|---|---|---|
| Detected | H4: dashboard + "retrain" ticket | same |
| Withdrawn | when the owner notices and taps /rollback | within the hour of the evidence, automatically, explained, audited |
| Flapping between two bad versions | n/a | impossible: 7-day cool-down per model |
| Owner disagrees | n/a | `/rollback` undoes; `/keep` pins for 14 days |

## Tests
217 (+13): McNemar on discordant pairs; evidence rules (worse, better is not a reason, too few, collapse,
miscalibration); the safety-rail order (7 cases); end to end on a real database: a significantly worse model rolled
back once with the reason in the audit trail and a Telegram message, then held by the cool-down an hour later; dry run
reports once and changes nothing; first-ever version held (nothing to return to); a better model stays; a pinned
model is held; `/keep` pins and is audited. Coverage 82.9%; strict typing on 23 modules.

## Incident during H6 (not a code defect)
A background task (the AfS citizenship fix) also worked inside this repository: it rebuilt the just-made H5 commit
onto its own branch, pushed, and opened PR #3, which was merged. The deployed code was right, but the local checkout
was left on the H4 commit, so the first H6 edit found the H5 code "missing". Diagnosed from `git reflog` before
touching anything, synced with a fast-forward only, and recorded as a working rule: check branch, reflog and
`origin/main` at the start of each milestone.
