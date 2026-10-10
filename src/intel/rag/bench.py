"""The P4.2 retrieval benchmark (Mac only; `uv sync --group bench`). Not served, not in any image.

1. `embed`: embed the review chunks with each candidate model once (cached in data/rag_bench/<model>.npy).
2. `load`: load each model's vectors as its own pack into a Postgres with pgvector (the throwaway test DB).
3. `pool`: run dense, lexical and hybrid search for every golden question and pool the top 20 of every variant,
   shuffled and anonymised, for blind relevance labelling (data/rag_bench/pool/<qid>.json; real text stays local).
4. `score`: recall@10, nDCG@10, MRR per variant on a split, with bootstrap 95% intervals, plus rerankers and the
   "not found" threshold on the adversarial questions.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg

from intel.rag import store
from intel.rag.chunk import Chunk, review_chunks
from intel.rag.embed import MODELS, Embedder, load

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "rag_bench"
GOLDEN = ROOT / "evals" / "rag" / "golden.jsonl"


def snapshot_dir() -> Path:
    market = ROOT / "data" / "market"
    return market / (market / "LATEST").read_text().strip()


def chunks() -> list[Chunk]:
    s = snapshot_dir()
    return review_chunks(
        pd.read_parquet(s / "biryani_reviews.parquet"), pd.read_parquet(s / "biryani_listings.parquet")
    )


def golden(split: str | None = None) -> list[dict[str, Any]]:
    qs = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    return [q for q in qs if split is None or q["split"] == split]


def embed(keys: list[str], cs: list[Chunk]) -> dict[str, dict[str, Any]]:
    BENCH.mkdir(parents=True, exist_ok=True)
    texts = [c.embed_text for c in cs]
    fingerprint = hashlib.sha256("\n".join(c.cite + "|" + c.embed_text for c in cs).encode()).hexdigest()[:12]
    report: dict[str, dict[str, Any]] = {}
    for key in keys:
        path = BENCH / f"{key}-{fingerprint}.npy"
        if path.exists():
            report[key] = {"cached": True, "dim": int(np.load(path, mmap_mode="r").shape[1])}
            continue
        t0 = time.perf_counter()
        emb = load(key)
        vecs = emb.documents(texts)
        np.save(path, vecs)
        report[key] = {
            "seconds": round(time.perf_counter() - t0, 1),
            "dim": emb.dim,
            "per_doc_ms": round(1000 * (time.perf_counter() - t0) / len(texts), 2),
        }
        print(key, report[key], flush=True)
    (BENCH / "fingerprint.txt").write_text(fingerprint)
    return report


def vectors(key: str) -> np.ndarray[Any, Any]:
    fp = (BENCH / "fingerprint.txt").read_text().strip()
    return np.load(BENCH / f"{key}-{fp}.npy")


def load_packs(dsn: str, keys: list[str], cs: list[Chunk]) -> None:
    rows = [c.row() for c in cs]
    for r in rows:
        r["posted_at"] = None if r["posted_at"] is None else r["posted_at"].to_pydatetime()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("create extension if not exists vector")
        store.ensure_schema(conn)
        for key in keys:
            n = store.load_pack(conn, f"bench-{key}", {"model": key}, rows, vectors(key))
            print("loaded", key, n, flush=True)


def _filters(q: dict[str, Any]) -> store.Filters:
    return store.Filters(**q.get("filters", {}))


def run(
    dsn: str,
    keys: list[str],
    modes: tuple[str, ...] = ("dense", "lexical", "hybrid"),
    limit: int = 20,
    reranker: store.Reranker | None = None,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """{variant: {qid: [cites in rank order]}} for every model × mode (lexical once)."""
    out: dict[str, dict[str, list[str]]] = {}
    qs = questions or golden()
    embedders: dict[str, Embedder] = {}
    with psycopg.connect(dsn) as conn:
        for key in keys:
            emb = embedders.setdefault(key, load(key))
            for mode in modes:
                name = "lexical" if mode == "lexical" else f"{key}:{mode}" + (":rerank" if reranker else "")
                if name in out:
                    continue
                out[name] = {}
                for q in qs:
                    qvec = emb.query(q["question"]) if mode != "lexical" else None
                    hits = store.search(
                        conn, f"bench-{key}", q["collection"], q["question"], qvec, _filters(q), mode, reranker, limit
                    )
                    out[name][q["id"]] = [h.cite for h in hits]
    return out


def pool(results: dict[str, dict[str, list[str]]], cs: list[Chunk], depth: int = 20) -> None:
    """Blind pools: the union of every variant's top `depth`, shuffled, with no system or rank shown."""
    by_cite = {c.cite: c for c in cs}
    target = BENCH / "pool"
    target.mkdir(parents=True, exist_ok=True)
    for q in golden():
        cites = sorted({c for run_ in results.values() for c in run_.get(q["id"], [])[:depth]})
        random.Random(q["id"]).shuffle(cites)  # noqa: S311 - a reproducible blind order, not security
        items = [
            {
                "cite": c,
                "place": by_cite[c].listing_name,
                "district": by_cite[c].district,
                "rating": by_cite[c].rating_norm,
                "text": by_cite[c].text,
            }
            for c in cites
        ]
        (target / f"{q['id']}.json").write_text(
            json.dumps({"question": q, "candidates": items}, ensure_ascii=False, indent=1, default=str)
        )


def labels() -> dict[str, dict[str, int]]:
    """qid -> {cite: grade}; grade 2 = clearly answers, 1 = partly, 0 = not relevant (unlisted = 0)."""
    path = BENCH / "labels.jsonl"
    out: dict[str, dict[str, int]] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            out[d["qid"]] = {k: int(v) for k, v in d["grades"].items()}
    return out


def metrics(ranked: list[str], grades: dict[str, int], k: int = 10) -> dict[str, float]:
    relevant = {c for c, g in grades.items() if g > 0}
    top = ranked[:k]
    hits = [c for c in top if c in relevant]
    recall = len(hits) / min(len(relevant), k) if relevant else 0.0
    dcg = sum((2 ** grades.get(c, 0) - 1) / np.log2(i + 2) for i, c in enumerate(top))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2**g - 1) / np.log2(i + 2) for i, g in enumerate(ideal))
    mrr = next((1 / (i + 1) for i, c in enumerate(top) if c in relevant), 0.0)
    return {"recall@10": recall, "ndcg@10": dcg / idcg if idcg else 0.0, "mrr": mrr}


def score(results: dict[str, dict[str, list[str]]], split: str, seed: int = 7) -> pd.DataFrame:
    lab = labels()
    qs = [q for q in golden(split) if q["kind"] == "answerable" and q["id"] in lab]
    rows = []
    rng = np.random.default_rng(seed)
    for variant, per_q in results.items():
        m = pd.DataFrame([metrics(per_q.get(q["id"], []), lab[q["id"]]) for q in qs])
        boot = [m.sample(len(m), replace=True, random_state=int(rng.integers(1 << 31))).mean() for _ in range(1000)]
        b = pd.DataFrame(boot)
        rows.append(
            {
                "variant": variant,
                "n": len(m),
                **{k: round(float(m[k].mean()), 3) for k in m.columns},
                "ndcg_ci": f"{b['ndcg@10'].quantile(0.025):.2f}-{b['ndcg@10'].quantile(0.975):.2f}",
            }
        )
    return pd.DataFrame(rows).sort_values("ndcg@10", ascending=False)


__all__ = ["MODELS", "chunks", "embed", "golden", "load_packs", "pool", "run", "score"]
