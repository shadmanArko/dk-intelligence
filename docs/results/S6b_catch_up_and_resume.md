# S6b results: a Mac that is off, and a run that is cut off (measured 2026-10-13, real model, real renderer)

What was built: [ADR 0039](../adr/0039-catch-up-and-finish-cut-off-runs.md) (and, earlier the same day,
[ADR 0038](../adr/0038-mission-control-and-planning-from-what-exists.md)). How to use it: [STUDIO.md](../STUDIO.md),
"Running without you".

## The proof: cut the power in the middle of a real run
`render` mode, so nothing was sent anywhere. One day, three ideas, started the way the schedule starts it
(`--trigger schedule`), real agents, real renders. The whole process group was killed with SIGKILL while a render was in
flight (what a power cut or a crash does: no cleanup, the lock file stays).

| | |
|---|---|
| Time of the cut | 95 s into the run |
| Finished by then | 10 agent calls, **71,127 tokens paid** (strategist, director, 3 copywriters, 4 adapters, 1 designer) |
| In flight at the cut | 2 designer calls and the first render |
| What the next check said | `WOULD RESUME: run 20261013T142638Z was cut off: finishing it (try 1 of 3)` |
| The resume (the real scheduled check) | **35 s**, 22,708 tokens, 2 agent calls (the two designers) + 3 renders |
| Result | 3 of 3 ideas, **12 of 12 posts**, status `done`, lock released, summary written |
| Finished calls made again | **0** of the 10. Only the 2 designer calls and the render that were in flight were redone |

Honest accounting: the cost of the cut is what was in flight. The two designer calls that were running had not reported
their tokens when the process died, so the 71,127 + 22,708 = 93,835 recorded tokens understate the true spend by those two
unfinished calls. A cut near the start costs almost nothing; a cut during the designers (the largest step) costs most.

Mission control drew the cut correctly: the 2 designer calls and the render show as failed bars, not as still working, and the
run's clock continued instead of restarting (the log's `ms` is one timeline across both processes).

## What the decision logic does (all covered by tests; the proof above is the end-to-end case)
- Mac off at 09:30, on at 17:10: the first check after that starts the day's run. Mac off for good: nothing happens.
- Before 09:30 a new run never starts; a cut-off run is finished at once.
- A lock whose process is dead is not a run in progress (the console had shown "running" forever; fixed). The console
  process I had running during the proof still showed it, because it was started before the fix: a console started after
  the merge reads `{"running": null}` for the same lock.
- 3 tries, then one notification and a `gave-up` marker, no loop. A run cut off more than 36 h ago is dropped.
- A failed run (it wrote its own end) is final: never retried.

## Bugs the tests found on the way (all fixed in the same change)
- A line cut in half by the power loss swallowed the next event (the resume's own start). The log now closes the line first.
- A resumed run's millisecond clock restarted at 0, so the timeline overlapped itself. It now continues.
- Calls left open in a cut-off run were drawn as working. They are now cut off.
- `schedule check` and the console used a single read of 200 posts and could stop short of later days; now a week at a time.

## What I could not test
launchd firing itself (the schedule was installed by the owner at 14:22 UTC and logged its first SKIP at once, as designed:
the calendar was planned 10.2 days ahead). Waking a powered-off Mac at a fixed time needs `pmset` and an admin password:
not done.

## Side effect to know about
The proof run is a `schedule` run that finished on 2026-10-13, so `min_days_between: 6` holds the next scheduled run back
until 2026-10-19. The next plan is due about 2026-10-20, so this changes nothing in practice. Its 12 rendered posts (for
24 Oct) are on this Mac only; nothing was handed over.
