# 0012. Local open models; €0 running cost

- **Status:** Accepted
- **Date:** 2026-10-07 (back-filled from the Phase 2 plan)

## Context
A small business; review texts and customer behaviour should not leave our machines without need.

## Decision
No paid AI APIs in the model pipeline. Text work uses readable dictionaries and local multilingual models; every
service runs on the existing VPS or the Mac.

## Consequences
- Good: €0 running cost; no data sent to third parties; deterministic, explainable decisions (dictionary phrases).
- Cost: lower ceiling on text understanding than large hosted models; revisited in Phase 4 (MCP + RAG).
