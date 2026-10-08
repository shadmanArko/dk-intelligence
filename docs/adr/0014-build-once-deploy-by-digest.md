# 0014. Build once in CI, deploy by digest, roll back automatically

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
Until H1 the VPS rebuilt the image from source on every deploy (`docker compose build --pull`). Consequences:
- what ran in production was never what CI had tested: a different build, at a different time, with whatever the
  base image tag pointed to that minute;
- nothing scanned the image. The first scan (2026-10-08) of the production image found **6 HIGH vulnerabilities
  with fixes available**: pyarrow 22 (our own dependency, held back by a version cap), urllib3 and msgpack vendored
  inside `pip` (unused at runtime), plus a uv download cache left in the image; the image was 1.63 GB;
- a deploy that started but misbehaved stayed live until someone noticed;
- CI reached the VPS without verifying the host key.

## Decision
1. **Build once.** CI builds the image (multi-stage: no compiler, uv, pip or caches in the runtime stage; base images
   pinned by digest), pushes it to GHCR as `sha-<commit>`, pulls it back **by digest** and scans that digest. The
   VPS only ever pulls; it never builds (except `--local-build`, an emergency path that is logged as such).
2. **Gates before deploy:** gitleaks over the full history; pre-commit hooks (incl. shellcheck and actionlint);
   pip-audit over every locked package with hashes; tests with a coverage floor; trivy: no HIGH/CRITICAL
   vulnerability that has a fix. The full report and a CycloneDX SBOM are kept as artifacts; SLSA provenance and an
   SBOM attestation are stored with the image.
3. **Deploy by digest over door 3.** CI sends `deploy <commit> <digest>`; `deploy.sh` accepts only that exact shape
   (regex), skips a commit older than the checked-out one (superseded), and refuses an image whose
   `org.opencontainers.image.revision` label is not the commit.
4. **Smoke test, then keep or roll back.** Before switching, the running image is tagged `:previous` and its active
   models are recorded. `deploy/smoke.py` runs inside the new container: right commit, every previously active
   model loaded, a real prediction in [0, 1]. On failure the previous image is started and smoke-tested again; the
   deploy exits non-zero, so CI goes red and Telegram is told.
5. **Supply chain:** actions pinned to commit SHAs, tool images to digests, a fixed runner image, least-privilege job
   permissions (`packages: write` only for the image job), `persist-credentials: false`, the VPS host key pinned
   (`INTEL_VPS_KNOWN_HOSTS`, `StrictHostKeyChecking=yes`), Dependabot for Python, actions and base images.
6. **Alerts:** any failure on main → Telegram, from a separate send-only CI bot.

## Consequences
- Good: the scanned bytes are the deployed bytes; a bad release is rolled back in about a minute without a human
  (rehearsed with real Docker: a broken release was detected, rolled back and re-verified); every deploy is logged.
- Good: 6 → 0 fixable HIGH/CRITICAL; image 1.63 GB → 955 MB; known-vulnerable pytest replaced.
- Cost: the VPS needs a read-only registry token (classic PAT, `read:packages` only, expires yearly); GitHub Actions
  minutes for the image build.
- Accepted: Docker keeps that login unencrypted in `~deploy/.docker/config.json` (mode 600). A credential helper needs
  a keyring or GPG agent a headless server does not have; the token can only read images, and `deploy` can already
  run Docker.
- Known limits: 43 HIGH findings without an upstream fix remain in Debian base packages (reported, not gating); a
  distroless base would cut them, at the cost of a shell for debugging. Artifact attestations signed by GitHub need
  a paid plan for private repos; buildx provenance is stored unsigned.

## Alternatives considered
- Keep building on the VPS: untested bytes in production, no scan, CPU spikes on the production box.
- Blue/green with two running containers: twice the memory on a small VPS; the rollback here takes seconds.
- A third-party SSH action: may wrap the command (breaking the strict request check) and did not verify the host key.
