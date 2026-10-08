# 0009. One plug-in interface for every model

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
After Phase 2 the system had eight models, and each one was wired in by hand in three places: the Sunday run
(`weekly.py`, ~250 lines of near-identical step blocks), the packaging source list (`package.py`) and the pipeline
diagram (`pipeline.py`). Adding a model meant editing all three; forgetting one failed late (on a Sunday) or silently
(a diagram arrow that did not match what the code read: two such arrows were found while making this change).

## Decision
Every model folder has a `plugin.py` with `PLUGIN = <ModelPlugin subclass>()`. The abstract base class declares:
`step`, `order`, `label`, `desc`, `inputs` (which snapshots it reads), `box` (diagram position), `kind`, `models`
(model name → sealed source files), `run(ctx) -> StepOutcome`, and optional `scores()` and `approval_text()`.
`intel.plugins.discover()` finds and validates all plug-ins at start-up: unique steps, orders and model names,
known inputs, existing source files, and every model folder with a `spec.yaml` covered by a plug-in.

The Sunday run, packaging, the diagram (steps, boxes, arrows) and Telegram texts are all derived from the plug-ins.

## Consequences
- Good (open/closed): a ninth model is one new folder; no other file changes. A test proves this with a model that
  exists only in the test.
- Good: the diagram's arrows are derived from declared inputs, so they cannot drift from the code.
- Good: the Sunday run's happy path is now testable end to end with fake models (it was 50% covered before).
- Cost: the serving pages (Insights) still render each insight explicitly; moving rendering into plug-ins is
  deferred until a second consumer needs it.

## Alternatives considered
- Entry points (`importlib.metadata`): for third-party plug-ins; ours live in one package.
- A registry decorator: needs an import side effect somewhere central; folder discovery does not.
