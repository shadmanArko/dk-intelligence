"""The knowledge pack's lifecycle, shared by `intel pack load` and the Sunday run (architecture §4.0):
build (only new text is embedded) → load as staged → gate against the active pack → activate, or keep the old one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from intel.rag import gate, store
from intel.rag import pack as kp
from intel.settings import McpSettings


def load_and_gate(
    s: McpSettings, pid: str, skip_gate: bool = False, activate: bool = True, echo: Callable[[str], None] = print
) -> dict[str, Any]:
    """Load pack `pid` as staged; unless skipped, gate it; activate it (database and files) only if it passes.
    Returns {pack, chunks, gate (or None), active (bool)}."""
    import numpy as np
    import pandas as pd
    import psycopg

    if not s.intel_rag_admin_dsn:
        raise kp.PackError("INTEL_RAG_ADMIN_DSN is not set (run `intel pack init`)")
    manifest = kp.verify(s.pack_dir / pid)
    if manifest.get("kind") != "full":
        raise kp.PackError(f"{pid} has no search index; build with --index")
    rows = pd.read_parquet(s.pack_dir / pid / "index" / "chunks.parquet")
    rows = rows.astype(object).where(rows.notna(), None)  # parquet NaN/NaT -> SQL NULL
    records = rows.to_dict("records")
    for r in records:
        r["aspects"] = list(r["aspects"]) if r.get("aspects") is not None else []
    vectors = np.load(s.pack_dir / pid / "index" / "vectors.npy")
    out: dict[str, Any] = {"pack": pid, "gate": None, "active": False}
    with psycopg.connect(s.intel_rag_admin_dsn, autocommit=True) as conn:
        store.ensure_schema(conn)
        out["chunks"] = store.load_pack(
            conn, pid, manifest, [{str(k): v for k, v in r.items()} for r in records], vectors
        )
        echo(f"{pid}: {out['chunks']} chunks loaded (staged)")
        if not activate:
            return out
        if not skip_gate:
            result = run_gate(conn, pid, manifest, s)
            out["gate"] = result.as_dict()
            path = s.pack_dir / "gates" / f"{pid}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out["gate"], indent=2))
            if not result.passed:
                return out
        store.activate(conn, pid)
    kp.activate(s.pack_dir, pid)  # the brand and trends side reads the same pack as search
    out["active"] = True
    return out


def run_gate(conn: Any, pid: str, manifest: dict[str, Any], s: McpSettings) -> gate.GateResult:
    """Search the golden dev questions in both packs with the serving stack (each pack with its own embedder)."""
    from intel.rag.embed import load
    from intel.rag.rerank import load_reranker

    active = store.active_pack(conn)
    if active == pid:
        active = None
    manifests = {pid: manifest}
    if active is not None:
        row = conn.execute("select manifest from rag.pack where pack_id = %s", (active,)).fetchone()
        manifests[active] = row[0]
    embedders: dict[str, Any] = {}
    rr = load_reranker(s.intel_rag_reranker) if s.intel_rag_reranker else None

    def reranker(query: str, docs: list[str]) -> list[float]:
        assert rr is not None
        return rr([(query, d) for d in docs])

    def search(pack_id: str, q: dict[str, Any]) -> list[str]:
        name = manifests[pack_id]["embedder"]["name"]
        emb = embedders.setdefault(name, load(name))
        collection = ("own_posts", "creator_posts") if q["collection"] == "posts" else q["collection"]
        hits = store.search(
            conn, pack_id, collection, q["question"], emb.query(q["question"]), store.Filters(**q.get("filters", {})),
            s.intel_rag_mode, reranker if rr is not None else None, limit=gate.K, rerank_pool=s.intel_rag_rerank_pool,
        )  # fmt: skip
        return [h.cite for h in hits]

    return gate.evaluate(
        search, pid, active, manifest, gate.read_jsonl(gate.GOLDEN), gate.relevant(gate.read_jsonl(gate.QRELS))
    )


def weekly_refresh(pull: Path | None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The Sunday step: build a pack from this week's pull and the latest market snapshot, load, gate, activate.
    Skipped (not failed) when the knowledge database or the embedder isn't configured on this machine."""
    from intel.rag.build import make_indexer
    from intel.rag.embed import load

    s = McpSettings()
    if not s.intel_rag_admin_dsn or not s.intel_rag_embedder:
        return {"status": "skipped", "why": "no knowledge database configured (intel pack init)"}
    indexer = make_indexer(load(s.intel_rag_embedder), s.pack_dir / "cache", pull)
    pid = kp.build_brand_pack(s.path(s.mip_dir), s.data_dir / "market", s.pack_dir, indexer=indexer)
    manifest = kp.verify(s.pack_dir / pid)
    out = load_and_gate(s, pid, echo=log)
    return {
        "status": "active" if out["active"] else "kept_previous",
        "pack": pid,
        "chunks": out.get("chunks"),
        "collections": manifest.get("collections"),
        "newly_embedded": (manifest.get("embedder") or {}).get("newly_embedded"),
        "gate": out["gate"],
    }
