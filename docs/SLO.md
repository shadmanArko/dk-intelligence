# Service level objectives — intel-api

What we promise about the service, how it is measured, and what happens when the promise is at risk (ADR 0016).
Rules: `deploy/monitoring/prometheus/rules/slo.yml` · unit tests: `deploy/monitoring/prometheus/tests/slo_test.yml`.

## Why these, and why measured this way
intel-api answers drafts (Telegram, Try a draft, `/v1/predict` for the harness) and scores every new post. Traffic is
a few requests per hour, so request-based ratios would swing wildly on one bad request. The SLIs therefore come from
an **outside-in probe**: every 30 s the blackbox exporter calls `/health/deep`, which checks the database and makes a
real prediction with the active model (without writing to the ledger). That is 86,400 samples per 30 days, steady
regardless of traffic.

## Objectives (30-day rolling window)
| SLI | Objective | Error budget | Budget in time |
|---|---|---|---|
| **Availability**: share of probes that succeed (HTTP 200 and `"status":"ok"`) | **99.5%** | 0.5% of probes | ~3 h 36 min / 30 days |
| **Latency**: share of *successful* probes answering within 300 ms | **95%** | 5% of successful probes | — |

Why 99.5% and not 99.9%: one VPS, no redundancy, deploys restart the container (a few seconds), and the owner is the
only on-call. 99.9% (43 min / month) would be spent by a single bad evening. 99.5% is a promise we can keep and still
notice real trouble. The rehearsed probe takes ~25–40 ms, so 300 ms leaves room for model and database growth.

## Alerts (multi-window, multi-burn-rate — Google SRE Workbook ch. 5)
Burn rate = how fast the budget is being spent relative to "exactly used up in 30 days".

| Alert | Condition | Meaning | Severity |
|---|---|---|---|
| `…BurnFast` | burn > 14.4 over 5 min **and** 1 h | 2% of the monthly budget gone in one hour | page (Telegram 🔴, repeats hourly) |
| `…BurnSlow` | burn > 6 over 30 min **and** 6 h | 5% gone in six hours | ticket (Telegram 🟠, repeats every 12 h) |

The long window proves the problem is real; the short window proves it is still happening (so the alert clears
quickly after a fix). When intel-api cannot be scraped at all, `IntelApiDown` fires and Alertmanager **inhibits** the
SLO alerts: one message per incident, not three.

## Budget policy
| Budget left (30 d) | What we do |
|---|---|
| > 50% | normal: ship features, deploy any time |
| 25–50% | deploys only with a rehearsal or a quick rollback plan; look at the top failure cause |
| < 25% | reliability work first (the cause of the burn) before new features |
| < 0 (SLO missed) | write a postmortem (template in docs/runbooks, H7) |

## Other promises watched (not SLOs, but alerting)
| What | Threshold | Alert |
|---|---|---|
| Background workers finish loops | inbox / telegram < 5 min, reports < 20 min, measure < 3 h | `IntelWorkerStalled` |
| Weekly training arrives | < 8 days | `IntelTrainingOverdue` |
| intel-db backup | < 26 h old | `BackupStale` |
| VPS collector | a clean daily run < 26 h ago; no failed steps | `CollectorStale`, `CollectorStepsFailing` |
| Disk / memory (no swap!) | > 15% / > 10% free | `HostDiskLow`, `HostDiskCritical`, `HostMemoryLow` |
| The monitoring itself | rule evaluation, notification delivery, root-disk series present | `PrometheusRuleFailures`, `AlertmanagerNotificationsFailing`, `HostDiskMetricMissing` |
