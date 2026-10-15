# Plan: a real login for the review page (publish.dhakakacchi.com/review)

Status: **superseded 2026-10-15**: the owner chose the simpler way (the admin panel's own email and password, no own accounts, no expiry). Built as dk-publishing PR #4 / ADR 0026. The plan below is kept for the two-factor and own-accounts ideas. Code lives in `dk-publishing` (ADR 0025's
page); this plan lives here because that repo is edited only with the owner's go.

## Why
Today the page opens only from a signed 24 h link (ADR 0025). The owner wants to sign in from any device (PC and phone)
without hunting for a link, and to give a second person access with their own account. The 08:00 digest also skips the
link when no agent drafts are waiting for today, so there are mornings with no way in.

## What we build (recommended)
1. **Username + password sign-in** at `/review/login`. One sign-in per device, then a cookie keeps you in. Works the same on
   PC and phone; each device signs in once.
2. **Accounts, no self-sign-up.** Table `publishing.review_users` (additive migration): username, password hash,
   role, active flag, password version, timestamps. Roles: `owner` (approve, reject, manage people) and `reviewer`
   (approve and reject only). The owner adds people on the server: `dk review user add|list|disable|set-password`.
   No admin UI and no email in this round (less to attack, less to maintain).
3. **Passwords:** stored only as hashes (`hashlib.scrypt`, standard library: no new dependency, salted, tuned cost).
   Minimum 12 characters. Never logged, never in the Sheet, never in a URL.
4. **Sessions:** the existing signed `HttpOnly` / `Secure` / `SameSite` cookie, now carrying the user id and the password
   version. Lifetime **30 days sliding** (a phone stays signed in), instantly revoked when the account is disabled or its
   password changes. Every approve/reject already records who decided; it will now record the username.
5. **Brute-force defence:** limit per username and per source address, an even delay for unknown users and wrong
   passwords (no hint which was wrong), lockout of 15 minutes after repeated failures, and a Telegram alert on a lockout.
6. **The signed link stays** as the recovery door (the digest link and `dk review link`), so a forgotten password never
   locks the owner out.
7. **Same checks as today:** every button still ends in `review_drafts`; forms keep their per-session token and
   same-origin check. A reviewer cannot manage accounts.

## Not now (and why)
- **Reusing the admin site's login.** Different stack and host (the ordering admin). Sharing a password or a session
  would tie a public customer system to the page that can publish posts, and a leak in either opens both. Possible
  later as a "Review drafts" button that mints a link from inside the admin; it needs a change in the Connect repo.
- **Two-factor (TOTP) codes.** Recommended as the *next* step, before more people get accounts. It is the one thing that
  protects against a guessed or reused password. Roughly half a day more.
- **Password reset by email, social login, an admin screen.** Not worth the attack surface for a one-to-three person team.

## Files (dk-publishing)
- `migrations/NNNN_review_users.sql` (additive), `domain/` (password policy, roles), `application/use_cases/` (sign in,
  manage users), `adapters/` (scrypt hashing, store), `entrypoints/publish_api/review.py` + `review_pages.py` (login form,
  sign-out, who-am-I line), `entrypoints/cli.py` (`dk review user ...`).
- ADR 0026, `docs/CHANGE-MAP.md` row, `docs/runbooks/review-page.md`, both guides and `llms.txt` (the docs-in-sync tests
  force this), tests (unit for hashing and policy, integration for sign-in, lockout, role limits, session revoke).
- Deploy as usual (`make deploy`), then on the server: `dk review user add <name> --role owner`.

## Verification
`make check` green; real-Postgres integration tests for: wrong password, unknown user (same answer and timing class),
lockout and recovery, disabled user's cookie refused, password change revokes sessions, reviewer cannot add users, a
decision records the username. Then a live check on the phone and the PC, and one failed-login alert in Telegram.

## Decisions for the owner
1. Go, with passwords first and two-factor as the next step (recommended), or both together?
2. Session length: 30 days sliding (recommended for phones) or 12 h as today?
3. Who gets the first extra account, and as `reviewer` (approve/reject only)?
