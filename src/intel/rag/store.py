"""The RAG store in Postgres + pgvector: load a pack, and hybrid search (architecture §4.0, §4.5, §4.6).

Search = dense (exact cosine over the pack's vectors) + lexical (Postgres full-text search, German, English and
simple configs, terms OR-ed so a natural question still matches) → reciprocal rank fusion → identical texts collapsed
→ at most `per_source` hits per place → optional cross-encoder rerank → an evidence threshold ("not found").
Both halves apply the same parameterised filters; the query text is only ever a bind parameter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

SCHEMA = Path(__file__).with_name("schema.sql")
RRF_K = 60
POOL = 50
TS_CONFIG = {"de": "german", "en": "english"}
COLUMNS = (
    "cite", "collection", "source_id", "text", "title", "lang", "posted_at", "platform", "district", "area",
    "listing_id", "listing_name", "rating_norm", "aspects", "suspicious", "dup_count",
)  # fmt: skip


@dataclass
class Filters:
    district: str | None = None
    area: str | None = None
    listing_id: str | None = None
    aspect: str | None = None
    rating_min: float | None = None
    rating_max: float | None = None
    lang: str | None = None
    since: str | None = None
    until: str | None = None

    def where(self) -> tuple[sql.Composable, list[Any]]:
        parts: list[sql.Composable] = []
        args: list[Any] = []
        for col, op, val in (
            ("district", "=", self.district),
            ("area", "=", self.area),
            ("listing_id", "=", self.listing_id),
            ("rating_norm", ">=", self.rating_min),
            ("rating_norm", "<=", self.rating_max),
            ("lang", "=", self.lang),
            ("posted_at", ">=", self.since),
            ("posted_at", "<", self.until),
        ):
            if val is not None:
                parts.append(sql.SQL("{} {} %s").format(sql.Identifier(col), sql.SQL(op)))
                args.append(val)
        if self.aspect is not None:
            parts.append(sql.SQL("%s = any(aspects)"))
            args.append(self.aspect)
        return (sql.SQL(" and ").join(parts) if parts else sql.SQL("true")), args


@dataclass
class Hit:
    cite: str
    score: float
    row: dict[str, Any]
    dense_rank: int | None = None
    lexical_rank: int | None = None
    rerank: float | None = None
    duplicates: int = 1
    extra: dict[str, Any] = field(default_factory=dict)


def ensure_schema(conn: psycopg.Connection[Any]) -> None:
    conn.execute(SCHEMA.read_text())


def _vec(v: np.ndarray[Any, Any]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in np.asarray(v, dtype=np.float32).tolist()) + "]"


def load_pack(
    conn: psycopg.Connection[Any],
    pack_id: str,
    manifest: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    vectors: np.ndarray[Any, Any],
    status: str = "staged",
) -> int:
    """Insert a pack's chunks (idempotent per pack: a re-load replaces it)."""
    import json

    with conn.transaction():
        conn.execute("delete from rag.pack where pack_id = %s", (pack_id,))
        conn.execute(
            "insert into rag.pack (pack_id, manifest, status) values (%s, %s, %s)",
            (pack_id, json.dumps(manifest, default=str), status),
        )
        with conn.cursor() as cur:
            cur.executemany(
                f"""insert into rag.chunk (pack_id, {", ".join(COLUMNS)}, tsv, embedding)
                    values (%s, {", ".join(["%s"] * len(COLUMNS))},
                            setweight(to_tsvector(coalesce(%s, 'simple')::regconfig, coalesce(%s, '')), 'A')
                            || to_tsvector(coalesce(%s, 'simple')::regconfig, %s)
                            || to_tsvector('simple', %s),
                            %s::vector)""",
                [
                    (
                        pack_id,
                        *[r.get(c) for c in COLUMNS],
                        TS_CONFIG.get(str(r.get("lang")), "simple"),
                        r.get("title") or r.get("listing_name"),
                        TS_CONFIG.get(str(r.get("lang")), "simple"),
                        r["text"],
                        r["text"],
                        _vec(vectors[i]),
                    )
                    for i, r in enumerate(rows)
                ],
            )
    return len(rows)


def activate(conn: psycopg.Connection[Any], pack_id: str) -> None:
    with conn.transaction():
        conn.execute("update rag.pack set status = 'retired' where status = 'active'")
        conn.execute("update rag.pack set status = 'active' where pack_id = %s", (pack_id,))


def active_pack(conn: psycopg.Connection[Any]) -> str | None:
    row = conn.execute("select pack_id from rag.pack where status = 'active'").fetchone()
    return None if row is None else str(row[0] if not isinstance(row, dict) else row["pack_id"])


def dense(
    conn: psycopg.Connection[Any], pack_id: str, collection: str, qvec: np.ndarray[Any, Any], f: Filters, k: int = POOL
) -> list[dict[str, Any]]:
    where, args = f.where()
    q = sql.SQL(
        "select {cols}, 1 - (embedding <=> %s::vector) as score from rag.chunk "
        "where pack_id = %s and collection = %s and {where} order by embedding <=> %s::vector limit %s"
    ).format(cols=sql.SQL(", ").join(map(sql.Identifier, COLUMNS)), where=where)
    v = _vec(qvec)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(q, [v, pack_id, collection, *args, v, k]).fetchall()


def lexical(
    conn: psycopg.Connection[Any], pack_id: str, collection: str, query: str, f: Filters, k: int = POOL
) -> list[dict[str, Any]]:
    where, args = f.where()
    # OR the query's lexemes in three configs: a natural-language question should not need every word to match
    tsq = (
        "(to_tsquery('german', replace(plainto_tsquery('german', %s)::text, '&', '|')) || "
        "to_tsquery('english', replace(plainto_tsquery('english', %s)::text, '&', '|')) || "
        "to_tsquery('simple', replace(plainto_tsquery('simple', %s)::text, '&', '|')))"
    )
    q = sql.SQL(
        "select {cols}, ts_rank_cd(tsv, " + tsq + ") as score from rag.chunk "
        "where pack_id = %s and collection = %s and {where} and tsv @@ " + tsq + " order by score desc limit %s"
    ).format(cols=sql.SQL(", ").join(map(sql.Identifier, COLUMNS)), where=where)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(q, [query, query, query, pack_id, collection, *args, query, query, query, k]).fetchall()


def fuse(dense_rows: list[dict[str, Any]], lexical_rows: list[dict[str, Any]], k: int = RRF_K) -> list[Hit]:
    hits: dict[str, Hit] = {}
    for rank, r in enumerate(dense_rows, start=1):
        h = hits.setdefault(r["cite"], Hit(r["cite"], 0.0, r))
        h.score += 1 / (k + rank)
        h.dense_rank = rank
    for rank, r in enumerate(lexical_rows, start=1):
        h = hits.setdefault(r["cite"], Hit(r["cite"], 0.0, r))
        h.score += 1 / (k + rank)
        h.lexical_rank = rank
    return sorted(hits.values(), key=lambda h: -h.score)


def diversify(hits: list[Hit], per_source: int = 2) -> list[Hit]:
    """Collapse identical texts (keeping a count) and keep at most `per_source` hits per place or account."""
    seen_text: dict[str, Hit] = {}
    per: dict[str, int] = {}
    out: list[Hit] = []
    for h in hits:
        norm = " ".join(str(h.row["text"]).casefold().split())
        if norm in seen_text:
            seen_text[norm].duplicates += int(h.row.get("dup_count") or 1)
            continue
        src = str(h.row.get("listing_id") or h.row.get("source_id"))
        if per.get(src, 0) >= per_source:
            continue
        per[src] = per.get(src, 0) + 1
        h.duplicates = int(h.row.get("dup_count") or 1)
        seen_text[norm] = h
        out.append(h)
    return out


Reranker = Callable[[str, list[str]], list[float]]


def search(
    conn: psycopg.Connection[Any],
    pack_id: str,
    collection: str,
    query: str,
    qvec: np.ndarray[Any, Any] | None,
    f: Filters | None = None,
    mode: str = "hybrid",
    reranker: Reranker | None = None,
    limit: int = 10,
    per_source: int = 2,
) -> list[Hit]:
    f = f or Filters()
    d = dense(conn, pack_id, collection, qvec, f) if mode in ("hybrid", "dense") and qvec is not None else []
    lx = lexical(conn, pack_id, collection, query, f) if mode in ("hybrid", "lexical") else []
    hits = diversify(fuse(d, lx), per_source)
    if reranker is not None and hits:
        pool = hits[:POOL]
        scores = reranker(
            query, [h.row.get("title") and f"{h.row['title']}\n{h.row['text']}" or h.row["text"] for h in pool]
        )
        for h, s in zip(pool, scores, strict=True):
            h.rerank = float(s)
        hits = sorted(pool, key=lambda h: -(h.rerank or 0.0))
    return hits[:limit]
