# 0039. A Mac that is off catches up, and a run that was cut off is finished

- **Status:** Accepted
- **Date:** 2026-10-13
- Builds on [0037](0037-running-unattended.md) (the check, drafts only) and [0034](0034-the-studio-package.md) (resume).

## Context
- ADR 0037 ran the check once a day at a fixed time with launchd's calendar trigger. If the Mac is powered off at that time
  launchd does not run it later, so that day's check is lost.
- A run takes about 13 minutes. If the Mac crashes, loses power or is shut during it, the run stops without writing its end.
  The lock file it leaves behind made the console show "running" forever, and nothing finished the work.
- The owner works on a laptop that is often closed: the studio must do the day's work when the Mac is next on, and must
  finish an operation that did not complete.

## Decision
- **The check is frequent and cheap, and it decides.** The launchd job runs `intel studio run --if-due` at every login
  (`RunAtLoad`) and every `check_every_minutes` (30). A check reads the calendar once and spends no tokens. It does one of:
  nothing (switched off, a run is working, not yet time, the calendar is far enough ahead, too many drafts wait),
  **resume** a run that was cut off, or **start** a new run. A Mac that was off at 09:30 therefore catches up at the first
  check after it is on; a Mac that stays off does nothing.
- **`run_at` (09:30) is the earliest time a NEW run may start**, in the Mac's local time. It does not delay finishing a cut-off run.
- **What "done" means.** A run is finished when its own log ends with an orchestrator `end` event (done, partial or failed
  are all final: a failed run is reported, not retried in a loop). A run whose log has no end and whose lock names a dead
  process was cut off. Only runs the schedule started (`trigger: schedule`) are finished this way; the owner's own runs
  stay the owner's (the Resume button is there).
- **Resume uses what was already paid for.** `--resume` reads the run's saved inputs and state, so finished calls are not
  made again (ADR 0034). At most `max_resumes` (3) tries per run; after that the check stops, writes a `gave-up` marker in
  the run folder and sends one macOS notification. A run cut off more than `resume_within_hours` (36) ago is dropped: its
  slots have passed, and it does not hold back a new plan (`min_days_between` ignores it).
- **A lock is only a lock if its process is alive.** The console and the check treat a lock naming a dead process as no run.
- **One clock across a resume.** A resumed run's log continues its millisecond clock where it stopped (the time the Mac
  was off is not counted); a line cut in half by a power loss is closed before the next event; calls left open when a run
  was cut off are drawn as cut off, not as still working.
- The check also reads the calendar a week at a time. The old single read asked for only the first 200 posts, which can
  stop short of the later days (150 posts are made per 10 days), so the end of the plan could look earlier than it is.

## Consequences
- Good: no day is lost to a closed Mac; a crash or power cut costs a resume, not a replan; a stuck "running" state cannot
  persist; a failure that cannot be fixed by retrying is told to the owner once, not looped.
- Cost / risk: 48 cheap checks a day while the Mac is on, each one calendar read and a log line in `data/studio/schedule.log`.
  launchd's interval timer does not fire while the Mac sleeps; the first check after it wakes catches up. A run that
  is cut off in the final handover can leave some drafts made and the rest not: the resume skips posts that exist (their keys
  are stable).
- Not done: waking the Mac from sleep or power-on at a set time (that needs `pmset` and an admin password: the owner's call).

## Alternatives considered
- Keep the calendar trigger and add `RunAtLoad`: it still misses a Mac that is on but asleep at 09:30, and it cannot finish
  a cut-off run without extra logic anyway.
- A resident process that waits for the Mac to come back: a new thing to keep alive, for what a 30-minute check does.
- Retrying a failed run automatically: a failed run (budget, a refusal, a broken input) would loop and spend tokens.
