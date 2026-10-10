# 0027. RAG as a judged artifact: knowledge packs, collections, hybrid search with a reranker, measured model choice

- **Status:** Proposed (P4.2; accepted at the end of P4.3, when the posts collections land)
- **Date:** 2026-10-10

## Context
- **What the owner asked for (2026-10-10):**
  - the strongest retrieval available, with as little hallucination and misinformation as possible;
  - the same MLOps lifecycle as the seven models: rebuilt on Sundays, measured, shipped to the VPS's database;
  - separate collections: a public brand collection, internal rules, and reviews.
- **Constraints:**
  - €0 and no paid AI APIs (ADR 0012);
  - the three doors (ADR 0003);
  - the VPS has about 7.8 GB, shared with the ordering system, so the query-time model must fit there later;
  - rule 10;
  - third-party review text is untrusted.
- **Corpora:**
  - **reviews:** 13,698 competitor reviews (84.5% German), 13,589 chunks after duplicate collapse;
  - **brand:** the brand book;
  - **rules:** voice and rules.

## Decision
1. **The index is a versioned artifact, like a model.**
   - A knowledge pack (`kp-<UTC>-<sha6>`) is immutable and checksummed. It holds the brand files plus
     `index/chunks.parquet` and `index/vectors.npy`.
   - Its manifest records the snapshot, the embedder, the code versions, the masking counts and a second,
     independent residual-PII scan.
   - Weekly builds embed only new text (a cache keyed by model and text).
2. **Collections.** `brand` (public), `rules` (internal) and `reviews` (untrusted text); `own_posts` and
   `creator_posts` come in P4.3.
   - Searches run inside one collection.
   - Each collection has its own filters and its own access per profile.
3. **Search:**
   1. dense, exact cosine search in pgvector (no approximate index at about 16k rows; the vector column has no
      fixed dimension, so a new model is a new pack, not a migration);
   2. lexical search with Postgres full-text in German, English and simple configurations;
   3. reciprocal rank fusion;
   4. collapse of identical texts, and at most 2 hits per place;
   5. a **cross-encoder reranker** over the top 50;
   6. an **evidence threshold**: below it, the tool says "not found" instead of returning loose matches.
4. **Against hallucination:**
   - a deterministic context header per chunk (place, district, rating, month);
   - a citation id per hit, stable across rebuilds;
   - `verify_citations`: cited sources exist, quotes appear in them, and the reranker scores each claim against its
     source;
   - facts only from `get_facts`;
   - masking at build time: emails, phone numbers (including German national formats), IBANs and private handles
     are masked, and links are removed;
   - injection-shaped text is flagged and labelled untrusted.
5. **The model choice is measured** on a golden set of 20 review questions plus 10 adversarial ones.
   - Relevance is labelled blind: candidates are pooled from every system, shuffled, with system and rank hidden.
   - Choices are made on the dev split; the test split is looked at only at milestones.
   - **The rule: ship the best combination that fits the VPS.** A larger model needs at least +5 nDCG@10 on test
     to bring the owner a choice.
   - **The result:** see `docs/results/P4_2_rag_reviews.md` and the table below, filled in at the end of P4.2.
6. **Serving has no torch.** The embedder and reranker run through onnxruntime and tokenizers (int8), on the Mac
   now and the VPS later. Torch is only in the Mac-only `bench` group.

## Consequences
- **Good:**
  - Answers cite real sources and say "not found" when the evidence is weak. Every pack is measured before it can
    go live.
  - The engine can change model without a schema migration.
  - The same code serves on the Mac and on the VPS.
- **Cost / risk:**
  - Reranking costs CPU time per query, so the serving latency is measured and budgeted.
  - With 10 questions per split, intervals are wide; the numbers are reported with them.
  - LLM-drafted relevance labels need the owner's spot check (10 answers).
  - First names inside German review text are not masked (no NER model): quotes stay short.

## Alternatives considered
- **A hosted vector database or a paid embedding API:** breaks €0 (ADR 0012) and sends review text out.
- **An approximate index (HNSW):** unnecessary at 16k rows, and it can return fewer than k hits under filters.
- **The biggest embedding model regardless of size:** measured, not assumed. Once the reranker runs, the embedding
  model matters far less than its size suggests.
- **Brand docs only in the prompt:** kept as the brand card, but the owner wants a public, searchable brand
  collection too.
