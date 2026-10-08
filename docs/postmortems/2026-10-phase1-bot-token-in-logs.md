# Postmortem: Telegram bot token written to the service logs

- **Date:** early October 2026 (Phase 1, while setting up the Telegram bot; exact day not recorded) · **Impact:** the bot token appeared in intel-api's container logs on the VPS ·
  **Severity:** security near miss (no evidence of misuse) · **Status:** final

## Summary
httpx logs every request URL at INFO level, and Telegram puts the bot token *in* the URL. The first Telegram polling
loop therefore wrote the token into `docker compose logs`, which was then pasted while debugging.

## Root cause
A library default (request logging) combined with an API design that puts a secret in the URL. Nothing in our code
logged the token directly, and no test looked at what libraries log.

## Detection
By reading a log excerpt, not by any check. It should have been caught before deploy.

## Actions
| Action | Type | Status |
|---|---|---|
| Revoke and replace the token (the owner, via BotFather) | mitigate | done |
| httpx / httpcore loggers at WARNING | prevent | done |
| Redaction filter on every log record (pattern of a bot token), kept when logs became JSON (H3) | prevent | done, tested |
| Secret scanning in pre-commit and over the full git history in CI (H0) | detect | done |
