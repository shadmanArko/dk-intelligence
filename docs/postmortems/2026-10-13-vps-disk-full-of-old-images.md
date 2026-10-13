# Postmortem: the VPS disk filled with old deploy images

- **Date / duration:** 2026-10-13, built up over about one day of many deploys; found when `HostDiskLow` fired (13.65% free).
- **Impact:** none yet (90% used, 9.8 GB free, nothing refused); the next large deploys or a backup could have failed.
- **Severity:** ticket (near miss)
- **Status:** final

## Summary
`deploy/deploy.sh` ended each good deploy with `docker image prune -f`, which removes only untagged layers. The images CI
builds are pulled by digest (intel-api) or by a sha tag (intel-mcp), so none of them was ever "dangling". Every deploy kept two
more images. About eight merges to `main` that day meant about eight deploys, and the VPS held 92 images (74 GB, 65 GB unused)
on a 96 GB disk shared with the ordering system.

## Timeline (UTC)
| Time | Event |
|---|---|
| 2026-10-13 morning to evening | many merges to `main`, each deploying through door 3 |
| 16:59 | Telegram: `HostDiskLow`, 13.65% free |
| 17:30 | the owner ran the read-only step of `docs/runbooks/disk.md`: `docker system df` showed Images 74 GB, 65 GB reclaimable |
| later | root cause found in `deploy.sh` |

## Root cause
The cleanup step did not match how the images are referenced. The test for the script checked the rollback and the smoke test but
never what is left on the disk after several deploys. The runbook for `HostDiskLow` named `docker image prune`, which has the
same blind spot.

## Detection
The alert worked (70% would have been better as the first warning: the existing alert starts at 15% free). We should also have
seen it in the deploy log: nothing recorded how many images exist after a deploy.

## What went well / what did not
- Well: the alert fired before anything failed; the owner's volumes (6.4 GB, the databases) were never at risk; the fix is small.
- Not well: a day of rapid merges multiplied the effect; I merged without looking at what each deploy leaves behind.

## Actions
| Action | Type | Status |
|---|---|---|
| `deploy.sh` removes our own old images after a good deploy; keeps what runs, `:current` and `:previous`; never fails a deploy | prevent | done (this change) |
| a test: a good deploy removes stale images and keeps current and previous; a failed deploy removes none | prevent | done |
| now: the owner runs `docker image prune -a -f && docker builder prune -af` once (runbook `disk.md`) | mitigate | owner |
| consider an earlier warning (`HostDiskLow` at 25% free) | detect | open |
