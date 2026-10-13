# S6 results: running without you (measured 2026-10-13, real publisher, real model runs)

What was built: [ADR 0037](../adr/0037-running-unattended.md), [STUDIO.md](../STUDIO.md) ("Running without you").

## What exists
- **A daily check, not a blind schedule.** `uv run intel studio schedule install` puts a launchd job on this Mac (not
  installed yet: that is your go). It runs `intel studio run --if-due --trigger schedule` at 03:30; the studio only works when
  the studio's OWN planned posts run out (fewer than 4 days ahead), nothing ran from the schedule in the last 6 days, and fewer
  than 40 drafts wait for approval. It always makes drafts.
- **Real check today** (`uv run intel studio schedule check`): the studio's posts end in 1.3 days, so it would run. (My first
  version looked at the publisher's `plan_ends`, which includes your hand-planned Sheet posts and said "49 days ahead": it
  would never have run. Fixed to count only the studio's own posts, with a test.)
- **Notifications without a secret.** A scheduled or button-started run ends with a macOS notification and
  `data/studio/last-summary.md`; a failure and the budget alert (80% of the token cap) notify too. The Telegram bot token
  stays on the VPS (rule 4), and dk-publishing's morning digest already tells you when agent drafts wait.
- **Autonomy, measured only** (`uv run intel studio autonomy`): per pillar, format and template, how many of your decided
  posts you approved over 14 days. Today: 2 decided (1 approved, 1 rejected), so nothing clears the bar (80% of at least 10).
  Switched off (`enabled: false`) and the auto-approve path is not built.
- **Models as data** (`config/studio/models.yaml`, `--model agent=name`).

## The model choice (D11): is a cheaper model for the designer cheaper?
The designer is about half a run's tokens, so I measured it on the SAME frozen 4-day inputs (12 ideas, render mode, one run
each: an indication, not a proof):

| | designer on Sonnet | designer on Haiku |
|---|---|---|
| Designer tokens (12 calls) | **121,236** (10.1k per call) | 315,014 (26.3k per call) |
| Designer time per call | 8-31 s | 48-150 s |
| Whole run | 401 s, 287k tokens | 674 s, 484k tokens |
| Ideas made / posts valid | 11 of 12 / 43 of 48 | 12 of 12 / 44 of 48 |
| Spec previews refused | 0 | 0 (12 of 12 previews OK) |

Haiku cost **2.6x the tokens and was slower**, with no gain in posts made. (The 4-5 held posts in both runs were brand-check
and render-QA holds outside the designer.) **Decision: the designer stays on Sonnet.** I did not test local models: there is no
quality signal to compare on (playbook N1), so that waits.

## Tests and gates
- New tests (`tests/test_studio.py`): the due decision in every case (calendar far or near, run too recent, backlog,
  switched off); only the studio's own posts count; the last scheduled run found from the run folders; the launchd job content and
  install/uninstall (with `launchctl` replaced: no real install in tests); a scheduled run notifies and writes the summary, a
  failed one says why, a run you started yourself does not interrupt you; the budget alert reaches you; the per-agent model flag is
  passed and checked; the autonomy table (window, kinds, minimum posts, source).
- `ruff`, `mypy --strict` (intel.studio), `pre-commit`.

## Decisions for the owner
1. **Install the daily check?** `uv run intel studio schedule install`. The first run would happen tomorrow at 03:30 (the calendar
   would be below 4 days) and would create about 120 drafts for you to review, so I recommend running the first 10-day drafts run
   yourself and installing the schedule after you have approved a day or two.
2. **A Telegram message from the Mac?** Needs a new endpoint on the VPS (the token cannot leave it). Today you get a macOS
   notification and the morning digest.
3. **Earned autonomy:** nothing to decide until you have approved or rejected at least 10 posts of a kind.
