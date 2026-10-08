# Postmortem: Grafana showed "No data" on every panel after the first deploy

- **Date:** 2026-10-08, ~1 h · **Impact:** dashboards unusable; alerting unaffected (Prometheus and Alertmanager
  were healthy) · **Severity:** ticket · **Status:** final

## Summary
Grafana 13 auto-updates its bundled plugins from the internet at start-up. Our hardened, read-only container could
not write the update; the update failed halfway and Grafana unloaded its Prometheus plugin.

## Root cause
A runtime self-update in a pinned image. The read-only file system did its job (it refused an unreviewed change);
the gap was that the rehearsal on the Mac only checked that dashboards *existed*, never that a panel returned data.

## Detection
The owner opened the dashboards. A check after deploy should have caught it.

## Actions
| Action | Type | Status |
|---|---|---|
| `GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false`: versions change only with the pinned image | prevent | done |
| `up.sh` ends with an end-to-end query (Grafana → Prometheus) on every deploy | detect | done |
| Rehearsals verify data through the UI's own API, not just that it started | detect | practice |
