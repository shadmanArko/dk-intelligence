# Phase 4 · P4.0: spikes and pins (2026-10-09)

Every library assumption P4.1 depends on, turned into a measured fact before any server code depends on it. The
assumptions now live as tests: [`tests/test_fastmcp_assumptions.py`](../../tests/test_fastmcp_assumptions.py), which
runs in CI from this milestone (CI installs the `mcp` group). Decision: [ADR 0025](../adr/0025-mcp-gateway-llm-agnostic.md).

| Assumption (architecture §9.4) | Result | Consequence |
|---|---|---|
| FastMCP 4 mounts servers without a namespace | ✅ 7 tools keep their plain names; prompts and resources come through; read-only annotations survive | gateway design as planned |
| Duplicate names across mounted servers | ⚠️ **collapse silently**: two servers both defining `same` list one tool, no error | the name-uniqueness test in P4.1 is mandatory |
| Gate pattern: filter lists and refuse calls | ✅ `on_list_*` filters tools, prompts and resources; `on_call_tool`, `on_get_prompt` and `on_read_resource` refuse hidden ones | `profile_gate` as planned |
| Forced arguments | ✅ a gateway `ToolTransform` with `ArgTransformConfig(hide=True, default=False)`: the argument vanishes from the schema, the tool gets `False`, and a client sending it is rejected | the checker can't ask for review text |
| Claude Code's working directory for stdio servers | not promised by Claude Code's docs; Claude Code sets `CLAUDE_PROJECT_DIR`, and `.mcp.json` expands `${CLAUDE_PROJECT_DIR:-.}` | `.mcp.json` uses `uv run --directory ${CLAUDE_PROJECT_DIR:-.}`; settings resolve `.env` from that variable or the package location |
| 64-character client tool names | longest planned name, `mcp__dk_intel_http__competitor_complaints`, has 41 characters | no renames |
| `httpx` and `httpx2` side by side | ✅ one lock: `httpx` 0.28.1 (intel-api, unchanged) and `httpx2` 2.13.1 (FastMCP 4). The lock change **only adds** 43 packages; no existing version moved | the intel-api image is unchanged (`--no-default-groups`) |
| Known vulnerabilities | `pip-audit` over every locked group, FastMCP's included: **none** | the 0-HIGH/CRITICAL ratchet holds |
| Memory | 94.7 MB peak RSS after importing FastMCP; 108.2 MB after a full gateway session (4 servers, 7 tools, in-process client) | matches the ≈100 MB idle estimate; the embedder (P4.2) is the open number |
| `tools/list` size | 3,399 bytes for 7 minimal tools (≈1.1k tokens *est.*, bytes ÷ 3) | the real descriptions and schemas get their budgets in P4.1 |

**Pins:** `fastmcp==4.0.11` (released 2026-10-04) brings `mcp` 2.3.0.
- **Why not the 7-day rule:** that rule would have picked 4.0.10. 4.0.11 fixes how transforms, visibility and auth
  apply to hashed tool lookups, which is what the profile gate and forced arguments rest on.
- **Why not 4.1.0** (2026-10-08): a minor release one day old, with breaking changes. It's reviewed from Thu 10-15.

**Moved to P4.5:** the spikes only the HTTP path needs: `cache_ttl` / `cache_scope`, `JWTVerifier` HS256, and
`langchain.mcp.MCPAdapter`.

**Tests:** 161 passed, 68 skipped, plus 5 new assumption tests. Docker-gated tests not run: Docker Desktop was off.
`tests/test_infra.py` checks for the `docker` binary, not a running daemon, so it errors instead of skipping when
the daemon is down. This is pre-existing; noted for a small fix.
