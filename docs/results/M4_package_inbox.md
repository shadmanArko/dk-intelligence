# M4 — Model packages and the VPS inbox (2026-10-07)

## Package
`uv run intel package build [--run DIR] [--allow-fail]` → `data/packages/<name>-<version>.tar.gz` + `.sha256`.

- **Contents:** model bundle, `metadata.json`, the judge `report.json`, `model_card.md`, the feature and predict
  source, and `SHA256SUMS`.
- **metadata.json records:** the pull and market ids, the split, the scores, every gate, the code hashes, the
  sklearn/python versions, and the git commit (marked dirty if uncommitted).
- **Failed runs:** packaged only with `--allow-fail`, marked `purpose: pipeline-test`. The server must never offer
  these for approval.
- **`intel package verify`** rejects: a wrong outer or inner checksum, unlisted files, links, absolute paths and
  `..` paths.

## Inbox (door 2), set up on the VPS
- **User:** `intelinbox`, password locked. Its single key is `~/.ssh/dk_intel_inbox` on the Mac.
- **Restrictions:** `command="/usr/bin/rrsync -wo -no-del /opt/dhaka-kacchi/intel-data/inbox",restrict,from="100.107.175.13"`
- **Upload** (`intel package upload FILE`): rsync the archive, then the `.sha256` (the "ready" marker). It then
  runs a `--dry-run --checksum` against the inbox, which lists any file whose remote bytes differ. Empty means
  identical. This was proven by dry-running a modified copy, which was listed.
- **macOS note:** the system `rsync` is openrsync (no `--chmod`); the commands avoid unsupported flags.

## Restriction tests (2026-10-07, all refused as intended)
| Attempt with the inbox key | Result |
|---|---|
| Read a file back | `rrsync error: reading from write-only server is not allowed` |
| Run a command / shell | `rrsync error: SSH_ORIGINAL_COMMAND does not run rsync` |
| Delete (`--delete` with an empty folder) | refused (connection closed by rrsync) |
| Write outside the inbox (`../escape`) | refused |
| Forward to the database port | `administratively prohibited` |
| Package still intact after all attempts | checksum dry run lists nothing |

First package in the inbox: `content_scorer-cs-20261007-33eb38` (pipeline-test, verdict FAIL). It is safe: M5's
server will not offer it for approval.
