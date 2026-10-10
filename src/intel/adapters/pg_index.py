"""PgEvidenceIndex: the active knowledge pack in Postgres + pgvector, read as `rag_reader` (SELECT only).

The embedder and reranker load lazily on the first search. If the embedder can't load (memory, missing files), search
falls back to keyword-only and says so (`mode: lexical`). Every session is read-only with a 2 s statement timeout.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.rows import dict_row

from intel.capabilities.evidence import EvidenceIndex, EvidenceQuery, EvidenceResult
from intel.capabilities.types import ToolProblem
from intel.rag import store
from intel.rag.embed import Embedder

Rerank = Callable[[list[tuple[str, str]]], list[float]]


class PgEvidenceIndex(EvidenceIndex):
    def __init__(
        self,
        dsn: str,
        embedder: Callable[[], Embedder] | None,
        reranker: Callable[[], Rerank] | None = None,
        rerank_pool: int = store.POOL,
        mode: str = "dense",
    ) -> None:
        self.mode = mode
        self.dsn = dsn
        self.rerank_pool = rerank_pool
        self._embedder_factory, self._reranker_factory = embedder, reranker
        self._embedder: Embedder | None = None
        self._reranker: Rerank | None = None
        self._embedder_failed = False
        self._reranker_failed = False
        self._lock = threading.Lock()

    def _connect(self) -> psycopg.Connection[Any]:
        try:
            conn = psycopg.connect(self.dsn, connect_timeout=3, autocommit=True)
        except psycopg.OperationalError as e:
            raise ToolProblem(
                "UPSTREAM_UNAVAILABLE", "the knowledge database isn't reachable", "uv run intel pack init --start"
            ) from e
        conn.execute("set default_transaction_read_only = on")
        conn.execute("set statement_timeout = '5s'")  # 36 ms warm; a cold first query on a busy Mac needs headroom
        return conn

    def _emb(self) -> Embedder | None:
        with self._lock:
            if self._embedder is None and not self._embedder_failed and self._embedder_factory is not None:
                try:
                    self._embedder = self._embedder_factory()
                except Exception:  # noqa: BLE001 - degrade to keyword search, reported as mode=lexical
                    self._embedder_failed = True
            return self._embedder

    def _rr(self) -> Rerank | None:
        with self._lock:
            if self._reranker is None and not self._reranker_failed and self._reranker_factory is not None:
                try:
                    self._reranker = self._reranker_factory()
                except Exception:  # noqa: BLE001 - degrade to unranked hits, reported as evidence=unscored
                    self._reranker_failed = True
            return self._reranker

    def _pack(self, conn: psycopg.Connection[Any]) -> str:
        pack_id = store.active_pack(conn)
        if pack_id is None:
            raise ToolProblem("UPSTREAM_UNAVAILABLE", "no active knowledge pack", "uv run intel pack build")
        return pack_id

    def search(self, q: EvidenceQuery) -> EvidenceResult:
        emb = self._emb()
        rr = self._rr()
        filters = store.Filters(
            district=q.district, area=q.area, aspect=q.aspect, rating_min=q.rating_min, rating_max=q.rating_max,
            lang=q.lang, since=q.since, until=q.until, platform=q.platform, content_type=q.content_type,
        )  # fmt: skip
        with self._connect() as conn:
            pack_id = self._pack(conn)
            qvec = emb.query(q.query) if emb is not None else None
            mode = self.mode if qvec is not None else "lexical"
            reranker = (lambda query, docs: rr([(query, d) for d in docs])) if rr is not None else None
            hits = store.search(
                conn,
                pack_id,
                q.collection,
                q.query,
                qvec,
                filters,
                mode,
                reranker,
                q.limit,
                rerank_pool=self.rerank_pool,
            )
        rows = [{**h.row, "score": h.score, "rerank": h.rerank, "duplicates": h.duplicates} for h in hits]
        return EvidenceResult(pack_id=pack_id, mode=mode, hits=rows)

    def sources(self, cites: list[str]) -> tuple[str, list[dict[str, Any]]]:
        with self._connect() as conn:
            pack_id = self._pack(conn)
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    f"select {', '.join(store.COLUMNS)} from rag.chunk where pack_id = %s and cite = any(%s)",
                    (pack_id, cites),
                ).fetchall()
        return pack_id, rows

    def own_posts(self, aspects: list[str]) -> list[dict[str, Any]]:
        with self._connect() as conn:
            pack_id = self._pack(conn)
            with conn.cursor(row_factory=dict_row) as cur:
                return cur.execute(
                    "select cite, text, aspects, metrics from rag.chunk "
                    "where pack_id = %s and collection = 'own_posts' and aspects && %s::text[]",
                    (pack_id, aspects),
                ).fetchall()

    def support(self, pairs: list[tuple[str, str]]) -> list[float] | None:
        rr = self._rr()
        return None if rr is None else rr(pairs)
