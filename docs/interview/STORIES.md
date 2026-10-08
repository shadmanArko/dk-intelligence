# Interview stories

One story per milestone, in STAR form (Situation, Task, Action, Result), plus the trade-off, because that is where
senior judgement shows. Numbers are from the repo and the decision records.

---

## H0 · Making the platform extensible without breaking production

**Situation.** After two phases the platform had eight models in production (two scorers, six analytical models) and
a weekly train → judge → approve → serve loop. Each model was wired in by hand in three places. Type checking reported
200 errors and was not enforced; coverage was not measured in CI.

**Task.** Make adding a model a one-folder change, and put quality gates in place without disturbing models already
serving on the VPS.

**Action.**
- Designed a `ModelPlugin` contract (an abstract base class): declared inputs, diagram position, the sealed source
  files per model, `run()`, and optional scoring and approval text. Discovery validates everything at start-up:
  duplicates, unknown inputs, missing files, model folders without a plug-in.
- Derived the Sunday run, the packaging, the pipeline diagram and the Telegram texts from the plug-ins, and proved the
  open/closed property with a test: a model that exists only in the test runs end to end.
- Introduced typing as a **ratchet**: the whole package clean under mypy's default checks, 14 core modules strict, a
  list that only grows. Fixed root causes rather than symptoms: one wrong return type on the DB helper caused 57 of
  the 200 errors.
- Found that type fixes in model code would change the hashes the server uses to reject training/serving skew, so
  every package built before the deploy would be refused. Wrote this down as a rule (sealed model code changes only
  with the model) and enforced it with a lock file, a test and a commit hook. The remaining 44 type errors in those
  files are documented debt with an exit plan.

**Result.** 200 → 0 type errors outside documented debt; 117 → 135 tests; the Sunday run's coverage from 50% to 76% with an
end-to-end test; branch coverage ≈ 76% in CI with an exact floor; secrets scanned across the full history of both repos (clean);
13 architecture decision records. No production package was invalidated.

**Trade-off I would defend.** I chose *not* to make everything strict at once. The sealed-code finding shows why:
"improving" code that is pinned by hash would have broken the safety mechanism against training/serving skew. Quality
work has to respect the system's invariants, not just the linter.

**Likely follow-up questions.**
- *Why an abstract base class and not a Protocol?* Shared defaults (`scores`, `approval_text`) and an explicit
  registry check (`isinstance`) at discovery; a Protocol would only type-check.
- *How do you know discovery can't silently miss a model?* It did once, during this change (a missing
  `__init__.py`); now any folder with a `spec.yaml` that no plug-in produces fails start-up.
- *Did any gate misbehave?* Yes: the first CI run printed "FAIL 75.99% < 76%" and stayed green, because coverage
  rounds before comparing. I made the comparison exact and raised coverage with tests rather than lowering the floor.
- *Why is coverage 76% and not 90%?* It is a floor that only rises. The uncovered parts are mostly HTML rendering and
  the Mac Control Room; the decision logic is high: judge 98%, ledger 98%, plug-ins 92%, registry 89%.
