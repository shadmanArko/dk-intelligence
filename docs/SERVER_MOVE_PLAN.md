# Moving the Sunday training and the Content Studio off the Mac — plan for approval

**Status:** approved by the owner on 2026-11-23 with the recommended decisions A (new door keys for the server,
ADR 0044), B1 (Claude Code logged in on the server) and C1 (reminder via intel-api -> Telegram). Phase IW is built
(`deploy/server/`, deploy request `~/homelab/deploy-requests/intel-weekly-server.md`); IR and IS follow.
**Goal:** the Mac runs no scheduled job. The three dk-intelligence launchd agents
(`com.dhakakacchi.intel.weekly`, `com.dhakakacchi.studio`, `com.dhakakacchi.studio.reminder`) move to the homelab
server, like the mip weekly refresh (mip repo `deploy/weekly/`, deploy request MW-1…MW-6).

## Target picture

```
VPS (unchanged)      intel-api, ledger, Telegram bot (the only poller), inbox (door 2), warehouse (door 1)
Homelab server       Sun 01:00  mip-weekly                  -> writes /srv/data/mip-weekly/logs/weekly_last_status
                     Sun 12:00  intel-weekly  waits for mip "result=success", then pull -> train -> judge -> package
                                              -> upload to the VPS inbox  (sklearn only, CPU, ~minutes)
                     09:30 (if due)  intel-studio  plan -> write -> render (Remotion/Chromium) -> drafts to dk-publishing
                     09:00      intel-remind  -> owner reminder (new channel, see decision C)
                     databases mip + dk-rag + Ollama are local on the server: no tunnels
Mac                  nothing scheduled. Kept: development, Control Room review pages (until moved), manual fallback
```

## What has to change (found in the code)

| Piece | Today (Mac) | On the server |
|---|---|---|
| "Is it due?" | `data/weekly/*.run.json`, lock `data/weekly/.lock` | same files on `/srv/data/dk-intelligence/data` (copied once from the Mac) |
| Wait for mip | `pgrep` for the Mac's `weekly_refresh.sh` (`weekly.py:38-56`) | read mip's `weekly_last_status`: wait while it is older than this Sunday or `result=failed` and a retry is pending; start on `success` (setting `MIP_STATUS_FILE`; `pgrep` stays as the Mac fallback) |
| Preflight | Tailscale, `lsof` on tunnel ports, `osascript` (`scripts/preflight.sh`) | container preflight: `select 1` on mip and rag directly over Docker networks, VPS reachable over Tailscale |
| mip / rag databases | tunnels 15434 / 15433 | direct, read-only roles `mip_reader` / `rag_builder` |
| mip YAML + wikidata files | `../MarketIntelligence Data Platform` checkout | read-only mount of the mip checkout on the server (`/srv/apps/mip-weekly/repo`) and `/srv/data/mip-weekly` |
| Warehouse (door 1) | code opens `ssh -L` as `intel`, key allowed **only from the Mac's IP** | same code in the container; needs decision A |
| Upload (door 2) | rsync as `intelinbox`, **only from the Mac's IP** | same; GNU rsync is fine; needs decision A |
| intel-api | `INTEL_API_URL` over Tailscale | same (server is on the tailnet) |
| Git stamp | `git rev-parse` | `INTEL_GIT_COMMIT` build arg fallback (same fix as mip) |
| Embedding models | `data/models` (1.2 GB, ONNX on CPU) | same folder on the server, copied once |
| Studio agents | headless `claude -p` with the owner's Claude login | needs decision B |
| Rendering | `dhaka-kacchi-reels` (Node, Remotion, headless Chromium, ffmpeg; 5 GB assets) | separate image with Node + Chromium + ffmpeg; assets copied to `/srv/data`; renders ~1.5-2x slower than the M3 Pro |
| Notifications | `osascript` | decision C |
| Control Room / Review check | Mac web page on 127.0.0.1:8765 | stays on the Mac in phase 1 (it only needs files); later a tailnet page on the server |

Mac-only calls that get a Linux path: `caffeinate`, `osascript`, `open -ga Tailscale`, `lsof`, launchd install checks
(`schedule.py`, `today.py`). On Linux they become no-ops or systemd checks; tests cover both.

## Decisions for the owner

**A. Let the server use doors 1 and 2 (recommended: yes, with new keys).** Today both VPS keys only work from the
Mac's Tailscale address, and the rules say the doors are never widened. Proposal: two **new** keys generated on the
server, with exactly the same restrictions (`intel`: forward to 127.0.0.1:5432 only; `intelinbox`: rrsync -wo
-no-del only), each limited to the **server's** Tailscale address. Same doors, one more trusted machine; the Mac's
keys can be removed later. Needs an ADR (0044).

**B. How the studio agents log in on the server (recommended: B1).**
B1: install Claude Code in the studio image and log in once on the server with your account (token stored in the
data folder, mode 600). Same cost as today. B2: keep the studio's AI steps on the Mac and move only rendering
(defeats the goal). B3: an API key (paid; against ADR 0012 unless you decide otherwise).

**C. Where the 09:00 reminder goes (recommended: C1).** C1: the server posts the reminder text to intel-api, and
the VPS sends it on Telegram (the token stays on the VPS, only the VPS polls). C2: email. C3: drop it.

## Phases (each = code + tests + deploy request part + rollback; Mac agent switched off only after a good run)

1. **IW: Sunday training on the server.** Code: mip-status wait, container preflight, Linux paths, git stamp,
   `deploy/server/` (image with the `train` + `rag` groups, compose, systemd timer Sun 12:00 + hourly catch-up via
   `Persistent=true`). Request: copy `data/` once, secrets as files, new door keys (A), one manual run, compare with
   the Mac's last report, then `launchctl bootout` of `intel.weekly`. Low risk: CPU-only sklearn.
2. **IR: reminder** (small, after C).
3. **IS: Content Studio** (largest): studio image (Node, Chromium, ffmpeg, Claude Code), reels repo + 5 GB assets on
   `/srv/data`, `--if-due` every 30 min via systemd, one supervised run producing real drafts, then `bootout` of
   `studio`. Render time and GPU use measured on the first run.
4. **IC (optional):** Control Room / Review check as a tailnet page on the server; delete the Mac `.command` files.

Order: IW first (needed every Sunday, and the mip move makes the Mac's `pgrep` wait useless), then IR, then IS.
Until IW is live, the Mac's `intel.weekly` must not run against a server mip run: after MW-5, keep the Mac training
but start it by hand once mip's status shows success.

## Delivery
Per phase: code + tests in this repo, ADR 0044 (doors, decision A), results doc with measured numbers, interview
story, and a deploy request in `~/homelab/deploy-requests/` (numbered parts, verification, rollback).
