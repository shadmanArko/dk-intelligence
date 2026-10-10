"""Embedders: one interface, several backends (architecture §4.0, §4.4).

- `HashEmbedder`: deterministic, no download; for tests and CI.
- `SentenceTransformerEmbedder`: the benchmark backend on the Mac (torch, `bench` group, never in an image).
- `OllamaEmbedder`: large models through the local Ollama server (e.g. qwen3-embedding:8b, quantised).
Serving (P4.2 onward) uses the benchmark's winner through ONNX or Ollama; the choice is recorded in ADR 0027.

Each model has its own query/passage convention (e5 prefixes, Qwen3/Harrier instructions); `MODELS` holds them, so
documents and queries are always encoded the way the model was trained.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

Vectors = npt.NDArray[np.float32]
TASK = "Given a question about restaurant customer reviews or brand rules, retrieve the passages that answer it"


@dataclass(frozen=True)
class ModelSpec:
    name: str  # Hugging Face id or ollama:<tag>
    query_prefix: str = ""
    passage_prefix: str = ""
    params_m: int = 0  # millions, for the size-fits-the-VPS rule
    max_tokens: int = 512


MODELS: dict[str, ModelSpec] = {
    "e5-small": ModelSpec("intfloat/multilingual-e5-small", "query: ", "passage: ", 118),
    "e5-large-instruct": ModelSpec("intfloat/multilingual-e5-large-instruct", f"Instruct: {TASK}\nQuery: ", "", 560),
    "bge-m3": ModelSpec("BAAI/bge-m3", "", "", 568),
    "qwen3-0.6b": ModelSpec("Qwen/Qwen3-Embedding-0.6B", f"Instruct: {TASK}\nQuery:", "", 596),
    "harrier-0.6b": ModelSpec("microsoft/harrier-oss-v1-0.6b", f"Instruct: {TASK}\nQuery: ", "", 596),
    "qwen3-4b": ModelSpec("ollama:qwen3-embedding:4b", f"Instruct: {TASK}\nQuery:", "", 4022),
    "qwen3-8b": ModelSpec("ollama:qwen3-embedding:8b", f"Instruct: {TASK}\nQuery:", "", 7567),
}


class Embedder(Protocol):
    name: str
    dim: int

    def documents(self, texts: list[str]) -> Vectors: ...

    def query(self, text: str) -> Vectors: ...


def _normalise(v: npt.NDArray[Any]) -> Vectors:
    v = np.asarray(v, dtype=np.float32)
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return (v / np.where(norms == 0, 1, norms)).astype(np.float32)


class HashEmbedder:
    """Bag of hashed character trigrams: crude but deterministic, so tests exercise the real search path."""

    name = "hash-256"
    dim = 256

    def _one(self, text: str) -> npt.NDArray[np.float32]:
        v = np.zeros(self.dim, dtype=np.float32)
        t = f"  {text.casefold()}  "
        for i in range(len(t) - 2):
            h = int.from_bytes(hashlib.blake2b(t[i : i + 3].encode(), digest_size=4).digest(), "little")
            v[h % self.dim] += 1.0
        return v

    def documents(self, texts: list[str]) -> Vectors:
        return _normalise(np.stack([self._one(t) for t in texts]) if texts else np.zeros((0, self.dim)))

    def query(self, text: str) -> Vectors:
        return _normalise(self._one(text))


class SentenceTransformerEmbedder:
    def __init__(self, key: str, device: str | None = None, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer  # bench group only

        self.spec = MODELS[key]
        self.name = key
        self.batch_size = batch_size
        self.model = SentenceTransformer(self.spec.name, device=device, trust_remote_code=False)
        self.model.max_seq_length = self.spec.max_tokens
        self.dim = int(self.model.get_sentence_embedding_dimension() or 0)

    def documents(self, texts: list[str]) -> Vectors:
        out = self.model.encode(
            [self.spec.passage_prefix + t for t in texts],
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return _normalise(out)

    def query(self, text: str) -> Vectors:
        out = self.model.encode([self.spec.query_prefix + text], normalize_embeddings=True, convert_to_numpy=True)
        return _normalise(out[0])


class OllamaEmbedder:
    def __init__(self, key: str, url: str = "http://127.0.0.1:11434", batch_size: int = 32) -> None:
        import httpx

        self.spec = MODELS[key]
        self.name = key
        self.tag = self.spec.name.removeprefix("ollama:")
        self.client = httpx.Client(base_url=url, timeout=600)
        self.batch_size = batch_size
        self.dim = int(self._embed(["probe"]).shape[1])

    def _embed(self, inputs: list[str]) -> Vectors:
        r = self.client.post("/api/embed", json={"model": self.tag, "input": inputs, "truncate": True})
        r.raise_for_status()
        return _normalise(np.asarray(r.json()["embeddings"], dtype=np.float32))

    def documents(self, texts: list[str]) -> Vectors:
        parts = [
            self._embed([self.spec.passage_prefix + t for t in texts[i : i + self.batch_size]])
            for i in range(0, len(texts), self.batch_size)
        ]
        return np.concatenate(parts) if parts else np.zeros((0, self.dim), dtype=np.float32)

    def query(self, text: str) -> Vectors:
        return self._embed([self.spec.query_prefix + text])[0]


def load(key: str) -> Embedder:
    if key == "hash":
        return HashEmbedder()
    if MODELS[key].name.startswith("ollama:"):
        return OllamaEmbedder(key)
    return SentenceTransformerEmbedder(key)
