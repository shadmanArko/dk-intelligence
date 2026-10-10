# 0027. RAG as a judged artifact: knowledge packs, collections, hybrid search with a reranker, measured model choice

- **Status:** Accepted (2026-10-10, end of P4.3; proposed at P4.2)
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
  - **rules:** voice and rules;
  - **own_posts** (P4.3): our 459 captioned posts with their latest metrics;
  - **creator_posts** (P4.3): 2,210 TikTok captions of 865 public Berlin food business accounts;
  - **trends** (P4.3, a table, not vectors): what lifts engagement in Berlin market posts, 537 rows.

## Decision
1. **The index is a versioned artifact, like a model.**
   - A knowledge pack (`kp-<UTC>-<sha6>`) is immutable and checksummed. It holds the brand files plus
     `index/chunks.parquet` and `index/vectors.npy`.
   - Its manifest records the snapshot, the embedder, the code versions, the masking counts and a second,
     independent residual-PII scan.
   - Weekly builds embed only new text (a cache keyed by model and text).
2. **Collections.** `brand` (public), `rules` (internal), `reviews` (untrusted text), `own_posts` (ours) and
   `creator_posts` (untrusted text).
   - Searches run inside one collection, or the two post collections together (`search_posts source=all`).
   - Each collection has its own filters and its own access per profile.
3. **Search:**
   1. dense, exact cosine search in pgvector (no approximate index at about 16k rows; the vector column has no
      fixed dimension, so a new model is a new pack, not a migration);
   2. lexical search with Postgres full-text in German, English and simple configurations;
   3. reciprocal rank fusion;
   4. collapse of identical texts (for posts: of captions cross-posted to several platforms), and at most 2 hits
      per place or creator account (our own posts are not capped);
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
   - **The result (2,626 judged pairs; `docs/results/P4_2_rag_reviews.md`):** the serving stack is
     **Harrier-OSS-v1-0.6B (dense) + bge-reranker-v2-m3 on the top 20, both ONNX int8 on CPU.**
     - nDCG@10 is 0.84 on dev and 0.84 on test; about 1.1 s per query on CPU.
     - Harrier and BGE-M3 tied on dev; dev recall broke the tie.
   - **Qwen3-Reranker-0.6B is better on dev (0.85)** but takes about 73 s per query on a 4-thread CPU, so it needs
     a GPU. Recorded as an owner option, not shipped.
   - **Hybrid search lost to dense for every model.** Serving is dense; lexical remains the fallback.
   - **Evidence levels** (dev calibration): none below 0.003, weak from 0.003 to 0.5, strong from 0.5. A hard
     "not found" threshold was rejected, because it hid real answers to broad questions.
6. **The activation gate (P4.3).** A new pack is loaded as *staged* and activated only if, on the **dev** questions
   and with the serving stack, at most one answerable question loses the relevant top-10 hit it had in the active
   pack, its residual-PII scan is empty and it has an index. Judged per question, not on means (n is small). The
   test split is never used by the gate. Relevance labels (cites and grades, no text) live in git
   (`evals/rag/qrels.jsonl`). One gated command moves both the database's and the files' active pack.
7. **Trends say what they are.** `trend_signals` reports engagement lift by tag in market posts, with the caveat
   that it is not a time trend (the source table has no time dimension). Origin-group (`community`) tags never
   enter a pack (rule 10, D2).
8. **Serving has no torch.** The embedder and reranker run through onnxruntime and tokenizers (int8), on the Mac
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
  - On posts (P4.3) n is 5 per split; dev picked the same serving stack (nDCG@10 0.72), test was 0.45 against
    0.49 for dense alone, within noise. Kept, because the reranker's score is what refuses no-answer questions.
  - Words of our own community ("Mela") aren't linked to their English meaning by the embedder; a glossary in
    the context header is a P4.6 candidate.
  - First names inside German review text are not masked (no NER model): quotes stay short.

## Alternatives considered
- **A hosted vector database or a paid embedding API:** breaks €0 (ADR 0012) and sends review text out.
- **An approximate index (HNSW):** unnecessary at 16k rows, and it can return fewer than k hits under filters.
- **The biggest embedding model regardless of size:** measured, not assumed. Once the reranker runs, the embedding
  model matters far less than its size suggests.
- **A gate on mean scores with a tolerance:** with 10–20 questions, one question moves a mean by 0.05–0.1, so the
  gate would flip on noise. Per-question loss counting is stable and explainable.
- **Trends "rising week over week":** impossible from the source table (no time dimension); reported as what it is.
- **Brand docs only in the prompt:** kept as the brand card, but the owner wants a public, searchable brand
  collection too.
