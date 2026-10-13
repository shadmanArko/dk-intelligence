# 0038. Mission control from the run's own events, and a plan that starts after what is already scheduled

- **Status:** Accepted
- **Date:** 2026-10-13

## Context
- The plan always started "tomorrow" (`plan.yaml: first_day_offset`, at most 7). After the first 10-day drafts run
  (14 to 23 Oct), the owner could not plan the next 10 days: a new run would plan 14 to 23 Oct again and spend about
  650,000 tokens on days that already have drafts.
- The first owner desk showed a decorative animation (ideas drifting through stations). The owner found it shallow: it
  said nothing about what was happening, how far along it was, or what was slow.
- Each page had its own header; moving between Today, the run page and the guide was by links that differed per page.

## Decision
- **The next plan starts after what exists.** `coverage.py` reads the studio's own posts in the publisher (source api,
  states that still hold a slot; the owner's Sheet posts and cancelled or failed posts do not count), one week at a time
  so neither the publisher's page size nor its ordering can hide a day. It answers: scheduled through D, N days ahead, days
  with nothing, next start D+1. `intel studio run` uses it by default and prints the sentence; `--from YYYY-MM-DD` (or the
  "start on" field on the Runs page) overrides it, never into the past. The start is frozen into the run's inputs, so a
  resume and a replay keep the same days. `intel studio coverage` prints it with no tokens. Today shows it as a card
  ("Plan the next days" when fewer than `lead_days` are covered) and its Run button carries the start date.
- **Mission control replaces the animation.** `telemetry.py` turns `events.jsonl` + `state.json` into one JSON: percent
  (stage weights by how long each stage really takes), elapsed, an estimate of the time left (shown only from 8 %), every
  agent call as a bar (Gantt), a matrix of every idea against every stage, tokens by agent, what is working now, retries,
  tool calls, the last events in words. The page (`/live`) draws a pipeline with waiting, working and done counts, the
  timeline, the matrix and the log. Nothing is invented: a demo (`?demo=1`) is made of synthetic EVENTS that go through
  the same function. The Today page keeps only a one-line progress strip that links to it.
- **One top bar** on every console page (Today, Mission control, Runs, Guide, and the review page, which opens in a new
  tab), added by the server so a new page gets it by writing `<!--NAV-->`.

## Consequences
- Good: the owner can start the next 10 days on any day without duplicating work; a presentation shows real numbers, not
  decoration; the demo is honest because it shares the code path.
- Cost / risk: the calendar read is a few small HTTP calls per Today load. If the publisher is unreachable the answer says
  so and the plan falls back to its own first day. Percent and "time left" are estimates; the page says so.
- Not done: planning more than `horizon_days` at once; a gap between scheduled days is reported, not filled.

## Alternatives considered
- Raising `first_day_offset` above 7: a fixed number that is wrong after a day, and it cannot express "after what exists".
- Using the Calendar's `limit=200` in one read: 150 posts per 10 days means days beyond about 13 are cut off silently.
- Keeping the animation and adding numbers beside it: the animation carried no information.
