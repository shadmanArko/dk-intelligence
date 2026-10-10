"""The P4.3 posts benchmark (Mac only, like bench.py): `python -m intel.rag.bench_posts pool <pack_id>` runs five
search variants over the post questions and pools their top 10, shuffled, with system and rank hidden, for blind
labelling (data/rag_bench/pool_posts/, local); `score <pack_id>` scores them on DISTINCT captions (cross-posted copies
of one caption count once, as search shows them)."""

import json
import math
import random
import re
import sys
import time
from pathlib import Path

import psycopg

from intel.rag import store
from intel.rag.embed import load
from intel.rag.rerank import load_reranker
from intel.settings import McpSettings

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "rag_bench"
POOL = BENCH / "pool_posts"
RUNS = BENCH / "run_posts.json"
GOLDEN = ROOT / "evals" / "rag" / "golden.jsonl"


def questions():
    qs = [json.loads(x) for x in GOLDEN.read_text().splitlines() if x.strip()]
    return [q for q in qs if q["collection"] in ("own_posts", "creator_posts", "posts")]


def coll(q):
    return ("own_posts", "creator_posts") if q["collection"] == "posts" else q["collection"]


def run_all(pack_id):
    s = McpSettings()
    emb = load(s.intel_rag_embedder)
    rr = load_reranker(s.intel_rag_reranker)

    def reranker(q, docs):
        return rr([(q, d) for d in docs])

    runs = {v: {} for v in ("dense", "lexical", "hybrid", "dense+rerank", "hybrid+rerank")}
    top = {}
    times = []
    rows = {}
    with psycopg.connect(s.intel_rag_admin_dsn, autocommit=True) as conn:
        for q in questions():
            qv = emb.query(q["question"])
            for v in runs:
                mode = v.split("+")[0]
                t0 = time.perf_counter()
                hits = store.search(
                    conn,
                    pack_id,
                    coll(q),
                    q["question"],
                    qv if mode != "lexical" else None,
                    None,
                    mode,
                    reranker if "rerank" in v else None,
                    limit=10,
                    rerank_pool=20,
                )
                if v == "dense+rerank":
                    times.append(time.perf_counter() - t0)
                    top[q["id"]] = hits[0].rerank if hits else None
                runs[v][q["id"]] = [h.cite for h in hits]
                for h in hits:
                    rows[h.cite] = h.row
    return runs, top, times, rows


def pool(pack_id):
    runs, top, times, rows = run_all(pack_id)
    RUNS.write_text(json.dumps({"runs": runs, "top_rerank": top, "ms": [round(t * 1000) for t in times]}, indent=1))
    POOL.mkdir(exist_ok=True)
    for q in questions():
        cites = sorted({c for v in runs.values() for c in v[q["id"]]})
        random.Random(q["id"]).shuffle(cites)  # noqa: S311 - a reproducible shuffle, not security
        items = [
            {
                "cite": c,
                "account": rows[c].get("account"),
                "platform": rows[c].get("platform"),
                "text": rows[c]["text"][:400],
            }
            for c in cites
        ]
        (POOL / f"{q['id']}.json").write_text(
            json.dumps({"q": q["question"], "items": items}, ensure_ascii=False, indent=1)
        )
        print(q["id"], len(items))


def _key(text):
    return re.sub(r"[@#]\S+|dhaka ?kacchi|[^a-zäöüß]", "", " ".join(str(text).casefold().split()))[:120]


def ndcg(ranked, grades, k=10):
    dcg = sum((2 ** grades.get(c, 0) - 1) / math.log2(i + 2) for i, c in enumerate(ranked[:k]))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def score(pack_id):
    import pandas as pd

    chunks = pd.read_parquet(ROOT / "data" / "knowledge" / pack_id / "index" / "chunks.parquet")
    group = {c: _key(t) for c, t in zip(chunks.cite, chunks.text, strict=True)}
    data = json.loads(RUNS.read_text())
    qrels = {}
    for line in (ROOT / "evals" / "rag" / "qrels.jsonl").read_text().splitlines():
        r = json.loads(line) if line.strip() else None
        if r and r["qid"] in {q["id"] for q in questions()}:
            g = {}
            for c, v in r["grades"].items():
                if v >= 1:
                    g[group.get(c, c)] = max(g.get(group.get(c, c), 0), v)
            qrels[r["qid"]] = g

    def dedup(run):
        out = []
        for c in run:
            if group.get(c, c) not in out:
                out.append(group.get(c, c))
        return out

    qs = questions()
    for split in ("dev", "test", "all"):
        sel = [q for q in qs if q["kind"] == "answerable" and (split == "all" or q["split"] == split)]
        print(f"== {split} (n={len(sel)})")
        for v, run in data["runs"].items():
            d = {q["id"]: dedup(run[q["id"]]) for q in sel}
            rec = [len(set(d[q["id"]][:10]) & set(qrels[q["id"]])) / min(10, len(qrels[q["id"]])) for q in sel]
            nd = [ndcg(d[q["id"]], qrels[q["id"]]) for q in sel]
            print(f"  {v:14s} nDCG@10 {sum(nd) / len(nd):.3f}  recall@10 (capped) {sum(rec) / len(rec):.3f}")
    print("top rerank:", {k: (round(v, 4) if v is not None else None) for k, v in data["top_rerank"].items()})
    ms = sorted(data["ms"])
    print("dense+rerank ms: median", ms[len(ms) // 2], "max", ms[-1])


if __name__ == "__main__":
    {"pool": pool, "score": score}[sys.argv[1]](sys.argv[2])
