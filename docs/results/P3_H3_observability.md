# Phase 3 · H3 — observability, SLOs and alerting (2026-10-08)

## What exists now
| Layer | What | Where |
|---|---|---|
| Service metrics | requests / latency / status per route template, predictions per model & source, worker heartbeats + errors, active models, ledger and training freshness, pending approvals, DB reachability | `intel-api /metrics` (`serve/observability.py`) |
| Deep health | database + a real prediction, no ledger write; used by the prober **and** the deploy smoke test | `GET /health/deep` |
| Logs | one JSON line per request with request id; uvicorn access log off; token redaction kept | stdout → `docker compose logs` |
| Probing | blackbox exporter → `/health/deep` every 30 s | the SLO ground truth |
| Host + jobs | node-exporter: disk, memory, load; textfile metrics from backups, deploys, the data collector | `/opt/dhaka-kacchi/metrics/*.prom` |
| SLOs | availability 99.5%, latency 95% < 300 ms (30 days), error-budget policy | `docs/SLO.md` |
| Alerts | 2 × burn-rate pairs + 14 operational / meta alerts → Telegram, with inhibition | `prometheus/rules/*.yml` |
| Dashboards | "intel-api · SLOs and service" (19 panels), "VPS · host and scheduled jobs" (14), provisioned read-only | `grafana/dashboards/*.json` |
| Checks in CI | config, **alert-rule unit tests**, Alertmanager config, compose, dashboard PromQL | `deploy/monitoring/check.sh` |

## Found and fixed on the way
- **A misleading page:** the first rule unit test showed the fast-burn alert saying "100% of probes failed in the last
  5 min" while printing the **1-hour** ratio (19.17%): in `A and B` Prometheus reports A's value. The short window now
  comes first.
- **A silent collector:** a failing step printed `!! failed` and the run continued; now every run publishes its failed
  step count and the last clean run, and two alerts watch them (rehearsed: clean run → 0; failing run → 1 and the last
  clean time kept).
- **A blind spot by design:** the disk alerts match `mountpoint="/"`; if that series disappeared they would be silent
  forever. `HostDiskMetricMissing` alerts on its absence (found because the Mac's Docker VM has no `/`).
- **Smoke-test side effect:** H1's smoke test called `/v1/predict`, which writes to the prediction ledger, so every
  deploy added one fake prediction. It now uses `/health/deep` (falls back to `/v1/predict` only for images older than
  this change, i.e. a rollback target).
- `node-exporter`'s usual `rslave` mount fails on Docker Desktop; a plain read-only mount works on both.
- **Works on my Mac, fails on Linux:** CI's first H3 run failed in `check.sh`: `amtool: permission denied` on the
  rendered config. The temp dir is mode 700 and the tool container runs as `nobody`; Docker Desktop ignores bind-mount
  permissions, Linux enforces them. The check tools now run as the calling user, exactly like Alertmanager runs as the
  deploy user on the VPS (re-checked: every VPS container can read what it mounts).

## First day on the VPS
- **Port clash:** 127.0.0.1:3000 belongs to `dk-publishing-webserver-1`; Grafana's port is now a setting (3330).
- **Every panel empty:** Grafana 13 auto-updates its bundled plugins from the internet at start-up. On the read-only
  container file system the update of the Prometheus plugin failed halfway and Grafana unloaded the plugin ("Could
  not find plugin definition"). The read-only file system did its job (it refused an unreviewed runtime change); the
  fix is `GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false`, so plugin versions change only with a reviewed image update.
  The Mac rehearsal had only listed the dashboards, never queried one: `up.sh` now ends with an **end-to-end check**
  (Grafana's datasource health = a real query to Prometheus) and says so loudly if it fails.
- **Hand-run `docker compose` commands failed** in the monitoring folder (variables only existed inside `up.sh`), so
  the Telegram test command never ran. `up.sh` now saves them into a managed block of `.env`.

## Rehearsal with real containers (Mac, throwaway clone)
`deploy.sh --local-build` → app smoke OK → `monitoring/up.sh` started the five services (the first attempt failed on
the mount above, and the app deploy stayed up as designed). Then:
- all 5 scrape targets up; probe success 1 at 25–40 ms; SLO error ratio 0; `intel_db_up` 1; the deploy outcome flowed
  through the textfile collector; 30 rules healthy; Grafana loaded both dashboards;
- **outage drill:** intel-api stopped at 15:42:36 → `IntelApiDown` firing at 15:46:09 (3 min `for` + scrape timing);
  Alertmanager kept `IntelApiDown` active and **suppressed** both SLO alerts by inhibition (one message per incident);
  Telegram delivery attempted (failed with the rehearsal's fake token and retried, the path
  `AlertmanagerNotificationsFailing` watches); restarted at 15:46:28 → resolved at 15:47:09.

## Tests
160 tests (+8): metrics content, route-template labels, request ids, JSON logs, redaction, `/health/deep` without
ledger writes, smoke test with a real model and against an older image; plus 9 promtool rule-test cases (4 SLO, 5 operational).
