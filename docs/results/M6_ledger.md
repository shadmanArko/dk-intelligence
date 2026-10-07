# M6 — Prediction ledger, measurement, backup, auto-deploy (2026-10-07)

## Live
- **Hourly measurement inside intel-api.** Every FB/IG post published from 2026-10-07 is scored once by the active
  `content_scorer` and once by the production `harness_predictor` (over the harness network). Only pre-posting
  inputs are used. Outcomes are recorded at 24h / 72h / 7d.
- **First run on the VPS:** 0 new posts, 0 errors. The ledger starts empty on purpose; the warehouse imports
  posts daily at 04:30–05:00.
- **Ledger access:**
  - `/v1/ledger/summary`: live accuracy per model;
  - `/v1/ledger/export`: rows for training;
  - Telegram `/status`: a live-ledger section.
- **Backup:** `deploy/backup.sh` at 02:50 writes `intel_*.sql.gz` into `/opt/dhaka-kacchi/backups`. The harness's
  03:00 job syncs it to Backblaze B2 (30-day retention). The first dump (8 KB) was verified.
- **Door 3:** GitHub Actions → forced-command key → `/opt/dhaka-kacchi/bin/run-intel-deploy.sh` (git pull + deploy.sh).
  Verified with the "Run workflow" button: test and deploy were both green, and the VPS logged `/ready` with the
  approved model. Every push to main now tests and then deploys.

## Design choices
- **Horizon windows:** the first snapshot within +12h / +24h / +48h after the 24h / 72h / 7d mark, with its real
  age stored. A later snapshot is never used for an earlier horizon (that bug was caught by a test).
- **Live label:** engagement > median latest engagement of EARLIER posts on the same platform. Training's
  expanding median includes the post itself, but live scoring cannot know that number yet.
- **Outages:** if the predictor is down, that model's score is backfilled later, but only while the post has no
  outcome yet (a prediction made after the fact would not be honest).
- **Privacy:** captions are never stored in the ledger, only a SHA-256 hash and the length.

## Incidents (fixed)
1. **The bot token leaked into the container logs.** httpx logs request URLs at INFO, and Telegram puts the token
   in the URL. The logs were pasted into chat, so the token was revoked in BotFather and replaced.
   - Fix (90f8d9c): httpx and httpcore are WARNING-only, a log filter masks token-shaped strings, and a test
     checks both.
   - The Mac `.env` no longer holds the token; only the VPS has it.
2. **The first measure run failed:** the VPS `INTEL_WAREHOUSE_DSN` used the Mac's tunnel address
   (127.0.0.1:5435) instead of `postgres:5432` on the harness network.
