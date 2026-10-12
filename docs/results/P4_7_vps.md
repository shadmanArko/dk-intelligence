# Phase 4 · P4.7: dk_intel on the VPS (2026-10-12)

**Live on the VPS since 2026-10-12**, after the owner's go-live steps ([docs/runbooks/mcp.md](../runbooks/mcp.md#on-the-vps-adr-0030)).
Decision: [ADR 0030](../adr/0030-dk-intel-on-the-vps.md).

## Measured before deciding
| What | Number | Source |
|---|---|---|
| VPS memory | 7.8 GB total, **5.0 GB available**; all containers ≈2.1 GB | owner's `free -h` + `docker stats`, 2026-10-12 |
| dk_intel, search models warm | **1.7 GB** idle, **2.2 GB** peak (3 clients searching) | HTTP server on the Mac, RSS |
| Model files (embedder + reranker, ONNX int8) | 683 MB + 561 MB | `data/models` |
| One weekly knowledge pack, compressed | **65 MB** | `intel pack ship --dry-run` |
| The intel-mcp image | **267 MB**, no torch, no MLflow; all server modules import | local build of target `mcp` |
| The container without its secret | refuses to start, with the reason | local run |
| The container with a secret | healthy in seconds, uid 10001, 89 MB before models load | local run |

**The choice:** full search on the VPS, with a 2.5 GB limit. That leaves about 2.5 GB free. Keyword-only would save
about 2 GB but loses the "not found" answer and the measured quality.

## What was built
| Part | Where |
|---|---|
| `intel-mcp` image target (only `mcp` + `rag` groups); intel-api's build now names `target: runtime` | `deploy/Dockerfile`, CI |
| CI: build, push, scan (0 fixable HIGH/CRITICAL) the second image | `.github/workflows/ci.yml` |
| compose service behind the `mcp` profile; 2.5 GB limit; loopback 8711 → the tailnet listener; knowledge volume read-only; intel-db 512 MB | `deploy/docker-compose.yml` |
| deploy.sh: pull the same commit's MCP image, check its label, (re)create the rag roles and grants, set their passwords from `.env` (by environment, never in arguments), smoke `/healthz`; a sick intel-mcp is stopped and fails the deploy without rolling back intel-api | `deploy/deploy.sh` (+ 3 tests) |
| Knowledge packages (`pack`, `models`, `activate`): built on the Mac, verified twice and installed by intel-api's inbox worker | `rag/ship.py`, `serve/knowledge.py` |
| The loader in intel-mcp: follows `packs/ACTIVE` into intel-db every 5 minutes, keeps two packs | `rag/lifecycle.sync_to_active` |
| Migration 0007 (additive): schema `rag` + two rag-only roles without passwords | `serve/migrations/0007_rag.sql` |
| Backups without the rag data and without privileges (restorable anywhere) | `deploy/backup.sh`, restore drill test |
| Separate VPS signing secret and tokens (`--host vps`) | `intel mcp init/token` |
| `intel pack ship [--models]`, `intel pack rollback --vps`, Sunday shipping when `INTEL_SHIP_KNOWLEDGE=true` | CLI, `lifecycle.weekly_refresh` |
| Monitoring: scrape job, 3 alerts with promtool tests and runbook sections, Grafana 256 → 384 MB | `deploy/monitoring/` |
| `tailscale serve` 8444 → 127.0.0.1:8711 | `infra/group_vars/all.yml` (owner runs Ansible) |

## Tests
- **`tests/test_vps_knowledge.py`:**
  - a gated pack round-trips and becomes active;
  - refused: ungated, a flipped byte, a forged inner checksum, a path escaping its folder;
  - rollback marker and retention;
  - models install where the server looks;
  - in Postgres: the reader can't read `schema_migrations` or write rag, and the loader can't create tables or read
    `model_package`;
  - migration and Mac schema columns are identical;
  - intake runs once per package, and the loader syncs and stays in sync.
- **`tests/test_deploy_script.py` (+3):**
  - without the profile, nothing about intel-mcp happens;
  - with it, the same commit ships and passwords never appear in arguments;
  - a sick intel-mcp is stopped while intel-api stays.
- **Elsewhere:** promtool tests for the three alerts; the knowledge line, incl. "sent to the VPS"; runbook sections
  for every alert.

Full suite green, coverage 80.9% locally.

## What the owner does (D8 and go-live)
Nine copy-paste steps in the runbook:
1. **Create the VPS's own signing secret on the Mac.**
2. **Ship the models and the current pack once**, about 1.3 GB.
3. **Generate two database passwords on the VPS.**
4. **Paste the VPS secret into a hidden prompt.**
5. **Set the tailnet name and your login, and turn the switch on.**
6. **Run Ansible**, preview first.
7. **Deploy.**
8. **Check memory and the pack load.**
9. **Turn on weekly shipping.**

## Go-live (2026-10-12)
- **Steps 1–8 done by the owner:**
  - the models (1,079 MB) and pack (65 MB) uploaded through the inbox in 17½ minutes and were installed by intel-api;
  - `tailscale serve` 8444 is tailnet only;
  - the deploy reported `intel-mcp … healthy`.
- **One false start:** the Ansible command was pasted into the tailnet-name prompt. It was cancelled with Ctrl+C
  before anything was written; the step only writes after both answers.
- **Found at go-live and fixed the same hour** ([PR #17](https://github.com/shadmanArko/dk-intelligence/pull/17)):
  - **Search ran keyword-only.** The Hugging Face download helper writes lock files next to the model files even
    when nothing needs downloading, and the VPS mounts them read-only. Present files are now used directly; the
    fix was reproduced and verified in the image with a read-only mount. A failed model load is now logged and
    retried after 10 minutes (it used to be silent and permanent).
  - **A degraded `grounding_pack` was cached for 5 minutes** (it was built before the pack had loaded). Degraded
    answers are never cached now.
  - **The loader's first login failed**, because deploy.sh set the role passwords after starting intel-mcp. They
    are set before now.
- **Deploy hiccup:** the release's first CI deploy failed on a reset image pull; a rerun fixed it. Pulls now retry
  ([postmortem](../postmortems/2026-10-12-deploy-pull-reset.md)).

### Measured on the VPS (from the Mac over the tailnet, after warm-up)
| Query | Latency | Evidence |
|---|---|---|
| reviews: "Lieferung kam kalt an" | 3.8 s | strong (0.97) |
| reviews: "too little meat" | 4.1 s | strong (0.99) |
| own posts: "Borhani" | 8.3 s | strong (0.99) |
| creators: "brunch spots" | 2.6 s | strong (0.86) |
| reviews: "sushi in Hamburg" (no answer exists) | 3.6 s | **weak** (0.03), as on the Mac |
| `grounding_pack` (instagram) | 0.5 s | all parts present |
| `get_facts` | 0.5 s | — |

- **Search latency:** median **3.8 s**, about 3× the Mac's 1.3 s (fewer, slower cores). Fine for agents. If it
  matters later, the levers are the rerank pool (20 → 10) and capping caption length for reranking.
- **Memory (after the searches):**
  - intel-mcp **1.71 GB of 2.5 GB**;
  - intel-db 122 MB of 512 MB;
  - intel-api 156 MB (unchanged);
  - the host still has **4.0 GB available**, with 34 MB of swap in use.
- **Isolation, checked over the tailnet:**
  - a writer token sees exactly 7 tools;
  - the owner profile works only with the owner's Tailscale login;
  - **a Mac token is refused by the VPS** (separate keys).
