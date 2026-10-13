# S8 results: Stop, and one model on its own (measured 2026-10-13, real runs)

What was built: [ADR 0041](../adr/0041-stopping-a-run-and-training-one-model.md). Buttons: Operations (Stop; "One model at a
time"), Runs and Mission control (Stop), the model view on the ML page (Retrain).

## One model on its own (real run, from the page's own API)
`ml.only.funnel` (`intel weekly --only funnel --no-upload`): **33 seconds**, steps wait, pull, market, ledger, funnel, judge and
package done; the 4 other models' steps, upload and the knowledge pack shown as "not part of this single-model run". The weekly
rule afterwards still said "done this week (Sun 11 Oct)": the one-model run did not count as the week's training.

## Stop, on a real ML run
Started the one-model run, stopped it 14 s in (during the pull). **1.5 s**; the run recorded itself `stopped` at the pull step
("stopped by the owner"), skipped the knowledge pack and the report, removed its lock, and left no processes; the operation shows
`stopped`; a second Stop answered "nothing is running".

## Stop, on a real studio run (1 day, 3 ideas, render mode)
Stopped with 4 agent calls finished and 3 in flight (**28,382 tokens paid**):

| | first attempt | after the fix |
|---|---|---|
| Time from Stop to the run ended | 26 s, no ending written | **2.5 s**, ending `stopped` written |
| Ideas recorded as failed | 0 | 0 |
| New agent processes started after the Stop | yes (`adapter-retry` ran to its end 18 s later) | none |
| Leftover processes | none | none |

The first attempt found two real faults, both fixed: the run waited for its in-flight agents before it could write its ending, and
the retry logic then started new agents after the Stop. The run now ends its own children at once, and no agent, retry or render
starts once a stop is requested.

**Resume of that stopped run, for real:** finished in one resume, **3 of 3 ideas, 12 of 12 posts**, status `done`. Before the Stop
7 calls / 28,382 tokens; the resume made 8 calls / 62,592 tokens; **0 finished calls were made again**. Only what was in flight
was redone.

## Also found on the way
- `os.killpg` on the operation's process group returned a permission error on macOS even though the run had stopped cleanly: the
  stop now signals the runs by their lock files first and treats a refused group signal as skipped.
- A stopped ML run exited with code 0, so the operation read `done`: it now exits 130 and reads `stopped`.

## Tests
`tests/test_operations.py` (16 tests): the fixed action list incl. the 6 single-model actions; the `--only` selection and the
`is_due` rules (a one-model run is ignored, a stopped run is an attempt); the Sunday run stopped at its first step; a studio run
that gets a real SIGTERM mid-run (ends `stopped`, no holds, handler restored, schedule does not restart it, resume pays nothing
twice); the stop procedure (runs first, then the driver, then the groups; never its own group); the endpoint and its token.

## Not there
Stopping a run started on another machine; pausing; the market-data refresh button (the owner chose to skip it).
