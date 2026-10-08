# 0011. Sealed model code changes only with the model

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
Packages pin the hash of their feature code (ADR 0007). Any byte change to a sealed file, even a type annotation,
makes every package built before it `incompatible` on the VPS. During H0 the type clean-up reached 44 errors in sealed
files, including `champions/harness_v1/features.py`, which is vendored from the production harness and pinned by its
own hash test.

## Decision
- A sealed file changes **only as part of a real model change** (new version, retrained, judged, approved).
- `sealed-code.lock.json` records the hash of every sealed file per model. A test and a pre-commit hook fail on any
  difference, with a message that explains the consequence. A deliberate model change updates the lock
  (`uv run python -m intel.sealed --update`) in the same commit.
- Type errors in sealed files are listed as **typing debt** in `pyproject.toml` (mypy `ignore_errors` for exactly
  those modules) and are fixed together with each model's next real change.

## Consequences
- Good: no accidental "incompatible" packages; changing model code is always a visible, reviewed act.
- Cost: 44 known type errors remain, documented, in seven sealed modules.

## Alternatives considered
- Hash only behaviour (AST without annotations): clever, but hides real changes from reviewers and from the package
  audit record.
- Fix everything now: would orphan the packages built on the Mac before the next deploy.
