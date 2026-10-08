# Phase 3 · H10: data protection and responsible AI (2026-10-09)

Decision record: [ADR 0024](../adr/0024-data-protection-and-responsible-ai-as-code.md). Policy:
`src/intel/privacy/policy.yaml`. Rendered: [record of processing](../privacy/RECORD_OF_PROCESSING.md),
[hashing rules](../privacy/HASHING.md).

## What the system held (measured before any rule was written)
| Finding | Measured | Now |
|---|---|---|
| Warehouse tables copied to the Mac per pull | 41 (models read 5) | **5**, declared with a purpose |
| Facebook commenter identities / comment texts per pull | 13 / 66 (× 12 kept pulls) | **0**: the table is not pulled; old pulls cleaned by `intel privacy minimise` |
| Customer, order and review tables on the Mac | `customer`, `orders`, `review`, `event`, … | **not pulled** |
| "IP addresses" in website events | 285 | **0**: they were app version numbers (`1.2.3.4`); random ids and coarse GeoIP only |
| Phone numbers in our captions | 81, all the same number | the business's own, allow-listed by hash |
| Unbounded retention | API predictions, events, container logs, job logs | 90 d / 180 d / 3 × 10 MB / 8 weeks, enforced |
| Models reading origin-derived data | 2 (community map, occasion planner) | reviewed exceptions, with an expiry date |

## The scan is precise (otherwise it gets switched off)
On a full pre-policy pull (41 tables, 12k rows), a naive scan flagged UUIDs, Facebook ids, decimals and version numbers
in 30 columns. The final detectors flag exactly one thing: the 13 commenter identities.
- **Market snapshot (14 tables):** one creator email, in a table declared for public creator profiles.
- **Speed:** a scan takes 0.7–1.1 s per snapshot.

## The gate on the models in production
All 7 production models pass on their latest real outputs (report + served bundle).

While building the gate, its first version produced four false positives. Each was fixed at its cause, not by adding
an exception:

| First flag | Why it was wrong | Fix |
|---|---|---|
| `origin` in the community map | the pickup point (Leopoldplatz), a geographic origin | the bare word removed; `country_of_origin` kept |
| `south_asian_specialists` in the creator matcher | creators who post South Asian *food*: a cuisine | names alone can't tell, so lineage became the real control |
| `lakshmi` in the weakness miner | a quote from a competitor review | blocked words apply to text a model *writes* (occasion planner) |
| the community map's origin groups, if named innocently | a name-only check would miss them | the lineage rule: reading `lor_community` needs the exception, whatever the fields are called |

## Retention, live
| Where | Rule | Enforced by |
|---|---|---|
| intel-db `prediction` (API drafts) | 90 days | daily job in intel-api, one transaction with its audit entry |
| intel-db `event_log` / `training_run` | 180 / 365 days | same |
| shadow ledger, outcomes, registry, audit | kept (no personal data; audit evidence) | stated in the policy |
| VPS intel-db dumps | 30 days | `deploy/backup.sh` (no longer only the harness's job) |
| container logs | 3 × 10 MB | `deploy/docker-compose.yml` |
| job logs | weekly, 8 kept | logrotate from `infra/site.yml` (tested with a forced rotation in the container) |

A stalled job alerts after 2 days (`IntelWorkerStalled{worker="retention"}`, promtool-tested).

## Tests
- **`tests/test_privacy.py`, 27 cases:**
  - the policy against the migrations, the data contracts and the market queries;
  - 10 detector cases, including the false positives;
  - the guard, the redaction and the minimiser on written pulls;
  - the gate on fabricated and real outputs; `build_package` refusing a run;
  - retention against a real Postgres, including dry run, the audit chain and idempotency.
- **Elsewhere:** 1 infra test (log rotation) and 1 promtool test.
- **Strict typing:** 30 modules (6 new).

## Still to do on the machines
- **Mac:** `uv run intel privacy minimise` cleans the 12 existing pulls. It removes only undeclared tables and
  re-checksums, so `intel reproduce` is unaffected (tested on a copy of the real latest pull: 35 tables removed, 5
  model tables byte-for-byte identical, `verify` OK).
- **VPS:** re-run the playbook (`--check --diff`, then apply) to install the logrotate rule. The retention job and the
  container log limits arrive with the normal deploy.
