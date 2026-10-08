# Phase 3 · H1 — CI/CD and software supply chain (2026-10-08)

## Pipeline now
```
push to main
 └─ test   gitleaks (full history) · pre-commit (ruff, mypy, shellcheck, actionlint, hygiene, sealed lock)
           · pip-audit (all locked packages, hashes) · pytest + coverage floor
 └─ image  build once (multi-stage, pinned bases) → push ghcr.io/…:sha-<commit> (+ provenance, SBOM attestation)
           → pull back by digest → trivy gate (no fixable HIGH/CRITICAL) → full report + CycloneDX SBOM artifacts
 └─ deploy door 3: "deploy <commit> <digest>" → strict check → pull + verify label → tag :previous
           → start → smoke test in the container → keep, or roll back to :previous and fail
 └─ alert  any failure on main → Telegram (separate send-only CI bot), both repos
```

## Before → after
| | Before | After |
|---|---|---|
| What runs in production | rebuilt on the VPS at deploy time | the exact digest CI built and scanned |
| Fixable HIGH/CRITICAL vulnerabilities in the image | **6** (never scanned) | **0**, gated |
| Image size | 1.63 GB | 955 MB |
| Known-vulnerable Python packages | pytest 8.4.2 (dev), pyarrow 22 (runtime) | none (pip-audit gate) |
| A release that starts but misbehaves | stays live until noticed | detected by the smoke test, rolled back automatically |
| CI → VPS host verification | none | pinned host key, `StrictHostKeyChecking=yes` |
| Action / tool versions | tags (movable) | commit SHAs and image digests; Dependabot proposals |
| Red CI | noticed by looking (4 days in the data platform) | Telegram alert, both repos |
| Shell scripts, workflows | unchecked | shellcheck + actionlint in pre-commit and CI |
| Deploy history | none | `/opt/dhaka-kacchi/logs/intel-deploys.log` |

## Verification
- `tests/test_deploy_script.py` runs the real `deploy.sh` against a real git repo with a recording fake `docker`:
  4 malicious/malformed requests refused (never echoed, docker untouched), superseded commit skipped, wrong-commit
  image refused before switching, good deploy (exact digest, `:previous` tagged first), failed smoke → rollback (the
  failed image never becomes `:current`), manual deploy uses the CI image. Plus `smoke.py` against a real running
  intel-api: right version passes; wrong version and a lost model fail; no answer fails.
- **Rehearsal with real Docker** in a throwaway clone: first deploy OK; then a deliberately broken release (wrong
  version on `/ready`) → `FAIL running version 'broken-on-purpose'` → rollback → previous version re-verified →
  exit 1; history shows `deployed`, `rolled_back`. About 40 s including the build.
- `tests/test_migration_policy.py`: the four existing migrations are additive; destructive SQL is caught (ADR 0015).
- **First pipeline run went red, correctly:** in CI, actionlint used the runner image's own (older) shellcheck and
  flagged an `A && B || C` in the alert step; the Mac had shellcheck 0.11, which does not flag it, so the local hook
  had passed. Root cause: a tool version nobody chose, taken from the runner image. Fix: the actionlint hook now
  installs a pinned shellcheck (0.11.0.1) for both places, and the step uses a plain `if`. The new alert job ran on
  that failure as designed (secrets not set yet, so it only left a notice).
- Findings fixed on the way: `weekly.sh` continued after a failed `cd` (runs without `set -e`); the deploy action
  could have wrapped the command and did not verify the host; the data platform's public README carried the VPS's
  tailnet address.
