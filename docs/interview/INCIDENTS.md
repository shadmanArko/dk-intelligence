# Incident stories

Real things that went wrong, in STAR form. Interviewers ask "tell me about a failure" or "an outage you owned" to
hear blameless analysis and a *systemic* fix. Each story ends with the fix's type: **prevent**, **detect** or
**mitigate**. Sources: [postmortems](../postmortems/README.md) and the milestone results.

---

## 1. A secret in the logs (security near miss)
**Situation.** While setting up the Telegram bot, the service logs on the VPS contained the bot token, and a log
excerpt containing it had been pasted while debugging.
**Task.** Contain it, then make sure no library default can do it again.
**Action.**
- Revoked the token immediately.
- Found the root cause: httpx logs every request URL at INFO, and Telegram puts the token *in* the URL. None of our
  code logged it.
- Raised those loggers to WARNING, and added a redaction filter on every log record (kept when the logs became JSON
  later).
- Added secret scanning in pre-commit and over the full git history in CI.

**Result.** No evidence of misuse, and the token replaced. A test now checks what the *libraries* log, not just our
code.
**Lesson.** Secrets in URLs plus library logging defaults is a class of bug. Fix the class (**prevent + detect**).

## 2. CI red for four days, and nobody knew
**Situation.** The data platform's CI failed on every push for four days. I found it by accident while doing
something else.
**Task.** Fix the failures, then fix the reason nobody noticed.
**Action.**
- Found two latent defects that only a *fresh* database shows: a migration granting rights on schemas that only dbt
  creates, and a duplicate dbt entry. The laptop's long-lived database hid both.
- Fixed both, and made a red `main` in either repo alert Telegram, with the alert path itself tested.

**Result.** CI failures now reach a phone within minutes, and fresh-database migrations are part of every run.
**Lesson.** A CI nobody watches is decoration. Alerting is part of CI (**detect**).

## 3. Dashboards empty on the first deploy
**Situation.** After the monitoring stack's first deploy, every Grafana panel said "No data". Alerting was fine.
**Task.** Restore the dashboards and close the gap in the rehearsal that missed it.
**Action.**
- Found that Grafana auto-updates its bundled plugins at start-up. My hardened, read-only container correctly
  refused the write, so the update failed halfway and the Prometheus plugin was unloaded.
- Pinned the plugins (no runtime self-update).
- Made the deploy script end with an end-to-end query through Grafana's own API.

**Result.** Fixed within the hour. Every deploy now proves data flows, not just that containers started.
**Lesson.** "It started" isn't "it works". Verify through the user's path (**prevent + detect**).

## 4. The first restore drill failed on production
**Situation.** The new weekly restore drill failed on its first run on the VPS: "table model_reference missing".
**Task.** Decide whether the backup was broken or the drill was wrong.
**Action.**
- The backup predated two migrations, so the drill was wrong: it checked an old backup against *today's* schema.
- Changed the drill to check each backup against **its own** schema version, read from the restored copy, and wrote
  a test for exactly that case.
- Earlier, in testing, the drill had also raced the database's init-time restart. It now checks readiness over TCP,
  not the socket.

**Result.** The next run was green: RPO 18 h, restore 4 s. The drill now can't cry wolf on older backups.
**Lesson.** A check that cries wolf gets ignored. Make it precise before making it loud (**detect**, done right).

## 5. A tool rewrote my work onto another branch (near miss)
**Situation.** A background automation fixing the data platform also touched this repository. It rebased my
just-committed milestone onto its own branch, opened a PR, and the PR was merged. My checkout was left behind.
**Task.** Make sure nothing was lost or doubled, then stop it recurring.
**Action.**
- An edit script refused to run because the text it expected wasn't there; it asserts what it replaces.
- Used `git reflog` to reconstruct the sequence, confirmed the merged content equalled my commit, then
  fast-forwarded.
- Made "check branch, reflog and origin before every milestone" a rule. Background tasks now get their own worktree
  and an explicit repository scope.

**Result.** No production impact and no lost work.
**Lesson.** Automation needs blast-radius limits like people do. Defensive edits (assert before replace) are cheap
insurance (**prevent**).

## 6. A test that measured the CI runner, not the service
**Situation.** The new load-test suite went red in CI. It asserted that throughput at 10 clients is at least 80% of
1 client's, and later that p95 at 25 clients stays under 1.5 s.
**Task.** Tell a real regression from an environmental assertion.
**Action.**
- The service is CPU-bound (GIL). A 2-core shared runner with coverage tracing reached 73% throughput, and at
  13.6 req/s, 25 clients queue for about 1.8 s *by arithmetic*.
- Replaced the absolute limits with invariants:
  - no errors;
  - an absolute p95 only for a single client;
  - under load, p95 within 3× of Little's law (concurrency ÷ throughput), so latency must be queueing, not stalls.

**Result.** Green, and still able to catch a real stall on any machine.
**Lesson.** Performance assertions in CI must be ratios or invariants, never numbers from your laptop.

---

## Which story for which question
| They ask | Tell |
|---|---|
| "A failure you owned" | 4 (restore drill) or the H0 content-scorer failure in STORIES.md |
| "A security issue" | 1 (token in logs) |
| "When did monitoring fail you?" | 2 (red CI) or 3 (empty dashboards) |
| "Working with automation / AI tools" | 5 (parallel session) |
| "A flaky test" / "testing performance" | 6 |
| "Disagreeing with a metric" | 6, or H4 in STORIES.md (PSI without significance cries wolf) |
