# 0044. The homelab server runs the Sunday training, through the same doors with its own keys

- **Status:** Accepted
- **Date:** 2026-11-23

## Context
The Sunday training ran on the Mac (launchd, `scripts/weekly.sh`): it needed the Mac awake, on AC power, on Tailscale
and holding two ssh tunnels to the homelab server's databases. The data platform's weekly refresh, which the training
reads, moves to the homelab server (data-platform repo `deploy/weekly/`). The training's wait for it was
`pgrep weekly_refresh.sh` on the Mac: on the server that finds nothing and the models would train on a half-rebuilt
market. The owner wants no scheduled job on the Mac (plan: `docs/SERVER_MOVE_PLAN.md`, approved 2026-11-23).

Doors 1 and 2 (ADR 0003) accept only the Mac's Tailscale address, and ADR 0003 says the doors are never widened.

## Decision
- The Sunday training runs on the homelab server as a batch container (`deploy/server/`): systemd timer hourly plus
  Sunday 12:00 Europe/Berlin, `Persistent=true`; the container checks "is it due?" first (same rules as the Mac,
  `is_due`), then `intel server-preflight`, then `intel weekly --if-due`.
- With `MIP_STATUS_FILE` set, the wait step waits until **this week's** data platform refresh has **succeeded**
  (`weekly_last_status`: `result=success`, `started_at` after Sunday 00:00 Berlin). A failed or unfinished week past
  the wait limit fails the step; the run is retried by the next due check. Without the setting (the Mac), `pgrep` as
  before.
- The databases are reached directly on their Docker networks (read-only `mip_reader`; `rag_builder` for the
  knowledge pack), not through tunnels.
- **Doors 1 and 2 are not widened; the server gets its own keys.** Two new keys generated on the server, each
  authorised on the VPS with exactly the restrictions of the Mac's key for that door (`intel`: `restrict,
  port-forwarding, permitopen` to the warehouse only; `intelinbox`: forced `rrsync -wo -no-del`), each with
  `from="<server tailnet IP>"`. What a key can do is unchanged; the set of machines holding one grows by one. The
  Mac's keys are removed once the Mac no longer runs the training (owner's step).
- The container is hardened like every server service: non-root uid 10004, read-only root file system, `cap_drop:
  ALL`, `no-new-privileges`, 4 CPU / 6 GiB, secrets as files, no ports, log rotation.
- The model code is unchanged (sealed, ADR 0011). The package's commit comes from the image build (`INTEL_GIT_SHA`)
  when there is no git. The MLflow database is moved with `deploy/server/rebase_mlflow.py` (its artifact paths are
  absolute).

## Consequences
- Good: the Sunday training no longer depends on the Mac; it starts only after a successful market refresh.
- Good: the blast radius of each door is unchanged; a compromised server can do what a compromised Mac could.
- Cost / risk: one more machine holds door keys (mitigated: per-machine keys, `from=` pinned, revocable alone).
- Cost: the Control Room's Run button and the Review check still live on the Mac (they only need the data folder);
  moving them is phase IC of the plan.

## Alternatives considered
- Add the server's address to the Mac's existing keys: one key on two machines cannot be revoked per machine.
- Tunnel from the server through the Mac: keeps the Mac in the path (the thing we remove).
- Keep training on the Mac and only fix the wait: the owner wants the Mac free of scheduled jobs.
