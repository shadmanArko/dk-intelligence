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

---

## H4 · Model monitoring that doesn't cry wolf

**Situation.** Approved models served 24/7, but nothing watched them between weekly trainings. Did today's posts
still look like the training data? Were the scores shifting? Was the model still right? Retraining followed the
calendar, not evidence. The features a model actually saw in production weren't even recorded.

**Task.** Close the loop from serving back to training, with signals the owner can trust at a small business's data
volume (a few posts a week).

**Action.**
- **Logged the served features** with every prediction, computed by the same sealed feature code and frozen
  context the model uses. That is the live side of both drift and skew detection.
- **A reference profile per model version**, rebuilt from the saved training run by a single code path, so new and
  already-active models are treated identically. It's delivered through the existing upload-only inbox.
- **Hourly monitor:** prediction and feature drift (PSI), live accuracy vs the test result and by platform,
  calibration. Shown on a dashboard and alerted on, with rule tests.
- **Statistics before alarms.** While testing I found that PSI on 40 posts over 11 bins reaches the "significant"
  0.25 by chance alone. A naive monitor would alarm every week. Drift now has to be large **and** statistically
  real (chi-square p < 0.01 at the actual sample sizes), and nothing is judged under 30 samples.
- **Evidence-triggered retraining with a cool-down,** and a training/serving **skew check** that rebuilds features
  for already-scored posts and diffs them against what was served.

**Result.** A closed loop: serve → log → compare → alert → retrain → judge → approve, automated except the human
approval. A real shift is detected at p < 1e-6, while same-distribution noise stays quiet. Testing also caught two
alert-text bugs that would have reached the phone ("500m" accuracy) and a delivery gap for the active model.

**Trade-off I would defend.** Monitoring can *trigger* retraining but never *promote* a model. A drift signal says
"the world changed"; only the judge on fresh held-out data plus a human can say "this new model is better". The
cool-down stops a persistent shift from retraining hourly.

**Likely follow-up questions.**
- *Why PSI and not KS?* Most inputs are categorical or binned. One method for all, with a matching significance
  test, keeps it consistent and explainable.
- *How do you know drift hurts?* You often don't, which is why performance and calibration are monitored
  separately. Drift alone needs ≥ 2 inputs or the score distribution to move before it asks for retraining.
- *What about label delay?* Outcomes mature after 7 days, so input and score drift are the early warning, and
  live accuracy is the confirmation.

---

## H5 · Governance you can trust, and proof the model can be rebuilt

**Situation.** Model approvals ran through Telegram and a web page, but the rules lived only in application code,
and the history was an ordinary table: one bug or one manual query away from "approved" meaning nothing. Model
cards were short. "Reproducible" was a claim no one had tested.

**Task.** Make the approval record trustworthy, make illegal model states impossible, document each model honestly,
and prove a production model can be rebuilt from its lineage.

**Action.**
- **Moved the lifecycle into the database:** a trigger allows only candidate → approved / rejected / expired.
  Decisions are final and activation history is append-only. A test tries all 42 status pairs and requires the
  database and the Python table to agree.
- **Built a tamper-evident audit trail.** Each governance action is written in the same transaction as the change
  (a failed action leaves nothing). The log is append-only and SHA-256 hash-chained, so editing or deleting any
  entry is detected at that exact entry, even by a superuser who disables the triggers. I backfilled the existing
  history and added an Audit page and a paging alert.
- **Model cards v2:** intended use and out-of-scope uses as code on each model's plug-in, plus lineage, data checks,
  training coverage, a confidence interval, confusion matrix, per-platform results and a calibration table.
- **Reproducibility as a test:** `intel reproduce` retrains from the recorded snapshots and diffs everything. CI
  trains twice and requires identical results.

**Result.** The production model reproduces **bit-exactly** (75/75 test predictions, same leaderboard and
metrics). The audit chain verifies, and tampering is caught. Coverage rose from 77% to 82.6%, because real training
now runs in CI. The new card surfaced something the old one hid: the model leans optimistic (20 of 39 below-usual
posts flagged as above).

**Trade-off I would defend.** A hash chain doesn't *prevent* a superuser from editing history; it guarantees the
edit is *detected* and alerted. Prevention would need write access controlled outside this database. For one
operator, evidence plus an alert is the right weight; anchoring the latest hash off-box is the noted next step.

**Likely follow-up questions.**
- *Why both Python and SQL rules?* Python gives readable errors and drives the UI; SQL makes bypass impossible. The
  equality test stops them drifting apart.
- *What makes training deterministic?* Seeded models and bootstrap, time-ordered CV (no shuffling), and immutable
  snapshots.
- *What breaks reproducibility in practice?* Pruned snapshots (refused, with a clear message) and changed sealed
  code (reported next to the result).

---

## H6 · A guardrail that can withdraw a model, but never promote one

**Situation.** Monitoring could see a production model getting worse and ask for retraining. But retraining takes
days (a Mac, a judge, an approval), and meanwhile the bad model kept answering every draft. Automation that acts on
production can also be wrong.

**Task.** Withdraw a degraded model automatically, on evidence strong enough to trust, without flapping, and keep
the owner in control.

**Action.**
- **Paired evidence instead of thresholds.** Every post is scored by both the production model and the previous
  system (shadow). McNemar's exact test on the posts where exactly one of them was right says whether the model is
  significantly worse *on the same posts*. That's fair at small samples and immune to "this month was just harder".
  Collapse to chance and severe miscalibration are secondary triggers. Nothing is judged under 40 matured outcomes.
- **Safety rails in a fixed order:** off / dry run / on; an owner pin (`/keep`, audited); one automatic rollback per
  model per week; only back to a version a human approved.
- **One path for every change:** the rollback goes through the registry, so the database lifecycle rules and the
  hash-chained audit trail apply. The reason is recorded, Telegram explains it, and undo is one tap.

**Result.** A measurably worse model is withdrawn within the hour of the evidence existing, explained and audited,
and never ping-pongs (all tested end to end on a real database). The system now has a complete autonomy boundary:
monitoring can *trigger training*, the guardrail can *restore an approved version*, and only the judge plus a human
can *promote*.

**Trade-off I would defend.** I shipped it **on** rather than in dry-run. At a few posts a week, a dry-run period
would delay protection by months, and the action only ever returns to a version the owner already approved. The
switch to dry-run is one line, and every decision is visible.

**Likely follow-up questions.**
- *Why McNemar and not comparing two accuracies?* The two accuracies come from the same posts, so they're paired.
  McNemar uses only the disagreements, which is exactly the information that distinguishes the models.
- *What if the baseline is down?* No paired posts, no paired evidence. The other triggers still work, and the
  baseline back-fills when it returns.
- *Isn't 40 outcomes slow?* Yes, deliberately. Fast symptoms (errors, latency, outages) are caught by the deploy
  smoke test and the SLO alerts. The guardrail is the slow, sure net for "it works but it's wrong".

---

## H7 · From "we have backups" to "the backup restores, every week"

**Situation.** Backups ran nightly and synced off-box, but none had ever been restored. All alerting ran on the
VPS it watched, so its own death would be silent. The box had no swap. Incidents were fixed and forgotten.

**Task.** Prove recoverability continuously, cover the monitoring's blind spot, and make every alert actionable.

**Action.**
- **A weekly restore drill:** the newest dump goes into a throwaway container, the tables, registry and **the
  audit hash chain of the restored copy** are verified, and RPO and restore time are published as metrics with
  alerts. It's tested with real Docker in CI, including a tampered backup that must fail.
- **A dead-man's switch:** an always-firing Watchdog alert feeds an external heartbeat service, which alerts when
  the heartbeat stops.
- **A runbook section for every alert,** linked from the Telegram message, with a test that fails if an alert has
  no runbook (or a runbook has no alert).
- **Blameless postmortems** for the four real incidents (a leaked token in logs, 4 days of unnoticed red CI, empty
  dashboards, a parallel-session rebase), each with prevent / detect / mitigate actions.
- **Recovery targets** written down (RPO 24 h, RTO 30 min), and swap.

**Result.** Recoverability is measured weekly instead of assumed, and the monitoring's blind spot is covered. The
drill's own first version had a real race (it waited for a temporary init-time server), caught by testing before it
could cry wolf on the server.

**Trade-off I would defend.** The drill restores the local copy, not the off-site B2 copy. Restoring from B2 would
need storage credentials on this box, widening its blast radius (ADR 0003). The B2 sync is the harness's
responsibility and is monitored there.

**Likely follow-up questions.**
- *Why verify the audit chain in the restore?* A backup can be technically valid and still carry a tampered history.
  The drill proves both "it restores" and "it's the record we trust".
- *What's your RTO really?* The drill measures the restore itself. The rest (rollback to `:previous`, redeploy) is
  automated and rehearsed (H1), so 30 minutes has margin.

---

## H8 · Infrastructure as code without breaking the trust model

**Situation.** The VPS side had been built by following guides: restricted SSH keys for three narrow "doors",
directories, a deploy script, cron lines, swap, tailnet-only publishing. The real state lived only on the box.
Rebuilding meant replaying a conversation, and drift would go unnoticed.

**Task.** Make the host reproducible and reviewable, without giving any machine (including the one where an AI
assistant works) a shell login to production.

**Action.**
- **One Ansible playbook in pull mode:** it runs *on* the VPS from the checkout the deploy already updates. Push
  mode, the usual Ansible setup, would have needed exactly the shell access the architecture forbids.
- **Clear boundaries:** secrets, private keys, checkouts and Docker are checked, never written; the shared
  harness's cron lines are left alone; app deploys stay with CI.
- **Adoption instead of duplication:** Ansible's cron module would add a second copy of every hand-made job (two
  backups a night). The playbook recognises identical existing lines and replaces them with managed ones.
- **Tested in CI on a fresh Ubuntu container set up like the VPS:** converges in one run, 0 changes on the second,
  doors byte-exact, harness job untouched. On the real host: preview with `--check --diff`, then apply.

**Result.** The VPS can be rebuilt or checked for drift with one command, and every host change is now a reviewed
diff in git.

**Trade-off I would defend.** Pull mode means the playbook can't be pushed to the VPS from CI. It runs when the
owner runs it. That's the right price for keeping production reachable only through three single-purpose doors.

---

## H9 · Measured capacity and a scaling plan with triggers

**Situation.** "How would this scale?" had no measured answer: no load test, no idea where a prediction's time went,
and the first request after a model load paid the cold start.

**Task.** Know the capacity, know what breaks first, and write a scaling plan where every step has a trigger metric.

**Action.**
- **Instrumented the request path** by stage (prepare / predict / ledger write), and measured features vs model
  with a micro-benchmark, because that code is sealed.
- **Built a load-test tool** that's safe against production by default. The endpoint that writes to the ledger is
  refused unless the target is local, because load-testing with it would poison the training data.
- **Measured the production model** against a real server and database. Added warm-up at load time and a batch
  endpoint.

**Result.** About 70 req/s per process. p95 is 21 ms with 1 client, about 230 ms with 10 (inside the 300 ms SLO)
and about 2 s with 50. Features take 73% of the compute, and batching makes a draft 23–89× cheaper. Today's
traffic is about 50,000× below capacity.

**Trade-off I would defend.** The tempting fix, "add more uvicorn workers", is wrong here. Every process also runs
the background loops, and two Telegram pollers or two measurers would conflict and double-count. Step one of the
scaling plan is splitting the stateless API from a singleton worker, triggered by the SLO burn alert, not by a guess.

---

## P4.0 · Turning library unknowns into tests before writing the server

**Situation.** Phase 4 puts an MCP layer over the models, in a fast-moving ecosystem: the MCP spec went stateless
in July, FastMCP 4 rebuilt on a breaking SDK v2 in September, and a one-day-old minor release was on PyPI. The
design rested on six library behaviours nobody had checked.

**Task.** Pin the libraries, and prove or disprove every assumption before any production code depended on it.

**Action.**
- **Spiked each assumption** in a throwaway project, then **kept them as tests** (`test_fastmcp_assumptions.py`)
  that CI runs from day one.
- **Picked the pin on evidence, not on habit.** The 7-day age rule would have chosen 4.0.10, but 4.0.11 fixes how
  visibility and auth apply to hashed tool lookups: the exact mechanism our role gate uses. The exception is
  written into the ADR.
- **Checked the lock is purely additive** (43 new packages, none moved) and ran pip-audit over every group.

**Result.** Five assumptions held. One didn't: tools with the same name on two mounted servers **silently collapse
into one**. That would have hidden a tool, or swapped which implementation answers, with no error. It is now a
mandatory test. The Claude Code config question was resolved from the docs (`CLAUDE_PROJECT_DIR`) instead of
guessed.

**Trade-off I would defend.** A spike day delays the first feature by a morning. It buys a design where the risky
parts are facts, and a regression suite that catches a library upgrade changing meaning, which is the usual way
MCP servers break.

---

## P4.1 · MCP as a thin edge: production writes unreachable by construction

**Situation.** AI clients (Claude Code now, agents later) should use the production models. intel-api also has
routes that write the ledger (`/v1/predict`), approve or roll back models, and HTML pages that render whole model
bundles, including origin statistics that may only be used at area level (rule 10). FastMCP can turn a FastAPI app
into an MCP server in one line.

**Task.** Expose what helps marketing, and make everything else impossible to reach, not merely discouraged.

**Action.**
- **Didn't auto-convert.** An adapter with a 7-route (method, path) allowlist refuses anything else before any I/O.
  A test checks the allowlist against the app's real routes in both directions. Another test fails if
  `from_fastapi` ever appears.
- **Moved the privacy filter to the source.** A per-section field *allowlist* runs inside intel-api's new
  read-only route, and the MCP layer checks again. The tests use bundles that deliberately contain every
  restricted field.
- **Layered for change.** A plain-Python capability core sits behind ports. FastMCP is a thin edge, so a spec
  change or a move to the VPS touches only the edge and the adapters.
- **Made every call accountable.** A hash-chained audit records each call, including denials, and stores the
  arguments only as a hash. It was proven under two concurrent processes.

**Result.** Ten tools reach Claude Code through one committed `.mcp.json`. Scoring 10 drafts against the
production model takes 182 ms over the tailnet. Local brand checks take about 5 ms. Role profiles cut the tool list
from 4.1k tokens (owner) to 650 (writer). 346 tests, 83.83% coverage.

**Trade-off I would defend.** Stdio profiles are least privilege, not a security boundary: any local process can
start `--profile owner`. I said so in the ADR instead of pretending otherwise. Real enforcement comes with signed
tokens over HTTP, and the design already routes Phase 5 agents there.

---

## P4.2 · Choosing a RAG stack by measurement, and letting the test split overrule a favourite

**Situation.** The owner asked for "the best embedding model in the world" and as little hallucination as
possible. Leaderboards disagree, none covers German restaurant reviews, and the VPS has no GPU.

**Task.** Pick the retrieval stack on our own data, honestly, within a CPU budget, and make "I don't know" a
first-class answer.

**Action.**
- **Built a golden set:** 20 real questions plus 10 adversarial ones (personal-data bait, injection, allergens,
  prices, topics the corpus doesn't cover). Labelled **blind**: candidates pooled from every system, shuffled, with
  system and rank hidden. That came to 2,626 judged pairs, so no system was penalised for hits nobody had judged.
- **Benchmarked** 7 embedding models, dense against hybrid, 2 rerankers and 3 rerank depths, with bootstrap
  intervals. Chose on dev; looked at test once.
- **Measured CPU latency** before choosing. The best reranker on dev took 73 s per query on CPU, so it became an
  owner option (a GPU), not the default.
- **Replaced a hard "not found" threshold** with evidence levels, after seeing it hide real answers to broad
  questions.

**Result.**
- The dev favourite (0.85 nDCG@10) fell to 0.67 on test.
- The CPU stack chosen on dev, Harrier-0.6B + a cross-encoder reranker (ONNX int8, about 1.1 s per query), scores
  **0.84 on both splits**, against 0.23–0.51 for keyword or small-model search.
- Hybrid search lost to dense everywhere.
- `verify_citations` flags invented citations and claims that their source doesn't support.

**Trade-off I would defend.** I shipped the second-best reranker on purpose. A model that's 1 point better on dev
but 60× slower on the hardware we actually have isn't better for the business. The GPU option is written down with
its numbers, so the owner can buy it later on evidence.
