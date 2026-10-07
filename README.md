# dk-intelligence

Models and MLOps for Dhaka Kacchi Berlin. **Train on the Mac (Sundays), judge, approve on Telegram, serve and
measure on the VPS.** The plan and milestones are in [docs/PHASE1_PLAN.md](docs/PHASE1_PLAN.md).

| Repo | Role |
|---|---|
| `dhaka_kacchi_ai_harness` | VPS stack: warehouse, crons, current predictor, cockpit design. Source of first-party data. |
| `MarketIntelligence Data Platform` (`mip`) | Market data: 1.87M public posts, competitors, occasions, communities. |
| **`dk-intelligence`** (this) | Models: pull → train → judge → package → approve → serve → measure. |

## Setup (Mac)
```bash
uv sync
cp .env.example .env      # then fill in; never commit .env
uv run intel doctor       # settings, tunnel, read-only login
```

## Commands
```bash
uv run intel pull                 # snapshot the VPS warehouse -> data/pulls/<id>/ (Parquet + manifest + SHA256SUMS)
uv run intel pull --tables social_post,social_metrics_snapshot
uv run intel pulls list
uv run intel pulls verify [ID]    # re-check checksums and row counts (default: latest)
uv run pytest                     # set INTEL_TEST_PG_DSN to also run the Postgres round-trip test
```

## How the warehouse is reached (door 1)
`intel pull` opens `ssh -N -L 5435:127.0.0.1:5432 intel@<vps-tailscale-ip>` itself and closes it afterwards.
The `intel` user can only forward to the database port, only from the Mac's Tailscale address. The database role
`intelligence_reader` is read-only. If a tunnel is already open on the local port, it is reused.

## Data
`data/` is git-ignored. Pulls are immutable: a run writes `<id>.partial/`, renames it when complete, and
updates `data/pulls/LATEST`. The newest `INTEL_KEEP_PULLS` pulls are kept. Training always names the pull it used.
