# 0022. Infrastructure as code, in pull mode

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
The VPS side was built by following guides step by step in chat: users with restricted keys (the three doors),
directories, a deploy script, cron lines, swap, `tailscale serve`. It worked, but the state existed only on the box;
rebuilding meant replaying a conversation, and a drifted line (a `permitopen`, a cron entry) would go unnoticed.

## Decision
1. **One Ansible playbook** (`infra/site.yml`, data in `infra/group_vars/all.yml`) converges exactly that state.
   Idempotent: a second run changes nothing (tested).
2. **Pull mode:** it runs on the VPS (`ansible_connection: local`) from the checkout the deploy keeps current. A push
   model would need an SSH login with a shell on the VPS from the Mac, where the AI assistant works: that would undo
   ADR 0003's three narrow doors.
3. **Boundaries:** secrets, private keys, git checkouts and Docker are *checked*, never written (a missing `.env`
   stops the run and is named); the harness stack and its crontab lines are not touched; app deploys stay with CI.
4. **Adoption, not duplication:** cron lines first made by hand are recognised and replaced by managed entries
   (Ansible's cron module otherwise adds a second copy: every backup would run twice).
5. **Check mode first** on the real VPS (`--check --diff`), then apply.
6. **Tested in CI** on a fresh Ubuntu 24.04 container set up like the VPS (a deploy user whose crontab holds a harness
   job and hand-made copies of ours): converges, then 0 changes; doors byte-exact; harness line kept.

## Consequences
- Good: the VPS can be rebuilt (and checked for drift) with one command; every change to it is a reviewed diff.
- Cost: Ansible on the VPS; a container can't test Tailscale, swap or Docker, so those tasks are verified by the
  first check-mode run on the real host.

## Alternatives considered
- Push from the Mac (the usual Ansible mode): needs a shell key on the VPS from the assistant's machine (ADR 0003).
- Terraform: manages cloud resources, not what is inside one existing VM; nothing here is provisioned by an API.
- A shell script: no check mode, no diff, idempotency by hand.
