# dk-intelligence on one page

**What it is.** The ML platform of Dhaka Kacchi Berlin, a kacchi biryani kitchen run by one person. Seven models tell
the owner what to post, when and where:
- **2 scorers:** will this draft beat our usual post, and will it reach more people?
- **5 insight models:** competitor weaknesses, a Berlin area map, an occasion planner, the website funnel, creator
  matches.

It runs on a laptop and one small shared VPS, for **€0 a month in ML infrastructure**, and it is built and operated
like a production system.

## The loop

```
  Mac, every Sunday                        VPS, 24/7
  ──────────────────                       ─────────
  snapshot (immutable, checksummed)
    → data contracts (bad data stops)
    → train 7 plug-in models
    → JUDGE vs the current champion  ──fail──► Telegram report
    → package (model + sealed code + card)
    → upload-only inbox  ───────────────────►  intake: hashes, code match
                                               → OWNER APPROVES on Telegram
                                               → serve (deploy-by-digest image)
                                               → shadow ledger: predict BEFORE the outcome
                                               → monitor drift + live accuracy, hourly
                                               → guardrail: auto-rollback if significantly worse
           ◄──────── "retrain early" ─────────  (drift / degradation)
```

**Autonomy boundary:**
- Monitoring may *trigger training*.
- The guardrail may *restore a version the owner already approved*.
- **Only the judge plus a human may promote a model.**

## Guarantees and the evidence behind them
| Guarantee | Mechanism | Evidence |
|---|---|---|
| Production runs exactly what CI tested | build once, scan, deploy by digest; smoke test, automatic rollback | rollback rehearsed: ~40 s |
| A new model must beat the old one on the same data | 7-gate judge, paired bootstrap CI, no platform worse | production model 0.688 vs 0.631 balanced accuracy (n = 75, newest posts) |
| Every prediction is honest evidence | shadow ledger: scored before the outcome exists | live gate uses it |
| Bad data never trains a model | pandera contracts on all 17 input tables | caught a real 3.6× audience inflation (subtotal rows) |
| A model can be rebuilt bit-exactly | lineage + sealed code + `intel reproduce` | 75/75 predictions identical |
| History can't be quietly edited | DB-enforced lifecycle + hash-chained audit log | tamper test; verified in every restored backup |
| A degraded model doesn't stay live | McNemar test vs the previous system on the same posts | withdraws within the hour, never ping-pongs |
| We'd know about any outage, including the box dying | SLOs (99.5%, p95 < 300 ms), burn-rate alerts, dead-man's switch | outage detected in 3.5 min; 30 alert-rule unit tests |
| Backups actually restore | weekly restore drill into a throwaway DB | RPO 18 h, restore 4 s |
| The host is code | Ansible pull-mode playbook | applied to production: 2nd run `changed=0` |
| We know the capacity | load test, latency budget by stage | ~70 req/s/process; p95 230 ms at 10 clients |
| Personal data is minimised *(built, merge pending)* | policy-as-code, pull guard, fairness gate, audited retention | pulls 41 → 5 tables; 0 commenter identities |

## Design principles (each one has an ADR, 24 in total)
1. **Right-sized tooling.** No Kubernetes, feature store or Kubeflow. Each has a written trigger in `SCALING.md`.
2. **Three narrow doors.** A read-only tunnel, an upload-only inbox and a forced-command deploy. The AI pair and the
   laptop never get a shell on production.
3. **Evidence over opinion.** The judge, the shadow ledger and the reproduction check: a claim about a model needs
   data that model never saw.
4. **Ratchets, not big bangs.** Typing, coverage and vulnerabilities only move one way, enforced in CI.
5. **Fail safe and loud.** Contracts stop the run, the guardrail rolls back, alerts link a runbook, and incidents
   get postmortems.

**Numbers:** 232 tests + 30 alert-rule cases · 82.6% branch coverage · 24 strict-typed modules · 0 fixable
HIGH/CRITICAL CVEs · image 1.63 GB → 955 MB · 24 ADRs · 4 postmortems · 3 repositories.
