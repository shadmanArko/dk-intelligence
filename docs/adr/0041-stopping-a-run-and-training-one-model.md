# 0041. Stopping a run cleanly, and training one model on its own

- **Status:** Accepted
- **Date:** 2026-10-13
- Builds on [0040](0040-operations-center-the-whole-machine-in-one-place.md) (the Operations pages) and
  [0039](0039-catch-up-and-finish-cut-off-runs.md) (a cut-off run is finished).

## Context
- The owner asked for a Stop button and for running one model alone. Both reach inside the programs, not only the page.
- A run that is killed leaves no ending: the studio run's log has no end and the ML run's lock and progress say "running". The
  schedule treats that as a crash and starts it again (ADR 0039; the ML run's hourly catch-up). A Stop that is undone by the
  scheduler within the hour is not a Stop.
- The ML run trains every model in one go; there was no way to train one.

## Decision
- **Stop is a clean ending, not a kill.** `stopping.py`: while a run is working, SIGTERM raises `Stopped` in its main thread
  (`sigterm_stops_cleanly`), the run writes its own ending and exits with 130, and `STOP` makes worker threads stop recording
  failures and stop starting new agents, retries or renders. The handler also ends the run's own child processes (agents,
  renderers, tunnels) at once, so the run does not wait for them. A stopped studio run is **final** (it has an end, status
  `stopped`): the schedule never restarts it; Resume on the Runs page continues it and does not redo finished calls. A stopped
  ML run is recorded `stopped` at the step it was in, skips the knowledge pack and the report, and counts as an attempt, not as
  "done this week": the catch-up retries it after the normal 6 hours, not at once.
- **Stop from the page** (`POST /api/ops/stop`, token required): asks the studio run and the ML run named by this program's own
  lock files to stop (SIGTERM), waits up to 25 s, then ends the operation's driver and whatever is left in the process groups of
  those runs (never this console's own), SIGKILL only for what ignored the request. A group the system refuses to signal is
  skipped, not an error. Buttons on Operations, Runs and Mission control.
- **One model alone:** `intel weekly --only <model or step> [--no-upload]` runs the same data steps and the judge for just
  those plug-ins (the two scorers are one plug-in and train together). The page offers it as a fixed action per plug-in with
  `--no-upload`: a candidate, nothing uploaded or deployed. It is recorded with `only` in its report and progress, never counts
  as this week's training (`is_due` ignores it), skips the knowledge pack, and the ML page shows the other models' steps as "not
  part of this single-model run". Deploying stays the full run's job (gates, the owner's approval for scorers).

## Consequences
- Good: Stop takes seconds, keeps the work, and cannot be undone by the schedule; a model can be retrained in about a minute
  without touching what is deployed; the history says which runs were one-model.
- Cost / risk: a stop kills in-flight agent calls, which were already paid for and are redone on Resume (the calls that had
  finished are not). A stop during the pull or market step leaves that snapshot unfinished. A stopped ML run that the owner never
  restarts is retried by the catch-up after 6 hours; to prevent that, run the full ML run from the page.
- Not done: stopping a run that was started on another machine; pausing instead of stopping.

## Alternatives considered
- SIGKILL on the process group: no ending is written, so the schedule restarts it, and held ideas could be recorded for agents
  that were only killed.
- A stop flag file the run polls: needs a check in every loop and a delay; a signal needs none.
- Making a stopped ML run count as done for the week: the week's training would silently not happen.
