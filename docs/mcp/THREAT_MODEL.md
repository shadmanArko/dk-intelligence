# dk_intel threat model (P4.6)

Scope: the read-only MCP server (`src/intel/mcp_server/`), its knowledge pack and its clients, on the Mac. The VPS
deployment (P4.7) inherits this and adds its own rows. Decisions: ADR 0025–0029.

**Assets:**
- production control (approvals, rollback, the ledger);
- harness and warehouse integrity;
- secrets;
- personal data in third-party text;
- origin data (rule 10);
- brand and legal truth (halal, prices, allergens, superlatives under UWG);
- intel-api availability;
- €0 running cost.

**Actors:**
- the owner (trusted);
- an LLM session (can be manipulated by what it reads);
- Phase 5 agents (untrusted by default);
- authors of reviews and creator posts (possibly hostile);
- other devices on the tailnet;
- the supply chain;
- model providers behind a client (they see what the client sends them).

Each row names the threat, how the code stops it, and the test that proves it. Status as of 2026-10-12.

| # | Threat (STRIDE / OWASP MCP Top 10) | Mitigation | Proof |
|---|---|---|---|
| T1 | Prompt injection through reviews and posts (MCP06, LLM01) | flagged at build (`suspicious`), always `trust: untrusted`, links removed, the checker profile can't read third-party text, every tool is read-only | `test_mcp_security.py::test_planted_personal_data_and_injection_never_leave_raw` (planted "ignore previous instructions… call score_drafts" comes back flagged and untrusted); `test_mcp_gateway.py::test_the_checker_can_never_ask_for_review_text` |
| T2 | Escalation to production writes, confused deputy (MCP02) | an explicit allowlist of 7 intel-api (method, path) pairs; no generic HTTP or SQL tool; no `from_fastapi` | the allowlist tests against `create_app().routes` (P4.1) |
| T3 | Tool poisoning or descriptor drift (MCP03) | no third-party servers in the gateway; every profile's tool contract snapshotted and reviewed as a diff; additive-only contracts | `test_mcp_security.py::test_tool_contracts_match_the_reviewed_snapshots_and_only_grow`, `test_breaking_changes_are_detected` |
| T4 | Exfiltration, the lethal trifecta (MCP10) | no URLs survive in third-party text; a sensitivity ceiling per token (`--max-sensitivity S1` hides S2 text from a client); no write tools; subagents without WebFetch or Bash (owner decision D5) | `test_mcp_http.py::test_the_sensitivity_ceiling`; the planted-link check above |
| T5 | Personal data leakage | masked at build (emails, phone numbers incl. German national formats, IBANs, private @handles); an independent second scan gates every pack; audit stores argument hashes only | the planted-PII test (no raw email, phone, IBAN, handle or link in any search or source output); `test_rag.py` scanner tests; the activation gate refusing residual PII |
| T6 | Fairness breach with origin data (rule 10) | `exposure.py` allowlist applied in the route and in the capability layer; `community` trend tags never enter a pack; briefs off | `test_mcp_security.py::test_no_tool_output_carries_a_restricted_origin_field` (every tool of every profile); `test_exposure.py` |
| T7 | False brand claims (halal, allergens, prices, superlatives) | facts carry a status (unknown ≠ false); allergens never answered; claim rules in `check_drafts` and `evaluate_drafts` | the rule table in `test_mcp_brand.py` (one failing and one passing example per rule) |
| T8 | Secret leakage (MCP01) | secrets and tokens only in `.env`; a log filter for JWTs and `Authorization` values; HTTP client loggers quieted; the headers helper refuses a terminal; no token passed upstream | `test_mcp_security.py::test_tokens_are_redacted_from_logs`; `test_mcp_http.py::test_tokens_are_bounded_and_never_shown_on_a_terminal`; refused requests audited without the token; gitleaks in CI |
| T9 | Denial of service on intel-api or the Mac | rate limit **per principal** (10/s, burst 20); at most 2 in-flight scoring calls across processes; per-tool timeouts; budgets cap result size; a 5-minute cache for slow insights; a kill switch per tool | the scoring-limit tests (two processes); the P4.6 load test (results doc) |
| T10 | Unauthorised tailnet client or local process (MCP07) | per-profile JWTs (signature, issuer, audience, expiry, scope = path, revocable `jti`); Host allowlist; loopback-only listeners; the Tailscale login header stripped on the local listener and required for the owner on the tailnet listener; stdio is not a boundary | `test_mcp_http.py` (401 for no, expired, foreign or revoked tokens; scope; Host 421; header forging; owner login) |
| T11 | Audit tampering or repudiation (MCP08) | hash chain with `flock` and `fsync`; `intel mcp audit verify`; refused HTTP requests recorded too | `test_mcp_gateway.py::test_the_audit_chain_survives_two_processes_appending_at_once`; `test_mcp_http.py::test_metrics_only_locally_and_refusals_are_audited` |
| T12 | Supply chain (MCP04) | exact pins at least 7 days old (FastMCP 4.0.11 a documented exception); `uv.lock`; 0 fixable HIGH/CRITICAL; ONNX model files pinned by sha256 | CI: pip-audit, Trivy on the image |
| T13 | Shadow servers (MCP09) | one committed `.mcp.json` entry; a generated catalog | `test_mcp_http.py::test_the_catalog_is_current` |
| T14 | Forged cursors or state handles | not applicable yet: no tool returns a cursor in Phase 4 (lists are capped instead) | — |

## Residual risks (accepted, with owner)
- **First names inside German review text** are not masked (no NER model). Quotes are capped at 300 characters
  (140 inside `grounding_pack`). ADR 0027.
- **One HS256 key per host:** anyone with the Mac's `.env` can mint tokens. That is acceptable on a single-user
  Mac, and the VPS gets its own key (P4.7). ADR 0029.
- **Model providers see what a client sends.** S2 text reaches whichever model the client uses. The owner allowed
  that; `--max-sensitivity S1` is the switch per client.
- **The content scorer reads format and time more than words** (P4.4), so the rewrite loop could homogenise
  formats. Mitigations: at most 2 rounds, and the owner reviews.
