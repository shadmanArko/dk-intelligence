# M5 — Serving, approval and deploy on the VPS (2026-10-07)

**Live:** `intel-api` and `intel-db` on vmd206122 (compose project `dk-intelligence`). The approval page is
https://vmd206122.tailc055f3.ts.net (tailnet only, via `tailscale serve`). The bot is `@dk_intel_controller_bot`,
locked to chat 8530058914.

## End-to-end, first time
1. On the Mac: `intel train content-scorer --baseline`. The champion recipe in the new format gives identical
   test scores (0.6248 / AUC 0.6963 / Brier 0.2132). It was refit on all 373 matured posts.
2. `intel package build` → `intel package upload`: the remote checksums matched.
3. The VPS inbox watcher verified the package, checked that the feature code hashes match the server's code,
   extracted it, and opened an approval.
4. The owner approved it in Telegram, and it was activated: `/ready` showed `content_scorer: cs-base-20261007T1743-33eb38`.
5. `/v1/predict` on the VPS returned 0.8456 for a test Reel. The production harness predictor gives 0.837 for
   the same input: the same recipe, with 13 more posts.

## Checks over the tailnet (from the Mac)
| Check | Result |
|---|---|
| Page over HTTPS | 200; `X-Frame-Options: DENY`, CSP `default-src 'none'`, HttpOnly SameSite=Strict CSRF cookie |
| Forged approve without the page token | 403 |
| Rollback with a real token but a foreign Origin | 403 |
| Port exposure | the API is bound to `127.0.0.1:8710` only; tailnet access goes via `tailscale serve` |

Local Docker run before deploying: intel-api used 144 MB of 512 MB, intel-db 18 MB of 384 MB. The image is
1.6 GB (pyarrow, scipy and sklearn wheels); it can be slimmed later.

## Lessons
- Telegram allows one `getUpdates` reader per bot. Only the VPS polls; the Mac must never read updates.
- The macOS system rsync is openrsync (no `--chmod`).
- Appending to a `.env` that lacks a final newline glues lines together (it happened once with the chat id).
  Scripts must check for the trailing newline first.

## Open
- Door 3 (GitHub auto-deploy) is not set up yet. Deploys are manual for now: `/opt/dhaka-kacchi/dk-intelligence/deploy/deploy.sh`
  after a `git pull`. See deploy/README.md step 5.
- `intel-db` is not in the nightly backup yet. It holds registry data only (M6/M7).
