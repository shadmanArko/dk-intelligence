# Phase 4 · P4.3: our posts, creator posts, trends and the activation gate (2026-10-10)

Claude Code (and later any agent) can now search **our own 459 posts** (with how each did against our typical post)
and **2,210 Berlin food creators' TikTok captions**, ask **what lifts engagement** in Berlin market posts, and every
new knowledge pack must pass an **activation gate** before it goes live. Decision: [ADR 0027](../adr/0027-knowledge-pack-and-rag-engine.md)
(accepted with this milestone).

## What was built
| Part | Where | What it does |
|---|---|---|
| Own posts | `rag/chunk.py` `own_post_chunks` | pull `social_post` (459 captions; 4 publishing tests such as "(test post)" dropped) + the **latest** `social_metrics_snapshot` per post. `engagement_rate` = (likes + comments + shares + saves) / reach, only from a reach of 50; `vs_our_median` = that rate over our median on the same platform. Cite `P-…` |
| Creator posts | `rag/chunk.py` `creator_post_chunks` | snapshot `creator_posts` + `creators` (865 public business accounts, TikTok). `like_rate` = likes / views; `vs_account_median` against that creator's own median. Cite `C-…` (account + time; permalinks are mostly missing) |
| Masking | `rag/clean.py` | handles stay only for our account and the 865 public creator accounts; every other `@handle` → `@[user]`. Pack totals: 395 handles, 99 phone numbers, 143 links, 2 emails masked |
| Store | `rag/schema.sql`, `rag/store.py` | 4 **added** columns (`content_type`, `account`, `url`, `metrics jsonb`) and an index; filters by platform and content type; search over several collections at once; at most 2 hits per creator account, our own posts uncapped; **cross-posted captions collapse into one hit** |
| `search_posts` | `capabilities/evidence.py`, `servers/evidence.py` | `source = own \| creators \| all`, platform, content type, dates, `sort = relevance \| performance` (relevant hits only, ordered by performance *relative to the account's own typical post*, so a big account can't win on size). The producer profile has `source` forced to `own` |
| `trend_signals` | same + `adapters/pack.py` `PackTrendSource` | the pack's `tables/trends.parquet` (snapshot `content_feature_performance`, 537 rows). The 101 `community` rows (origin-group labels, some naming a religion) **never enter the pack** (rule 10, D2). Rice-dish tags are Wikidata ids; the pack adds their names from the data platform's Wikidata file (sha256 in the manifest). Every answer carries the caveat: *not a time trend, not our posts, Instagram not collected* |
| Hidden patterns | `capabilities/patterns.py` | "what competitors' customers miss that we do well": joins the weakness miner's negative aspects, our posts about the same aspect that beat our median, and optionally gap areas. `proven` / `untested` / `weak`, each marked "a pattern to test, not a fact". Tested on fixtures; shown to clients in P4.4 (`grounding_pack.opportunities`) |
| Activation gate | `rag/gate.py`, `intel pack load` | a pack is loaded as *staged*, searched with the serving stack on the **dev** questions next to the active pack, and activated only if **at most one** answerable dev question loses its relevant top-10 hit, the residual-PII scan is empty and the pack has an index. The result is saved to `data/knowledge/gates/<pack>.json`; `--skip-gate` exists and says so. A gated load also moves the file-side `ACTIVE`, so brand, trends and search read the same pack |
| Relevance labels in git | `evals/rag/qrels.jsonl` | citation ids and grades only (no text), so the gate and the scores are reproducible; cites are stable across rebuilds |
| Small fixes | `checks.py`, `insights.py` | the superlative rule now catches "Best biryani in Berlin!" and "beste Kacchi Berlins" (it caught only "best in Berlin"), without firing on "best enjoyed hot. Berlin…"; the identical "national day" occasions now read "national day: Republic Day" and have distinct ids (the public holiday name only, never the country) |

## The golden set, extended
- **12 post questions:** 10 answerable (5 own posts, 3 creator posts, 2 both) + 2 that must find nothing (our "sushi
  menu"; posts listing a customer's private phone number). Split dev 6 / test 6, fixed before any result.
- **Labels:** blind as in P4.2. Five variants (dense, lexical, hybrid, dense + rerank, hybrid + rerank), top 10 each,
  pooled and shuffled with system and rank hidden: **340 pooled pairs** in two rounds (the second after the
  duplicate collapse surfaced new posts). I also searched the whole corpus by keyword for each topic and labelled
  what no system had found, so recall isn't flattered by a shallow pool. **203 relevant (110 fully, 93 partly).**

## Results (posts, the serving stack is the P4.2 one: Harrier-0.6B dense + bge reranker, ONNX int8, CPU)
Scored on **distinct captions**: our captions are cross-posted to Facebook, Instagram and Threads, and a list of
three copies of one caption is one answer, not three. `python -m intel.rag.bench_posts score <pack>`.

| Variant | dev nDCG@10 | test nDCG@10 | dev recall@10* | test recall@10* |
|---|---|---|---|---|
| keyword only | 0.33 | 0.26 | 0.39 | 0.28 |
| hybrid (RRF) | 0.47 | 0.46 | 0.53 | 0.46 |
| dense | 0.68 | 0.49 | 0.69 | 0.64 |
| **dense + rerank (serving)** | **0.72** | 0.45 | **0.77** | 0.40 |
| hybrid + rerank | 0.71 | 0.47 | 0.69 | 0.45 |

\* recall of distinct relevant posts, capped at 10 (a question with 24 relevant posts can't show more than 10).
n = 5 per split: one question moves a mean by 0.2, so these intervals are wide.

- **The duplicate collapse was the biggest win.** Before it, the serving stack scored 0.60 (dev) and 0.38 (test), and
  its top 10 held 43 relevant distinct posts over the 10 questions; after it, 0.72 and 0.45 and 57 (+33%). A
  planner asking for examples now gets up to 10 different posts, not the same caption three times.
- **The serving stack stays as it is.** It is best on dev (the rule: choose on dev). On test, dense alone is ahead
  (0.49 vs 0.45), within the noise of 5 questions, and dense alone can't say "not found": the reranker's score is
  what refuses the no-answer questions. Hybrid again doesn't beat dense.
- **"Not found" works on posts with the review calibration:** the two no-answer questions scored 0.0007 and 0.0005
  (floor 0.003), so they return no hits. The narrow question "creators reviewing biryani" (one partly relevant post
  in 2,210) and "posts asking followers for ideas" come back **weak**, not hidden. A short phrasing such as "our sushi
  menu" scores 0.013 and comes back weak with three menu posts and the instruction to check them: by design since
  P4.2 (flag loose matches, never silently drop them).
- **Where it's weak:** "a food festival or event in Berlin" (test, nDCG 0.15): our event posts say *Mela* (the
  Bengali word for a fair), and neither the embedder nor the reranker connects it to "festival". Fix candidates for
  P4.6: a small glossary in the context header (Mela → fair/festival, Borhani → yogurt drink), measured on dev.
- **Latency:** dense + rerank over posts, median 1.27 s, max 3.5 s per query on the Mac's CPU (the reranker reads up
  to 20 captions of up to 1,700 characters). `trend_signals` answers from a cached table in milliseconds.
- **Reviews are unchanged:** same chunks, same cites, same vectors (all from the cache: `newly_embedded` was 2,673
  for the first posts build, then 0).

## The owner's spot check (2026-10-12)
10 answers (6 review questions, 4 post questions), top 5 hits each with Claude's blind label, judged by the owner.
- **Agreement: 38 of 45 labels (84%).** **Trust: 10 of 10 answers** ("would you trust an answer built from these hits":
  yes for all).
- **All 7 disagreements were Claude being stricter than the owner**; the owner never marked a hit Claude called
  relevant as irrelevant:
  - two "partly" hits fully answer the question: a wait of more than an hour, and "quantity was really less";
  - two dry-biryani reviews count for "too little meat";
  - "creators trying biryani" is read more broadly, as creators trying Berlin restaurants (3 hits).
- The owner's labels replace Claude's in `evals/rag/qrels.jsonl` (marked `owner-spotcheck`). Consequence: the
  published scores were conservative. **Posts, serving stack, test nDCG@10: 0.45 → 0.61**, now ahead of dense alone
  (0.55) on test as well as dev. The review numbers weren't re-run (the benchmark packs were throwaway); the four
  relabelled review hits sit in the serving stack's top 5.

## The gate, measured
| Run | Result |
|---|---|
| Real data: the posts pack against the active reviews-only pack | **passed**: 0 dev questions lost; gained the 5 dev post questions; hit rate on answerable dev questions 0.67 → 1.00 |
| Real data: the first posts build | the pack's own second PII scan found 3 items, so **the gate would have refused it**. All three were false positives (two "date / 23 Uhr" event announcements, one fashion code "SF1OG SS26"); the scanner now drops dates first and needs ≥12 digits for an IBAN, and a test keeps real IBANs and phone numbers flagged |
| Postgres test, a deliberately worse pack (the same chunks with shuffled vectors) | **refused** (`tests/test_rag_posts.py::test_the_gate_refuses_a_deliberately_worse_pack`); the good pack stays active |
| Unit test: one dev question lost | passes (tolerance 1); two lost: refused; any test-split question: never looked at |

## Pack `kp-20261010T164245-6225c4` (active on the Mac)
brand 9 · rules 10 · reviews 13,589 · own_posts 459 · creator_posts 2,210 = 16,277 chunks; trends 537 rows; pull
`20261009T130432Z`, snapshot `20261009T072822Z`; residual PII 0; chunker version 2.

## Tests
`tests/test_rag_posts.py` (12): metrics from the latest snapshot and relative to our median, the reach floor, handle
masking with the allowlist, publishing tests dropped, the full pack with posts and trends and no `community` tags,
a pack without trends failing closed, `search_posts` by source and by performance, `trend_signals` filters and
caveat, the store's per-account cap and filters, cross-posted captions collapsing, the gate (unit and in
Postgres), qrels holding cites and grades only, and the pattern join. Plus the superlative and national-day cases.
The whole suite passes with branch coverage 82.3% (floor 80).

## What's next
- **Owner:** spot-check 10 answers (about 40 minutes; the page is ready, see the hand-off message).
- **P4.4:** `dk_studio` (`grounding_pack` with `opportunities` from `patterns.py`, `evaluate_drafts`) and the content
  prompts; the Sunday pack step.
