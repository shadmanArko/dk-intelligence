# 0001. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-08 (decisions 0002–0008 back-filled from Phases 1–2)

## Context
The system grew over two phases across three repositories and two machines. The reasons behind its shape (why the
Mac trains, why the VPS refuses some packages, why some models never auto-approve) lived in commit messages and chat.
A new engineer, an auditor or an interviewer cannot recover them from the code.

## Decision
Every decision that constrains future work gets a short record in `docs/adr/` (Context → Decision → Consequences →
Alternatives). Records are immutable once accepted; changes supersede them.

## Consequences
- Good: the "why" is reviewable next to the code; reversing a decision forces a look at its original trade-offs.
- Cost: a few minutes per decision.

## Alternatives considered
- Wiki pages: drift away from the code and are not reviewed in pull requests.
