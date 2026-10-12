# 0033. Contracts between the projects: owner-defined, consumer-pinned, tested, checked at run time

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
- The Content Studio (Phase 5 plan) joins four repos: dk-intelligence, dhaka-kacchi-reels, dk-publishing and the
  ordering system. The owner wants to change one project and have the others follow, or fail loudly, never silently.
- Before this, nothing caught a mismatch until a live run. Two examples were found while building it:
  - the handover's test manifests lacked half the fields a real manifest has (version, sizes, checksums, template);
  - the test stand-in for dk-publishing's contract had no version field.

## Decision
- **The owner defines each contract.** reels writes `index/content-spec.schema.json` and
  `index/manifest.schema.json`. The manifest is validated before every write, and a reels test fails when either
  file is stale. dk-publishing serves `/openapi.json` and the versioned `/v1/spec`. dk_intel's tools have reviewed
  snapshots (ADR 0029).
- **Each consumer pins a reviewed copy** (`contracts/pinned/`) and lists every link in `contracts/registry.yaml`:
  the owner, source, version and consumers.
- **Consumer tests** (`tests/test_contracts.py`) check that:
  - every spec the lab writes, every batch the handover sends, and every manifest fixture matches its pinned copy;
  - the versions in the code, the registry and the pinned documents agree.
- **`uv run intel contracts sync --check`** reports drift from the owners' current files (exit 1). Without `--check`
  it refreshes the pins, to be reviewed in the diff together with the code change the drift needs.
- **Checked at run time:**
  - dk_reels `list_templates` reports `contracts {content_spec, manifest}`, and a mismatch stops the run before any
    AI;
  - the handover refuses a `/v1/spec` whose `spec` is not `publishing-spec/v1`, and holds a manifest of an unknown
    version.
- **Rule for owners: add fields; never rename or remove one without a new version.**

## Consequences
- A breaking change in reels or dk-publishing fails a test here, or stops a run with a clear reason, instead of
  failing quietly in production.
- CI can't see the sibling repos (reels is private and separate), so `sync --check` runs on the Mac. The pins and
  tests run everywhere.
- Not covered yet: the performance join with the ordering system's warehouse (milestone S4 adds it to the registry).
