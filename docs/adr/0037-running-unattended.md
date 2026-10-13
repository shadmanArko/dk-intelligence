# 0037. Running unattended: a cheap daily check, drafts only, local notifications, measured autonomy, models chosen by measurement

- **Status:** Accepted
- **Date:** 2026-10-13

## Context
- The studio makes a 10-day plan in about 15 minutes. The owner does not want to start it by hand, and does not want to be
  the scheduler. Every output is a draft that a person approves on the review page; that stays true.
- The Telegram bot token lives on the VPS only (rule 4), so the Mac cannot message Telegram directly. dk-publishing already
  sends a morning digest that says "Review today's posts" when agent drafts wait.
- The designer is about half of a run's tokens. Moving a step to a cheaper model is only safe when it is measured.

## Decision
- **A daily check, not a blind schedule.** `uv run intel studio schedule install` puts a launchd job on the Mac (this user
  only) that runs `intel studio run --if-due --trigger schedule` every day at `schedule.yaml: run_at`. The check reads the
  publisher's calendar (no AI) and starts a run only when: the calendar's last planned slot is fewer than `lead_days` ahead,
  nothing ran from the schedule in the last `min_days_between` days, and fewer than `max_awaiting` drafts wait for approval,
  so a backlog is never added to. The mode is `drafts` and nothing in the scheduled path can approve or post.
- **Notifications without a secret.** A scheduled or button-started run ends with a macOS notification and
  `data/studio/last-summary.md`; a failure and the budget alert (at `alert_at` of the token cap) notify too. Telegram stays
  the publisher's job (its morning digest). A Mac-to-Telegram message would need a new endpoint on the VPS: left as an owner
  decision, not built.
- **Earned autonomy is measured, not built.** `uv run intel studio autonomy` reports, per kind of post (pillar, format,
  template), how many decided posts the owner approved over `window_days`, and whether a kind would clear the bar in
  `autonomy.yaml`. `enabled: false` is the shipped state. The auto-approve path does not exist: it would need a dedicated
  publisher key with the approve right and enforcement there in code (never a prompt), and the owner's explicit decision.
- **Models are data and chosen by measurement.** `config/studio/models.yaml` names the model of each agent;
  `--model agent=name` runs an experiment on frozen inputs; a step moves only when the measured posts made, refusals and
  look hold (docs/results/S6_unattended.md).
- One run at a time (the lock), resume, the budget cap and the retry rules from ADR 0034 apply unchanged.

## Consequences
- Good: the calendar stays filled without the owner starting anything; it cannot pile up drafts; every failure and the
  budget alert show up without a secret on the Mac.
- Cost / risk: the Mac must be on at `run_at` (launchd does not wake it); a missed day is retried the next day. The owner
  still approves every post, so a scheduled run that makes weak drafts costs them review time: the autonomy report is how
  that is watched.
- Not done: Telegram from the Mac; auto-approval.

## Alternatives considered
- A Claude Code scheduled task or cron running the studio blindly every 10 days: it ignores whether the calendar needs
  filling and whether drafts are waiting.
- Sending the summary through the bot with the token on the Mac: breaks rule 4.
- Building auto-approval now: a reach and trust decision for the owner, with no data yet (few decided posts).
