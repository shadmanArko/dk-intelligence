# 0024. Data protection and responsible AI as code

Date: 2026-10-09 · Status: Accepted

## Context
Before writing any rule, we measured what the system actually held:
- **The warehouse pull copied all 41 readable tables to the Mac, but the models read 5.** The other 36 included the
  harness's customers, orders with customer ids, customer reviews, agent actions, and the raw Facebook post payloads.
  Those payloads embed comments: **13 commenter identities (`from.id`, `from.name`) and 66 comment texts** in every
  pull, with 12 pulls kept. No model used any of it.
- **Retention was unbounded in four places.** Nothing deleted API draft predictions or operational events. Docker kept
  container logs forever. The cron job logs grew without end. Our own backups were pruned only as a side effect of the
  harness's job.
- **Two approved insight models read data derived from residents' origin or citizenship.** The community map ranks
  areas partly by South Asian-origin residents, and the occasion planner sizes occasions by Berlin residents per
  citizenship. Both use official aggregate statistics and never individuals. That was written in prose, but no check
  enforced it.
- **The ModelCard said "no personal data of customers or commenters is used or produced"**, and nothing verified it.

A naive PII scan of the pulls flagged every UUID, Facebook id, decimal and app version number (`1.2.3.4` looked like an
IP address). A scan that noisy gets switched off in a week.

## Decision
1. **One policy file** (`src/intel/privacy/policy.yaml`) declares every table we pull or store: its purpose, its
   personal-data level (`none` / `pseudonymous` / `declared`), its categories and its retention. Tests check it against
   the real tables in both directions:
   - every migration table and every contract table is declared;
   - every retention rule names a real column.

   The record of processing (`docs/privacy/RECORD_OF_PROCESSING.md`) is rendered from it, and a test fails if the
   committed copy drifts.
2. **Purpose limitation by default.**
   - A warehouse pull copies only the declared tables (now 5). A new table needs a purpose in the policy first.
   - Older pulls are cleaned with `intel privacy minimise`. It removes only undeclared tables, so every file a model
     reads stays byte-for-byte identical and `intel reproduce` is unaffected. The manifest records what was removed
     and the original checksums.
3. **The pull guard** runs inside `run_pull` on the half-written pull, before checksums and the rename:
   - it redacts emails and phone numbers in third-party free text (competitor review texts);
   - it scans every table;
   - undeclared personal data **fails the pull**, and with it the weekly run.

   Reports carry counts, never values.
4. **Precise detectors.**
   - Emails.
   - Phone numbers in international form only, 9–15 digits, never inside a longer token (UUID, id, decimal).
   - IBANs, validated by the mod-97 checksum.
   - Identity keys in JSON (`email`, `phone`, `ip`, `from` when it holds an object, ...).

   The business's own phone number is allow-listed **by SHA-256**, so it doesn't appear in the repository.
5. **A responsible-AI gate in `build_package`.** No package is built unless all of these hold:
   - **(a) protected attributes by name:** religion, ethnicity, nationality, citizenship, ... and the data platform's
     communities of origin (added from the run's snapshot) appear in no key, feature or column name;
   - **(b) protected attributes by lineage:** a model that reads a table marked `protected_derived` needs a reviewed
     exception for exactly those tables. The exception records model, tables, basis, use, reviewer, date and an
     expiry date, and an expired exception fails the gate;
   - **(c) blocked words:** the content playbook's blocked words don't appear in text a model *writes*;
   - **(d) personal data:** none in the report or the served bundle.

   The result is recorded in the package metadata and on the model card.
6. **Retention is enforced by the owner of the data.**
   - **intel-api:** a daily job deletes API predictions after 90 days, events after 180 days and training reports
     after 365 days. The shadow ledger and governance records are kept, because they hold no personal data and are
     the audit evidence. All deletions and their audit entry (`retention_purge`) commit in one transaction. A stalled
     job alerts after 2 days (`IntelWorkerStalled{worker="retention"}`).
   - **Files:** `backup.sh` prunes its own dumps after 30 days. Container logs rotate at 3 × 10 MB. The playbook
     installs logrotate for our job logs (weekly, kept 8 weeks).
7. **The community map and the occasion planner keep their origin-derived inputs** as reviewed exceptions, by the
   owner's decision of 2026-10-08 (area or city aggregates of public statistics; used to choose areas and occasions,
   never to target or exclude people). Both are reviewed again by April 2027.

## Consequences
- The Mac holds 36 fewer tables per pull, including every customer table and every commenter. "Which personal data do
  you hold, why, and for how long?" has a one-page answer that a test keeps true.
- A harness change that adds a column with emails into a pulled table now stops the weekly run with a message naming
  the table, the column and the count. That is the intended cost: the run fails safe instead of quietly copying it.
- The gate's name check is heuristic, so lineage is the real control. A model can't hide origin data behind an
  innocent field name, because the gate looks at what it *reads*, from the data contracts (ADR 0017).
- Not done, and written down: a full DPIA (the processing isn't high-risk under Art. 35: no profiling of individuals,
  no automated decisions with legal effect), and data-subject request tooling in this repository (customer data lives
  in the harness, which has erasure built in: `deleted_at` ⇒ empty contact hashes).
