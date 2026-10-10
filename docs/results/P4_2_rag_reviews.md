# Phase 4 · P4.2: the RAG engine and cited review search (2026-10-10)

Claude Code (and later any agent) can now search 13,589 competitor reviews, the brand book and our rules, and get
back cited evidence or "not found". Decision: [ADR 0027](../adr/0027-knowledge-pack-and-rag-engine.md).

## What was built
| Part | Where | What it does |
|---|---|---|
| Cleaning | `rag/clean.py` | masks emails, phone numbers (including German national formats), IBANs and private @handles; removes links; flags injection-shaped text; a second, independent PII scan |
| Chunking | `rag/chunk.py` | 1 review = 1 chunk, with a context header (place, district, rating, month); documents split by heading; stable citation ids (`R-…`, `B-…`, `U-…`); identical texts at one place collapsed |
| Collections | one engine | `brand` (public), `rules` (internal), `reviews` (untrusted text); `own_posts` and `creator_posts` follow in P4.3 |
| Knowledge pack | `rag/pack.py`, `rag/build.py` | an immutable, checksummed pack holds the brand files, chunks and vectors; the manifest records counts, masks, embedder and code versions; an embedding cache makes weekly rebuilds embed only new text |
| Store and search | `rag/store.py`, `rag/schema.sql` | Postgres + pgvector (exact scan, no fixed vector dimension); dense or hybrid (+ German/English/simple full-text, RRF); duplicates collapsed; at most 2 hits per place; cross-encoder rerank; evidence levels |
| Serving models | `rag/embed.py`, `rag/rerank.py` | ONNX int8 through onnxruntime; no torch at serving time. Models are stored as real files under `data/models/` (the artifact that later ships to the VPS) |
| Tools (`dk_evidence`) | `mcp_server/servers/evidence.py` | `search_reviews`, `search_brand` (brand or rules), `get_sources`, `verify_citations`, and the prompt `cited_answer`. The checker's `get_sources` has `include_text` forced off |
| Commands | `intel pack init / build --index / load` | the local knowledge database (pgvector) with a read-only and a builder role; DSNs written to `.env`, values never printed |
| Benchmark | `rag/bench.py` (Mac only, `bench` group) | 7 embedding models × dense/hybrid × 2 rerankers × rerank depths; blind pooled labels; bootstrap intervals; threshold calibration |

## The golden set
- **Questions:** 20 review questions (German and English, some with district or rating filters), plus 10
  adversarial ones:
  - no answer exists, or one shouldn't be given: sushi, our own brand, reviewers' phone numbers, an injection asking
    for names, allergens, our prices, wifi or parking, crêpes, addresses;
  - one adversarial question turned out to be answerable: a single review praises fresh sushi. It was relabelled
    as answerable, because the corpus is the truth.
- **Split:** dev 15 and test 15, by question id, fixed before any results.
- **Labels:** **2,626 question–review pairs judged blind.**
  - Candidates were pooled from every system's top 20, shuffled, with system and rank hidden.
  - Grades: 2 = answers, 1 = partly, 0 = not. Consistent rules across six rounds.
  - Every system's top 10 is fully judged, so no unjudged hit counts as wrong.
- **Who labelled:** Claude drafted the labels (`by: claude-blind`). The owner spot-checks 10 answers.

## Results (nDCG@10, mean over questions, bootstrap 95% interval in brackets)
| Configuration | dev | test | Query time | Runs on |
|---|---|---|---|---|
| keyword only (Postgres full-text) | 0.38 | 0.23 | ms | anywhere |
| e5-small, dense (118M) | 0.65 | 0.51 | 2 ms (CPU) | anywhere |
| BGE-M3, dense (568M) | 0.81 | 0.74 | 13 ms (CPU, int8) | CPU |
| Harrier-OSS-0.6B, dense | 0.79 | 0.81 | 140 ms (CPU, int8) | CPU |
| e5-small + hybrid + Qwen3-Reranker-0.6B | 0.85 | 0.67 | ~3.6 s (Mac GPU); ~73 s on 4 CPU threads | GPU only |
| Qwen3-Embedding-0.6B + hybrid + Qwen3-Reranker | 0.81 | 0.82 | ~3.6 s (GPU) | GPU only |
| ONNX e5-small + hybrid + bge reranker (top 20) | 0.76 | 0.62 | ~0.9 s (CPU) | CPU |
| BGE-M3 dense + bge reranker (top 20) | 0.84 | 0.76 | ~1.0 s (CPU) | CPU |
| **Harrier-0.6B dense + bge reranker (top 20)**, the serving stack | **0.84** (0.73–0.95) | **0.84** (0.72–0.95) | **~1.1 s (CPU)** | CPU |

Recall@10 for the serving stack: 0.96 on dev and 0.88 on test; MRR is 1.0 on dev and 0.95 on test.

### What we learned
1. **The reranker is the big lever**, more than the size of the embedding model.
2. **Hybrid (adding keyword search) hurt every model.** OR-ing every word of a natural question adds noise that rank
   fusion can't remove. Serving is dense, with keyword search kept as a fallback.
3. **The test split earned its keep.** A configuration chosen on dev alone (e5-small + Qwen3 reranker, 0.85) fell
   to 0.67 on test.
   - Small first-stage models miss reviews that no reranker can recover.
   - The serving stack was chosen **on dev** (Harrier and BGE-M3 tied at 0.84; the tie was broken on dev recall,
     0.96 vs 0.95). Test is reported, not used to choose.
   - The serving stack is the only CPU stack that holds up on both splits (0.84 and 0.84).
4. **Bigger isn't automatically better on our data.**
   - e5-large (560M) did worse than BGE-M3 and Harrier, even with the reranker.
   - The 4B and 8B Qwen models were downloaded but not run to completion. On a CPU their query time alone rules them
     out for the VPS, and the 0.6B stack already sits at the top of the measured range.
5. **The best reranker needs a GPU.** The Qwen3 reranker is excellent, but takes about 73 s per query on 4 CPU
   threads. Serving uses the bge cross-encoder (ONNX int8).
6. **Quantisation is free here.** ONNX int8 matches torch with cosine 0.9999 (Harrier) and 0.99 (BGE-M3).
   e5-small int8 + reranker scored 0.852 vs 0.854 for fp32.

## Against hallucination: measured
- **Evidence levels, calibrated on dev:**
  - **strong** (≥ 0.5): every specific question; no unanswerable question exceeds 0.18;
  - **weak** (0.003–0.5): broad questions ("why the worst ratings?", "what do people love?") score low against any
    single review, so their hits are returned with a flag and advice to search specific topics;
  - **none** (< 0.003): no hits. On dev this catches the personal-data and price questions, and never hides an
    answerable question.
- **A hard "not found" was tried first and rejected.** It would have hidden real answers to broad questions, which
  is itself misinformation.
- **`verify_citations`, end to end:** a supported claim scored 0.97; a false claim citing a real review was marked
  weak (0.0); an invented citation was marked unsupported.
- **Cleaning the real corpus:**
  - masked 1 email, 1 phone number and 4 private handles;
  - 0 residual PII in the independent scan;
  - 0 injection-shaped reviews;
  - 109 duplicate reviews collapsed.

## Bugs found and fixed while measuring
- The first ONNX embedder reused the torch model's name, so the embedding cache would have mixed int8 and fp32
  vectors. ONNX models now carry their own name.
- Parquet NaNs reached Postgres as `''` on load. The loader now converts every missing value to NULL.
- A cold first query on a busy Mac exceeded a 2 s statement timeout. The query takes 36 ms warm; the timeout is now
  5 s.
- onnxruntime 1.30 refuses external weights behind Hugging Face cache symlinks. Serving models are now real files
  under `data/models/`, which is the artifact the VPS will receive.
- A Harrier ONNX check showed that left padding breaks its in-graph pooling (cosine 0.33). It now pads right
  (cosine 0.9999).

## Not done in P4.2 (planned)
- **P4.3:** our posts and creator posts (`search_posts`, `trend_signals`), and the owner's spot check of 10 answers.
- **Labels:** the golden-set labels were drafted by Claude. A larger, fresh question set is needed before the next
  model change, because the test split has now been seen once.
- **VPS sizing:** the Harrier int8 model is about 1 GB on disk. RAM on the VPS is measured in P4.5 before it
  ships there.
