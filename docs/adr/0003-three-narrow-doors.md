# 0003. Three narrow doors; no shell for the assistant

- **Status:** Accepted
- **Date:** 2026-10-03 (back-filled)

## Context
The engineering work is done with an AI coding assistant on the Mac. The VPS also runs the ordering system and holds
customer data. A compromised or mistaken assistant with a shell there could do real damage.

## Decision
The Mac reaches the VPS only through three single-purpose doors, enforced by SSH `authorized_keys` options:

| Door | User | Can do | Enforced by |
|---|---|---|---|
| 1. Read the warehouse | `intel` | forward to Postgres 5432 (and the collector DB 5442) only | `restrict,port-forwarding,permitopen=…,from="<Mac tailnet IP>"`, read-only DB role |
| 2. Deliver packages and reports | `intelinbox` | write files into one inbox; no read, no delete | forced command `rrsync -wo -no-del` |
| 3. Deploy | `deploy` (from GitHub Actions) | run one script: pull + deploy | forced command `run-intel-deploy.sh` |

The assistant never receives an interactive VPS login; the owner runs any one-off command there.

## Consequences
- Good: the blast radius of the Mac (and anything running on it) is "read some tables, drop a file in an inbox".
- Good: every change on the VPS goes through git and CI (door 3).
- Cost: setup steps are run by hand by the owner (to be codified in H8, pulled on the VPS, never pushed from the Mac).

## Alternatives considered
- One admin key on the Mac: simplest, and the largest blast radius.
- A VPN-wide allow rule: still gives a shell.
