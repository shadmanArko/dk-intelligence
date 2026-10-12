# 0030. Run dk_intel on the VPS as a separate, opt-in container, fed through the existing doors

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
- Phase 4 built dk_intel on the Mac (ADR 0025–0029). The owner wants it reachable when the Mac is off, for Phase 5
  agents.
- **Measured** (2026-10-12):
  - **The VPS:** 7.8 GB total, 5.0 GB available, all containers about 2.1 GB.
  - **dk_intel with its search models warm:** 1.7 GB idle, 2.2 GB peak under 3 searching clients.
  - **Model files:** 1.2 GB. **A knowledge pack:** 65 MB compressed.
- **Constraints:**
  - the three doors (ADR 0003): the Mac reaches the VPS only through a read-only tunnel, an upload-only inbox and
    the CI deploy;
  - Claude never gets a VPS shell;
  - intel-api's 512 MB and its SLO must not be put at risk;
  - €0.

## Decision
1. **A separate container, `intel-mcp`,** built from the same commit as intel-api but from its own image target
   (`mcp`: only the `mcp` and `rag` groups, 267 MB).
   - It is scanned under the same 0-HIGH/CRITICAL gate.
   - intel-api's image is unchanged: CI now names its target explicitly.
   - Memory limit 2.5 GB (the measured peak plus headroom); intel-db's limit rises from 384 MB to 512 MB.
   - **It holds no Telegram token, no intel-api database access and no inbox mount.** It joins only the
     dk-intelligence network.
2. **Opt-in.** It runs only with `COMPOSE_PROFILES=mcp` in `deploy/.env`: the owner switches it on (D8) and off
   (`docker compose stop intel-mcp`).
3. **Through the existing doors, none widened:**
   - **Code:** the CI deploy (door 3). The forced command is unchanged. `deploy.sh` pulls the MCP image for the
     same commit by tag, checks its commit label, sets the database role passwords from `deploy/.env`, and
     smoke-tests it. A failed MCP check stops intel-mcp and fails the deploy, but never rolls back intel-api.
   - **Data:** packs and models travel through the upload-only inbox (door 2) as `knowledge-*` packages. intel-api's
     inbox worker verifies the outer and inner checksums, refuses unsafe paths, and installs only packs whose Mac
     gate passed, into a volume intel-mcp mounts read-only. A rollback is a tiny marker package.
   - **Reading:** intel-mcp calls intel-api's allowlisted read routes over the compose network, exactly as the Mac
     does.
4. **Database:** migration `0007_rag.sql` (additive) creates schema `rag` and two roles **without passwords**:
   - `intel_mcp_reader`: SELECT on rag, read-only sessions;
   - `intel_mcp_loader`: write on rag tables, no DDL.

   Neither can see anything outside `rag`. deploy.sh (re)creates them and their grants and sets their passwords
   on every deploy, so a restored database heals itself.
5. **Backups** exclude the `rag` data and all privileges (`--exclude-table-data='rag.*' --no-privileges`).
   - The knowledge base is rebuildable from its pack.
   - Third-party review text stays out of the off-site backup.
6. **Tailnet only:**
   - `tailscale serve` on 8444 forwards to `127.0.0.1:8711`, intel-mcp's tailnet listener; it is managed by the
     Ansible playbook.
   - **VPS tokens** are signed with the VPS's own secret (`INTEL_MCP_SECRET_VPS`, `aud=dk_intel@vps`), so a Mac
     token never works there and a VPS token never works on the Mac.
7. **Monitoring:**
   - Prometheus scrapes intel-mcp over the compose network.
   - Alerts: down (only once it has run in the last 7 days), memory above 90% of its limit, bursts of refused
     requests; all tested with promtool.
   - Grafana's limit rises from 256 MB to 384 MB (it ran at 250.7 MB).
8. **The audit chain stays a file** (on intel-mcp's own volume), the same tested code as on the Mac. This is a
   change from the architecture's `mcp_call_log` table: one implementation, no new write path into intel-db for a
   process fed by LLM-driven input.

## Consequences
- **Good:**
  - AI clients and Phase 5 agents work when the Mac is off.
  - The ordering system and intel-api are isolated from intel-mcp's memory and failures.
  - Every new byte arrives through a door that already existed, verified twice.
- **Cost / risk:**
  - 2.5 GB of the VPS's 5 GB headroom; the memory alert and the one-command stop are the safety valves.
  - A second tailnet-reachable service (read-only, JWT-protected).
  - Search runs on fewer cores than the Mac; latency is measured after go-live.
  - The MCP image is pulled by tag (and label-checked), not by a digest named in door 3's request; widening that
    request would mean changing the forced command.

## Alternatives considered
- **Inside intel-api:** the embedder would blow its 512 MB and share its Telegram and registry credentials with a
  process driven by LLM input.
- **Keyword-only search on the VPS (about 300 MB):** loses "not found" and the measured quality; memory allows the
  full stack.
- **Packs over a new channel (rsync to intel-db, or the VPS pulling from the Mac):** a new door. The inbox already
  carries verified packages.
- **Digest-pinning the MCP image through door 3:** needs a new forced-command format; the label check plus a
  commit-named tag gives the same guarantee without touching the door.
