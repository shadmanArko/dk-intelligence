# dk-intelligence on the homelab server

Phase IW of [the server move](../../docs/SERVER_MOVE_PLAN.md) (ADR 0044): the Sunday training runs here, not on the
Mac. Deploy request: `~/homelab/deploy-requests/intel-weekly-server.md` (private, outside the repo).

| File | What |
|---|---|
| `Dockerfile` | Python 3.13 + the `train`, `rag`, `mcp` groups; ssh + rsync for doors 1 and 2; tini; uid 10004; read-only root |
| `compose.yaml` | the `weekly` batch job: no ports, `cap_drop: ALL`, 4 CPU / 6 GiB, secrets as files; joins the mip and dk-rag networks + an egress bridge (VPS over Tailscale) |
| `entrypoint.sh` | secret files -> `MIP_READER_DSN`, `INTEL_RAG_ADMIN_DSN`, `DK_WAREHOUSE_DSN` and the two door key paths |
| `run-weekly.sh` | due? -> `intel server-preflight` (exit 75 = not ready) -> `intel weekly --if-due` |
| `intel-weekly.service` / `.timer` | hourly + Sunday 12:00 Berlin, `Persistent=true`, max 10 h |
| `rebase_mlflow.py` | one-time: point the copied `mlflow.db` at `/data/mlruns` |
| `env.example` | non-secret settings for `/srv/apps/intel-weekly/weekly.env` |

Mounts: `/srv/data/dk-intelligence` -> `/data` (the Mac's `data/` folder, copied once), the data platform checkout
(read-only, `/mip-repo`) and its data folder (read-only, `/mip-data`: the weekly status file and bulk wikidata).

The run waits for `/mip/data/logs/weekly_last_status` to show a **successful** refresh of this week before it trains.

```bash
cd /srv/apps/intel-weekly/repo/deploy/server
docker compose --env-file /srv/apps/intel-weekly/weekly.env run --rm --no-deps weekly intel server-preflight
docker compose --env-file /srv/apps/intel-weekly/weekly.env run --rm --no-deps weekly intel weekly-status
docker compose --env-file /srv/apps/intel-weekly/weekly.env run --rm --no-deps weekly intel weekly --wait-hours 0
```
