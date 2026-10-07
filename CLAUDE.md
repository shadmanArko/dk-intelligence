# dk-intelligence — conventions

Models + MLOps for Dhaka Kacchi. Plan: `docs/PHASE1_PLAN.md` (milestones M0–M8). Mac trains on Sundays; the VPS
serves, measures and asks for approval. MCP is Phase 3, agents Phase 4: neither lives here yet.

## Run it
```
uv sync
uv run intel doctor
uv run intel pull
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

## Rules that must not be broken
1. **Read-only on the warehouse.** Only the `intelligence_reader` role through the tunnel. Never write to the
   harness databases; never change the shared Postgres image or the ordering DB.
2. **No shell on the VPS for Claude.** VPS changes are commands the owner runs, or the deploy pipeline.
3. **Secrets only in `.env`** (git-ignored). Never print DSNs unredacted (`settings.redact`).
4. **Pulls are immutable and verified.** Train only from a complete pull (manifest + SHA256SUMS); every model
   records the pull id it was trained on.
5. **One feature function per model**, used by training AND serving (no training/serving skew).
6. **Only pre-posting information as features** for the content scorer; anything measured after posting is a label.
7. **A challenger replaces the champion only through the judge gates and a human approval.** Rollback must
   always be possible.
8. Times are UTC `timestamptz`; money stays exact (Parquet decimal).
9. **No personal data** beyond what the warehouse already holds (it is minimised: no names, emails or phones).
   Content rules from the data platform still apply (no religious ritual words, no ethnic/religious ad targeting).

## Layout
- `src/intel/settings.py` env settings · `tunnel.py` SSH tunnel (door 1) · `pull.py` warehouse snapshots
- `src/intel/cli.py` the `intel` command
- `docs/` plan and decisions; `data/` (git-ignored) pulls, runs, packages
