# Phase 4 · P4.6: hardening: observability, security tests, load, the live Sunday pack step (2026-10-12)

No new ADR: this implements ADRs 0025–0029. Threat model: [docs/mcp/THREAT_MODEL.md](../mcp/THREAT_MODEL.md).
Runbook: [docs/runbooks/mcp.md](../runbooks/mcp.md). Hand-over: [docs/mcp/PHASE5_HANDOVER.md](../mcp/PHASE5_HANDOVER.md).

## What was built
| Part | Where | What it does |
|---|---|---|
| Metrics | `kit/metrics.py`, `/metrics` (local listener only) | `intel_mcp_tool_calls_total{tool,profile,status}`, latency and result-size histograms, `intel_mcp_auth_failures_total{reason}`, `intel_mcp_cache_hits_total{tool}`; own registry |
| Audit stats | `intel mcp audit stats` | calls, errors, denials, p50/p95 and mean result tokens per tool, from the hash chain (which now records result size) |
| Refused requests in the audit | `RefusalAudit` (ASGI) | 401/403/421 never reach the MCP middleware; now recorded (reason, listener, never the token) and counted |
| Token-safe logs | `kit/log_redact.py` | JWT-shaped strings and `Authorization` values become `[redacted]` on FastMCP, uvicorn and MCP loggers; HTTP client loggers quieted |
| Cache | `ResultCache` | 5 minutes for insights, brand, trends and `grounding_pack`; keyed by tool, arguments, token ceiling and active pack; never errors or searches; inside the profile gate |
| Rate limit per principal | gateway | the load test found FastMCP's default bucket is **global**: 10 agents' handshakes refused each other. Now one bucket per token (subject + jti) |
| Search under load | `pg_index.py`, `rerank.py` | at most 2 model runs at once (each already uses every core); rerank batches sorted by length (padding cost) with order restored |
| Warm models | `PgEvidenceIndex.warm` | the shared HTTP process loads embedder and reranker at start; stdio stays lazy (one process per editor session, ~1.2 GB each if eager) |
| Contract snapshots | `tests/mcp/snapshots/*.json`, `contract_changes.yaml` | every profile's tool contract, reviewed as a diff; breaking changes (removed tool or argument, newly required argument) fail unless declared |
| Security tests | `tests/test_mcp_security.py` (+ HTTP) | planted personal data and injection through the real build into search and sources; restricted origin fields absent from every tool of every profile; tokens redacted from logs; description and instruction limits; cache rules; refusals audited; `/metrics` never on the tailnet listener |
| Retrieval ratchet in CI | `tests/test_rag_eval_ci.py`, `evals/baselines.json` | the real search code on a synthetic corpus must keep hit@5, MRR and precision@3 at their baseline (1.0); milestone numbers recorded in the same file |
| Sunday report | VPS `RunReport.knowledge`, `knowledge_line` | the Sunday Telegram message and the Monday report say whether the new knowledge pack went live, was refused by its gate (and which questions it lost), was skipped or failed |
| Docs | threat model (T1–T14 → mitigation → test), runbook, Phase 5 hand-over with decisions D5, D9–D11 | |

## Measured
### The first live Sunday knowledge step (run `20261011T100005Z`, Sun 11 Oct)
The scheduled Sunday run on the Mac ran the new step by itself:
- **Run status:** pass.
- **The pack:** `kp-20261011T100222-6225c4`, 16,289 chunks. Our own posts grew from 459 to 471 (the week's new
  posts); only **11 new texts** needed embedding (the cache).
- **The gate:** **passed**, with 0 dev questions lost and a hit rate of 1.00 for both packs. The pack went live.
- **The gap:** the VPS ignored the `knowledge` field then (unknown keys are dropped); from this release the Sunday
  message shows it. This replaced the planned "dry run by hand": the real run did it.

### Load test (`intel mcp loadtest`, 10 clients × 3 rounds × 8 tools = 240 calls, real stack, warm models)
| | before fixes | rate limit per principal | + model gate, sorted batches |
|---|---|---|---|
| Completed | **aborted**: "Rate limit exceeded for client: global" during handshakes | 240/240, 0 errors | 240/240, 0 errors |
| Wall time / throughput | — | 94 s / 2.55 calls/s | **74 s / 3.24 calls/s (+27%)** |
| `search_posts` p50 / p95 | — | 24.3 s / 25.2 s | **13.2 s / 17.0 s** |
| `search_reviews` p50 / p95 | — | 7.0 s / 8.0 s | 8.6 s / 15.4 s (fair queueing) |
| `evaluate_drafts` p50 / p95 | — | 210 ms / 524 ms | 140 ms / 517 ms |
| cached tools (facts, brand, trends, complaints, occasions) p50 | — | 7–16 ms | 6–15 ms; ~90% cache hits |

**Capacity, plainly:** a search costs about 1.3 s of the Mac's whole CPU (embed + rerank 20 passages). One or two
clients get answers in about 1–2 s. Ten clients searching at once queue up, at about 0.8 searches/s. That's
enough for the owner plus a few Phase 5 agents. More would need the HNSW/GPU options in the runbook's growth table.

### Usage so far (audit stats, real sessions + evals, 2026-10-09 → 10-12)
18 tools used. Search and `grounding_pack` p50 2–10 s in stdio sessions: each new process loads the models on its
first search (a one-time cost, now avoided on HTTP by warming). Errors are almost all invalid arguments from the
tool-use eval's model, which then corrected itself.

### Ratchets
- **Coverage stays at 80.** Local 81.5%, but CI measured 81.0% on the previous release (it runs a slightly
  different set of tests), so a floor of 81 would be too tight to be fair. It rises when CI shows room.
- **Tool contracts only grow** (snapshot test).
- **Retrieval baselines only rise** (`evals/baselines.json`).
- **Budgets only fall** (P4.4).

## Tests
- `tests/test_mcp_security.py`: 7 tests.
- `tests/test_mcp_http.py`: +1 (metrics and refusal audit).
- `tests/test_rag_eval_ci.py`: 2.
- `tests/test_weekly.py`: +1 (the knowledge line).

Full suite green.
