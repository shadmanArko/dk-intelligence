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
uv run intel champion reproduce   # M2: re-check the harness predictor, write the benchmark
uv run intel market pull          # snapshot market priors, occasions and cue dictionary from the data platform
uv run intel train content-scorer # M3: train candidates, judge against the champion (report + MLflow)
uv run intel weekly               # the full Sunday run now (also: double-click "Run Sunday training.command")
uv run intel weekly --if-due      # what launchd runs hourly: only if this week's run has not happened
uv run intel weekly-status        # current run step by step, recent runs, due or not
uv run mlflow ui --backend-store-uri sqlite:///data/mlflow.db   # browse runs at http://127.0.0.1:5000
uv run pytest                     # set INTEL_TEST_PG_DSN to also run the Postgres round-trip test
```

## How the warehouse is reached (door 1)
`intel pull` opens `ssh -N -L 5435:127.0.0.1:5432 intel@<vps-tailscale-ip>` itself and closes it afterwards.
The `intel` user can only forward to the database port, only from the Mac's Tailscale address. The database role
`intelligence_reader` is read-only. If a tunnel is already open on the local port, it is reused.

## Data
`data/` is git-ignored. Pulls are immutable: a run writes `<id>.partial/`, renames it when complete, and
updates `data/pulls/LATEST`. The newest `INTEL_KEEP_PULLS` pulls are kept. Training always names the pull it used.

## Control Room (Mac)
Double-click **`DK Control Room.command`** (or `uv run intel control`): a local page (127.0.0.1 only) with a
**▶ Run training now** button and the live pipeline diagram: every box lights up while it runs, click any box for
what it does and what happened (rows pulled, candidates, each judge check, ...), plus the live log and run history.
Runs started there (or with `Run Sunday training.command`) run in the background: closing a window never stops them.
The diagram comes from `src/intel/pipeline.py`; add a step or a node there and it appears everywhere.

## Schedule and dashboards
- **Mac:** launchd `com.dhakakacchi.intel.weekly` (`scripts/com.dhakakacchi.intel.weekly.plist`) runs
  `scripts/weekly.sh --if-due` at login, every hour and on Sundays 12:00. A run starts once per week; if the Mac
  was off on Sunday, it starts within an hour of being turned on. Logs: `data/logs/weekly_*.log`.
- **VPS page** (tailnet): https://vmd206122.tailc055f3.ts.net. *Approvals* · *Posts* (predicted vs real per post)
  · *Try a draft* · *Training* (live step-by-step progress, history, deployed version).
- **Telegram:** `/status`, `/training`, `/rollback`; run results after every Sunday run; Monday 09:00 report.
- **GitHub Actions:** every push to main runs the tests and then deploys to the VPS.
