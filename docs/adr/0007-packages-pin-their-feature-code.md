# 0007. Packages pin their feature code; the server refuses a mismatch

- **Status:** Accepted
- **Date:** 2026-10-05 (back-filled)

## Context
Training/serving skew (features computed differently at serving time than at training time) is the most common
silent failure in production ML. Here training and serving run on different machines from different checkouts.

## Decision
A model package (`.tar.gz` + outer SHA-256) contains the model, metadata, judge report, model card, **the source of
every file that computes its features**, and `SHA256SUMS`. Metadata records the hash of each such file, the git
commit, Python and scikit-learn versions. On intake the VPS compares those hashes with its own code and marks a
mismatch `incompatible`; such a package can never be approved. On load it re-checks the model file's hash.

## Consequences
- Good: skew between the trained and the served feature code is impossible by construction.
- Good: the package is an audit record: what code, data and judge verdict produced this model.
- Cost: feature code and server must be deployed together (ADR 0011 makes this deliberate).

## Alternatives considered
- A feature store serving one implementation to both sides: right at larger scale (ADR 0013).
