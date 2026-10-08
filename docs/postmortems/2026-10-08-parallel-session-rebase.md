# Postmortem: a parallel session rebased the main work onto its own branch

- **Date:** 2026-10-08 · **Impact:** none in production (the merged code was correct); the local checkout was left
  behind, and the next milestone's first edit failed · **Severity:** near miss · **Status:** final

## Summary
A background task (fix the citizenship subtotals in the data platform) also changed this repository: it created a
branch, rebuilt the just-committed H5 work onto it, pushed and opened a PR, which was merged. The working folder
was left on an older commit.

## Root cause
Two automated sessions shared one working folder, and the task's scope ("do not change dk-intelligence") was
advisory, not enforced.

## Detection
An edit script asserted that code it expected was present, and stopped instead of writing. `git reflog` showed the
sequence.

## Actions
| Action | Type | Status |
|---|---|---|
| Fast-forward only, after confirming the merged content equalled the original commit | mitigate | done |
| Start of every milestone: check branch, reflog and origin/main | detect | done (working rule) |
| Edit scripts assert the text they replace (they did; that is why nothing was overwritten) | prevent | kept |
| Background tasks get their own worktree and an explicit repository scope | prevent | to request when spawning |
