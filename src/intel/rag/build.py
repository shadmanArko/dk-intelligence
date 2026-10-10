"""Build the search index of a knowledge pack: chunks + vectors for the brand, rules and reviews collections.

Weekly rebuilds embed only new or changed text: vectors are cached by sha256(model, embed_text) under
data/knowledge/cache/. The manifest records counts per collection, masking counts, the embedder and the code
versions, and the result of an independent residual-PII scan (a pack with any finding is refused by the gate).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from intel.rag.chunk import CHUNKER_VERSION, Chunk, document_chunks, review_chunks
from intel.rag.clean import CLEANER_VERSION, residual_pii
from intel.rag.embed import Embedder, Vectors
from intel.rag.pack import Indexer

NEVER_READ = ("raw_social_posts", "customer", "orders", "review.parquet")  # test-enforced: never opened


class EmbeddingCache:
    def __init__(self, directory: Path, model: str) -> None:
        self.path = directory / f"{model}.npz"
        self.vectors: dict[str, np.ndarray[Any, Any]] = {}
        if self.path.exists():
            data = np.load(self.path)
            self.vectors = dict(zip(data["keys"].tolist(), data["vectors"], strict=True))

    @staticmethod
    def key(model: str, text: str) -> str:
        return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()

    def embed(self, embedder: Embedder, texts: list[str]) -> tuple[Vectors, int]:
        keys = [self.key(embedder.name, t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self.vectors]
        if missing:
            new = embedder.documents([texts[i] for i in missing])
            for i, v in zip(missing, new, strict=True):
                self.vectors[keys[i]] = v
            self.path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.path, keys=np.array(list(self.vectors)), vectors=np.stack(list(self.vectors.values())))
        return np.stack([self.vectors[k] for k in keys]).astype(np.float32), len(missing)


def collect(snapshot: Path, brand_files: dict[str, Path]) -> list[Chunk]:
    reviews = pd.read_parquet(snapshot / "biryani_reviews.parquet")
    listings = pd.read_parquet(snapshot / "biryani_listings.parquet")
    out = document_chunks("brand", [("brand-book", brand_files["brand-book.md"].read_text())])
    out += document_chunks("rules", [("voice-and-rules", brand_files["voice-and-rules.md"].read_text())])
    out += review_chunks(reviews, listings)
    return out


def make_indexer(embedder: Embedder, cache_dir: Path) -> Indexer:
    def index(target: Path, snapshot: Path, brand_files: dict[str, Path]) -> dict[str, Any]:
        chunks = collect(snapshot, brand_files)
        if len({c.cite for c in chunks}) != len(chunks):
            raise ValueError("citation id collision; refusing to build")
        vectors, embedded = EmbeddingCache(cache_dir, embedder.name).embed(embedder, [c.embed_text for c in chunks])
        rows = pd.DataFrame([c.row() for c in chunks])
        rows.to_parquet(target / "chunks.parquet", index=False)
        np.save(target / "vectors.npy", vectors)
        masks: Counter[str] = Counter()
        for c in chunks:
            masks.update(c.masks)
        residual = Counter(k for c in chunks for k in residual_pii(c.text))
        return {
            "embedder": {"name": embedder.name, "dim": int(vectors.shape[1]), "newly_embedded": embedded},
            "versions": {"cleaner": CLEANER_VERSION, "chunker": CHUNKER_VERSION},
            "collections": dict(Counter(c.collection for c in chunks)),
            "masks": dict(masks),
            "suspicious": sum(c.suspicious for c in chunks),
            "duplicates_collapsed": sum(c.dup_count - 1 for c in chunks),
            "residual_pii": dict(residual),
        }

    return index
