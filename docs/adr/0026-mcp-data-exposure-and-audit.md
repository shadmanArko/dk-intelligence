# 0026. What MCP clients may see: data classes, a field allowlist at the source, and a hash-chained call audit

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
- **What changes:** from Phase 4, AI clients read our model outputs through the `dk_intel` MCP gateway (ADR 0025).
  Some of those clients send what they read to a model provider: Claude today, Groq later (D3).
- **The owner's decision (2026-10-09):** clients may see the data that lives on the VPS. The default safeguard:
  private people's names and handles are withheld.
- **Rule 10 still holds:**
  - origin and citizenship statistics are owner-reviewed exceptions, for the community map (area level) and the
    occasion planner (city level);
  - they may not be reused anywhere else without asking.
- **What the bundles hold (2026-10-09):**
  - the community map's bundle carries per-area origin counts, an origin-weighted `audience` and order locations;
  - the occasion planner's picks carry the community and the number of people who celebrate;
  - the creator matcher holds personal names.
- **What existed before:** intel-api had no JSON route for insights; the HTML pages render whole bundles.

## Decision
1. **Four data classes**, labelled on every tool (`_meta` `dk/sensitivity`, `dk/trust`):

   | Class | What | Leaves through MCP? |
   |---|---|---|
   | S0 public | brand book, confirmed facts, platform rules | yes |
   | S1 internal | scores, insight aggregates, our posts, voice and rules | yes |
   | S2 third-party text | short review and caption excerpts, marked `trust: untrusted` | yes, capped at 140 characters |
   | S3 restricted | origin and citizenship counts and weights, `audience`, order locations, personal names and followers, logins, secrets | **never** |

2. **The allowlist sits at the source.**
   - `intel.exposure` lists, per model and per section, the fields that may leave. Anything not listed never
     leaves.
   - intel-api's read-only route `GET /v1/insights/{name}` applies it, so S3 fields never cross the tailnet.
   - The MCP capability layer checks again (`leaked_keys`) and refuses an answer that carries a restricted key.
   - Funnel order counts from 1 to 4 become `"<5"`.
3. **Area scores are shown with a method note.** The community map's `score` and `gap` are area-level but derived
   from origin statistics. They are shown with the note "choose areas, never people" (D2). `audience` and the
   origin columns never are.
4. **Every call is audited.**
   - One JSON line per tool call, resource read and prompt get: principal, profile, tool, a hash of the arguments
     (never the arguments themselves), status (ok / error / denied), and duration.
   - Each line is hashed over the previous one.
   - Appends take an exclusive file lock and fsync, because every Claude Code session runs its own stdio server.
   - `intel mcp audit verify` recomputes the chain.
   - The intel-api actor (Telegram or Tailscale login) is never passed on: it becomes `actor_role`.
5. **Copy guard.** Blocked words in *our* copy fields (occasion labels, greetings, hashtags) are withheld with a
   loud warning and an audit line.
   - Third-party quotes are evidence: they are never censored, but they are always marked untrusted.

## Consequences
- **Good:**
  - A rule-10 leak needs two independent mistakes: one in the route and one in the capability layer.
  - Tests prove both against synthetic bundles that contain every restricted field, and against the real local
    bundles on the Mac.
  - Every answer can be traced to a caller and a time without storing what was asked.
- **Cost / risk:**
  - An allowlist must be extended whenever a model gains a useful field: an added field stays hidden until someone
    lists it.
  - The audit file grows by one line per call. Retention is set when H10's policy file lands.
  - Stdio profiles are least privilege, not a security boundary (ADR 0025). Real enforcement over HTTP comes with
    signed tokens in P4.5.

## Alternatives considered
- **A denylist** (remove the known bad fields): a new bundle field would leak by default.
- **Filtering only in the MCP server:** S3 fields would still cross the tailnet, and any other client of intel-api
  would get them.
- **Logging full arguments:** drafts and queries could contain personal data, and the audit's job is
  accountability, not content.
- **The intel-db `audit_log`:** the Mac has no write path to intel-db (three doors). The VPS server gets an
  append-only table in P4.7.
