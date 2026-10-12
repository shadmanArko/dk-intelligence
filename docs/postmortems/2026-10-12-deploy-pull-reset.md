# Postmortem: a deploy failed because the image pull was reset mid-download (the second time)

- **Date:** 2026-10-12, ~20 min · **Impact:** none for users: the previous version kept serving; the release
  (P4.7) went live 20 minutes later than it could have · **Severity:** ticket · **Status:** final

## Summary
CI deployed commit `bd063ed`. On the VPS, `docker pull` of the new intel-api image failed with "connection reset by
peer" from the GitHub registry. `deploy.sh` stopped before switching anything (`set -e` at the pull) and CI sent the
failure alert. Rerunning the deploy job pulled the same digest and deployed it.

## Root cause
A transient network failure between the VPS and ghcr.io, which had already happened once (during P4.1–P4.2), plus a
deploy script that treated one failed pull as final. The safety design worked: no partial switch, no rollback
needed. The gap was resilience: a known-flaky link with no retry.

## Detection
CI's deploy job failed and the Telegram alert fired. The deploy log named the cause in one line.

## Actions
| Action | Type | Status |
|---|---|---|
| `deploy.sh` retries each image pull up to 3 times (15 s apart); after 3 failures it records `failed` and switches nothing | prevent | done (tested: two failures then success; three failures switch nothing) |
| The same retry covers the intel-mcp image pull | prevent | done |
| If it happens again with retries in place, look at the VPS's IPv6 route to ghcr.io (both resets were over IPv6) | investigate | open, only if it recurs |
