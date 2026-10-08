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

---

## H1 · Making production run exactly what was tested, and undo itself

**Situation.** Every push rebuilt the service image on the production VPS. Nothing was scanned. The first scan I ran
found 6 HIGH-severity vulnerabilities with fixes available in the image that was serving: one in our own dependency
(held back by an old version cap), two inside `pip`, which the service never even uses, plus a package download cache
left in the image (the image shrank by ~680 MB once caches and build tools were out). A release that started but misbehaved would have stayed live until someone noticed.

**Task.** Guarantee that production runs the bytes CI tested and scanned, recover from a bad release without a human,
and harden the supply chain, on a single small VPS and at no extra cost.

**Action.**
- Build once: a multi-stage image (no compiler, package manager or caches at runtime; base images pinned by digest),
  pushed to the registry, pulled back **by digest** and scanned. The deploy names that digest; the VPS never builds.
- The deploy request travels over a forced-command SSH key. The server accepts only `deploy <40-hex> sha256:<64-hex>`,
  skips stale commits, and refuses an image whose revision label doesn't match.
- Before switching it tags the running image `:previous` and records the active models. A smoke test inside the new
  container checks the version, that no approved model was lost, and a real prediction. On failure it rolls back and
  re-verifies the old version, and the pipeline goes red and alerts on Telegram.
- Made rollback schema-safe with an **expand/contract migration policy** enforced by a test.
- Supply chain: actions pinned to commit SHAs, tool images to digests, least-privilege tokens, pinned SSH host key,
  dependency audit against lock-file hashes, SBOM and provenance, Dependabot, shellcheck and actionlint.
- Tested the deploy script itself (real script, real git, a recording fake Docker) and **rehearsed** a broken
  release with real Docker before touching production.

**Result.** 6 → 0 fixable HIGH/CRITICAL; image 1.63 GB → 955 MB; rollback rehearsed at about 40 s end to end; every
deploy recorded; red CI in either repo now pages me on Telegram.

**Trade-off I would defend.** I did not do blue/green (two live containers behind a switch). On a 7.8 GB box shared
with the ordering system, a second API container costs real memory for a few seconds of saved downtime. Rollback to a
tagged previous image gives most of the safety at no standing cost. Same for distroless: it would cut the 43
unfixable base-image findings, but loses the shell I need for incident debugging today; it is noted as the next step.

**Likely follow-up questions.**
- *What if the new version migrates the database and then rolls back?* That is why migrations are additive only;
  destructive changes need a marked "contract" migration in a later release, and a test enforces it.
- *Why not let Trivy fail on every HIGH?* 43 have no fix upstream; failing on them would block every deploy without
  making anything safer. Fixable ones block; the rest are reported in every build.
- *How do you know the rollback works?* A unit test of the real script, and a rehearsal with a deliberately broken
  release in a throwaway environment.

---

## H3 · From "I'd notice eventually" to SLOs with burn-rate alerts

**Situation.** A production ML service on one VPS, with four background workers (inbox watcher, Telegram bot, hourly
measurement, weekly reports) and nightly jobs (backup, data collector). The only signals were Telegram run reports
and checking by hand. A stalled worker, a full disk, a failing backup or a slowly failing collector step would be
found by luck. The VPS has no swap, so running out of memory kills a process without warning.

**Task.** Make failures visible and actionable, define what "working" means in numbers, and alert on it without
waking anyone for noise, at €0 and inside the memory budget of a shared VPS.

**Action.**
- Instrumented the service: route-template latency and status (bounded label cardinality), predictions by model,
  worker heartbeats, and database-backed state read at scrape time. Added structured JSON logs with request ids.
- Measured from outside: a 30-second probe of a deep health endpoint (database + real prediction, no side effects).
  Chose probe-based SLIs because at a few requests per hour, request ratios swing on one bad call.
- Wrote SLOs (99.5% availability, 95% under 300 ms) with an error-budget policy, and multi-window, multi-burn-rate
  alerts (SRE Workbook): page at 14.4× over 1 h and 5 min, ticket at 6× over 6 h and 30 min. Inhibition means one
  incident sends one message.
- Treated monitoring as code: alert rules have **unit tests** (promtool), every dashboard query is parsed in CI,
  secrets are rendered at deploy time, and dashboards are read-only in the UI.
- Rehearsed with real containers, including an outage drill.

**Result.** Hard outage detected in 3.5 min and resolved 41 s after recovery, with exactly one alert; collector
failures now visible (they used to scroll past); a blind spot (missing disk series) is now itself alerted; 8 new
tests plus 9 alert-rule test cases.

**Trade-off I would defend.** Self-hosted rather than Grafana Cloud: metrics never leave the box and there's no
vendor limit, at ~300–500 MB of RAM we have. The known gap is that nothing on the VPS can report the VPS itself
dying. The fix is an external dead-man's switch, which sends data off the box, so I made it an explicit next decision
rather than slipping it in.

**Likely follow-up questions.**
- *Why does the 1 h window come second in the alert expression?* Because the alert's value is the left operand's.
  A unit test caught the page saying "last 5 min" while showing the 1-hour number.
- *How do you avoid alert fatigue?* Burn rates instead of thresholds on single failures (a single failed probe is
  tested not to page), page vs ticket severities with different repeat intervals, and inhibition.
- *What's your SLI if traffic grows?* Add request-based SLIs from the latency histogram alongside the probe; the
  route-template labels are already there.

---

## H2 · Data contracts: bad data stops at the door

**Situation.** Every Sunday the pipeline pulled 17 tables (5 from the business warehouse, 12 from a market-data
platform) and trained on whatever came back. The only data check was a row count in one model. A renamed column, a
metrics job that silently stopped, or a calendar that no longer reached into the future would only have shown up as a
quietly worse model weeks later.

**Task.** Stop bad data before any model sees it, with a reason a human can act on, and without blocking good data.

**Action.**
- Wrote **consumer-driven contracts**: only the tables and columns the models actually read. Structure is checked on
  the stable Arrow types, not pandas dtypes (which change when nulls appear). Values are checked with pandera,
  collecting every failure at once. On top: volume vs last week, freshness with warn and error tiers, calendar
  horizon, cross-table links, no future timestamps.
- Designed the gates around the business: the owner's own data blocks the run; market data falls back to the newest
  snapshot that passes, because last week's market is fine but wrong data is not.
- **Calibrated on real data before enforcing.** A gate that blocks good data every week gets switched off.
- Every run leaves a **data card** (rows, change, freshness, per-column nulls and ranges) for the audit trail.

**Result.** 0 false errors on real data. One true finding on the first run: a statistics table that mixes countries
with subtotals, so summing it inflates the audience 3.6×. A near-miss deployment bug (the server importing a
training-only library) is now caught by a dedicated test. 14 new tests, one per failure class.

**Trade-off I would defend.** I made the citizenship rule *precise* (country names unique, subtotals recognised)
instead of "every row unique" or switching the check off. The contract should encode what the consumer relies on.
The real fix (flag subtotals upstream) went to the team that owns that data, as a separate change.

**Likely follow-up questions.**
- *Why not just rely on the upstream dbt tests?* They protect the producer's tables. A consumer has its own
  assumptions (which columns, how fresh, how far ahead), and only the consumer can state them.
- *Why warnings at all?* Some signals are worth knowing but not worth stopping for, like a posting pause or a few
  reviews pointing at a delisted restaurant. They travel in the run report and on Telegram.
- *How is this different from drift monitoring?* Contracts catch broken data (wrong, missing, stale). Drift (H4)
  catches valid data whose distribution moved enough to hurt the model.
