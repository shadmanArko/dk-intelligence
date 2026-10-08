# 0016. Observability: metrics, probes, SLOs and alerts as code

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
Until H3 the only signals were Telegram run reports and `curl /ready` by hand. A stalled worker (the measurer, the
inbox watcher), a full disk, a failing backup or a slow model would be found by noticing, if at all. The data
collector printed `!! failed` for a failing step and the run carried on, so a step could fail daily unseen. The VPS has
no swap, so memory exhaustion kills a process without warning.

## Decision
1. **Instrument the service:** `/metrics` (Prometheus) with requests, latency and status per *route template*,
   predictions per model/source, worker heartbeats and errors, and scrape-time state from the database (active models,
   ledger freshness, last training report, pending approvals). One structured JSON log line per request with a
   request id (accepted from `X-Request-ID` when well-formed, always returned). Token redaction still applies.
2. **Measure from outside:** a blackbox probe of `/health/deep` every 30 s (database + a real prediction, nothing
   written to the ledger) is the ground truth for the SLOs in `docs/SLO.md`.
3. **Self-hosted stack** (separate compose project, tailnet-only, images pinned by digest, read-only containers,
   memory limits ~800 MB total, observed use lower): Prometheus (35-day retention), Alertmanager → Telegram,
   Grafana (dashboards provisioned from JSON in git, UI edits disabled), node-exporter (host + textfile metrics from
   backups, deploys and the collector), blackbox exporter.
4. **SLO alerts by burn rate**, multi-window, with inhibition so one incident sends one message.
5. **Everything is code and tested:** `deploy/monitoring/check.sh` (CI) runs `promtool check config`, **`promtool
   test rules`** (unit tests of which alerts fire, when, with which text), `amtool check-config`, compose validation,
   and a PromQL parse of every dashboard query. `up.sh` renders secrets at deploy time (token in a 600 file, never in
   the config) and reloads; `deploy.sh` calls it after each successful deploy (never fatal for the app).

## Consequences
- Good: a dead worker, full disk, stale backup or failing collector step becomes a Telegram message; burn-rate
  alerts page within minutes for a hard outage and stay quiet for a single failed probe (unit-tested).
- Good: rehearsed with real containers: API stopped → `IntelApiDown` firing after 3.5 min, the SLO alerts
  suppressed by inhibition, Telegram attempted; restored → resolved 41 s later.
- Cost: ~300–500 MB RAM on the VPS; one more stack to update (Dependabot covers the images).
- Known limit: Alertmanager and Telegram are the alert path; if the whole VPS dies, nothing on it can say so. An
  external dead-man's switch (a free heartbeat service pinged by an always-firing alert) is the next step; it sends
  data off the box, so it is a separate decision.

## Alternatives considered
- Grafana Cloud free tier: no RAM cost, but metrics leave our servers and the free tier's limits change.
- Request-based SLIs only: at a few requests per hour, one bad request would swing the ratio wildly.
- Uptime pings from outside only: catch "down", miss "up but every worker stalled" and "slow".
