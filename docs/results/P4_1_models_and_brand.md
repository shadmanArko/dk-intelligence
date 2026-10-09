# Phase 4 · P4.1: the first MCP servers, `dk_models` and `dk_brand` (2026-10-09)

Claude Code can now use the production models and the brand rules through `dk_intel`. The committed `.mcp.json`
starts the server; Claude Code asks once to approve it. Decisions:
[ADR 0025](../adr/0025-mcp-gateway-llm-agnostic.md) (gateway) and
[ADR 0026](../adr/0026-mcp-data-exposure-and-audit.md) (exposure and audit).

## What was built
| Part | Where | What it does |
|---|---|---|
| Two read-only intel-api routes | `serve/read_api.py` (PR #6) | `GET /v1/insights/{name}`: one section of an active model, through the field allowlist. `GET /v1/monitoring/latest`: the last persisted monitoring result. Neither ever writes |
| Field allowlist | `exposure.py` | per model and section. Restricted (rule 10) fields never leave intel-api; small order counts become "<5" |
| Capability core | `capabilities/` | scoring, status, five insights tools, brand rules, facts, 19 caption rules; plain typed Python, no MCP, HTTP or SQL |
| Adapters | `adapters/` | intel-api through a 7-route allowlist (refuses anything else before any I/O); the knowledge pack's brand part, checksum-verified |
| Knowledge pack (brand only) | `rag/pack.py`, `intel pack build` | immutable copies of the brand book, voice and rules, facts, playbook and content rules, each pinned by sha256; atomic activation |
| Kit | `mcp_server/kit/` | request ids, hash-chained audit (denials included), profile gate (lists **and** calls), kill switch, rate limit, copy guard |
| Servers and gateway | `mcp_server/` | `dk_models` (7 tools), `dk_brand` (3 tools, 3 resources), one gateway per profile from `profiles.yaml` |
| Clients | `.mcp.json` | stdio via `uv run --directory ${CLAUDE_PROJECT_DIR:-.} intel mcp serve --profile owner` |

## Measured
| | Number |
|---|---|
| Tools exposed | 10 (owner). The two owner-only tools are `score_drafts` and `model_status` |
| Tool list size (tokens, bytes ÷ 3) | owner 4,124 · planner 2,052 · analyst 1,705 · checker 993 · producer 723 · writer 650. The owner's budget is ≤ 6k |
| Stdio start (`uv run`) to tool list | 0.9 s |
| Server memory (RSS) | 137.5 MB, plus 32.6 MB for the `uv` launcher |
| Latency over the tailnet, median / max of 5 | `brand_rules` 1.6 / 2.3 ms · `get_facts` 5.1 / 5.7 ms · `check_drafts` (10 drafts) 5.5 / 67.5 ms · `score_drafts` (10 drafts, production model) 182 / 200 ms · `model_status` (5 upstream calls) 473 / 897 ms |
| First real call (2026-10-09) | 2 Instagram and Facebook drafts scored by `cs-20261008T071453-4ea997` (0.60 vs 0.42, with plain reasons); the TikTok draft was routed to `not_scored` instead of being sent to a scorer that can't read it |
| Tests | 346 passed with Postgres (Docker-gated tests excluded). Coverage 83.83% (floor 80). Strict mypy adds `intel.capabilities.*`, `intel.adapters.*`, `intel.mcp_server.*`, `intel.rag.pack`, `intel.exposure`, `intel.explain` and `intel.serve.read_api` |

## Tests that guard the promises
- **Allowlist:** every allowed route exists in `create_app()`, and the only POST is `/v1/score/batch`. Eleven
  forbidden routes are refused with zero requests sent. No `Authorization` or cookie header goes upstream.
- **Layering:** an AST walk enforces it. The capability core imports no fastmcp, mcp, httpx, psycopg or adapters;
  intel-api imports no MCP code; no `from_fastapi`.
- **Exposure:** no restricted key leaves any section of any model. This is checked on synthetic bundles that
  *contain* every restricted field, and on the real local bundles.
- **Profiles:** writer, checker, producer and analyst see only their tools, and are refused the others, with the
  refusal audited.
- **Audit:** two processes append 300 lines at once, and the chain verifies. Editing one line breaks it at exactly
  that line.
- **Limits:** two scoring slots are shared across processes, and a third caller gets `RATE_LIMITED`. The draft
  budget is per principal and per minute.
- **Brand:** each of the 19 rules has a failing and a passing example. Blocked words are never echoed back. A
  tampered pack fails closed (`BRAND_UNAVAILABLE`).
- **Smoke test:** the exact command in `.mcp.json` starts a stdio server and lists the 10 tools.

## Deviations from the architecture page
- **No `ErrorHandlingMiddleware`.** FastMCP's version turns our `ToolError`s (`[CODE] message. Try: …`) into opaque
  "Internal error" protocol errors, which a model can't act on. Masking stays on (`mask_error_details`).
- **Typed `outputSchema` per tool is deferred.** Tools return JSON objects. Typed output models come with the
  contract snapshots, before P4.5's HTTP clients.
- **Metrics, cache, egress and budget middleware:** as planned, in P4.4–P4.6.
- **Model cards as resources (`dk://models/{name}/card`):** moved to P4.3.
- **`LENGTH_OUT_OF_RANGE` isn't built.** The playbook has no caption-length ranges per format yet. The platform's hard limit
  (`LENGTH_OVER_API`) is checked.

## Found along the way
- Docker Hub started rate-limiting GitHub's shared runners (429). CI now pulls through `mirror.gcr.io` (PR #7).
  The images stay pinned by digest.
- The VPS training history shows the 2026-10-08 run as the latest; the 2026-10-09 run's report isn't on the VPS.
  `model_status` shows exactly what the VPS has.
