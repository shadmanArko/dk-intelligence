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
    """`onnx:<key>` is the serving backend (no torch); a plain key is the benchmark backend."""
    if key in ("hash", HashEmbedder.name):  # a pack records the name, "hash-256"
        return HashEmbedder()
    if key.startswith("onnx:"):
        return OnnxEmbedder(key.removeprefix("onnx:"))
    if MODELS[key].name.startswith("ollama:"):
        return OllamaEmbedder(key)
    return SentenceTransformerEmbedder(key)


def model_dir(repo: str) -> str:
    """Serving models live as real files under data/models/<repo> (the artifact that later ships to the VPS),
    not as cache symlinks: onnxruntime refuses external weights whose resolved path leaves the model folder."""
    from intel.settings import McpSettings

    return str(McpSettings().data_dir / "models" / repo.replace("/", "--"))


def _hf_file(repo: str, filename: str) -> str:
    from huggingface_hub import hf_hub_download

    return str(hf_hub_download(repo, filename, local_dir=model_dir(repo)))


class OnnxEmbedder:
    """The serving backend: onnxruntime + tokenizers, no torch, int8. Pooling as the model was trained:
    mean (e5), inside the graph (sentence-transformers exports, e.g. BGE-M3), or the last token (Qwen3/Harrier).
    `key` maps to an ONNX export of the benchmarked model."""

    # key -> (repo, file, pooling)
    ONNX: dict[str, tuple[str, str, str]] = {
        "e5-small": ("Xenova/multilingual-e5-small", "onnx/model_quantized.onnx", "mean"),
        "e5-small-fp32": ("Xenova/multilingual-e5-small", "onnx/model.onnx", "mean"),
        "bge-m3": ("Xenova/bge-m3", "onnx/sentence_transformers_quantized.onnx", "graph"),
        "harrier-0.6b": ("onnx-community/harrier-oss-v1-0.6b-ONNX", "onnx/model_quantized.onnx", "graph"),
    }

    def __init__(self, key: str, max_tokens: int = 512, batch_size: int = 64, threads: int | None = None) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        repo, file, self.pooling = self.ONNX[key]
        self.spec = MODELS[key.removesuffix("-fp32")]
        if file.endswith("model_quantized.onnx") and self.pooling == "last":
            _hf_file(repo, file + "_data")  # external weights live next to the graph
        self.name = f"onnx:{key}"  # distinct from the torch model's name: the embedding cache is keyed by it
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(_hf_file(repo, file), opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tok = Tokenizer.from_file(_hf_file(repo, "tokenizer.json"))
        self.tok.enable_truncation(max_tokens)
        if self.pooling == "last":
            self.tok.enable_padding(direction="left")  # the last position is then every row's last real token
        else:
            self.tok.enable_padding()
        self.batch_size = batch_size
        self.dim = int(self._encode(["probe"]).shape[1])

    def _encode(self, texts: list[str]) -> Vectors:
        enc = self.tok.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        if "position_ids" in self.inputs:
            feed["position_ids"] = np.clip(np.cumsum(mask, axis=1) - 1, 0, None)
        out = self.session.run(None, feed)
        if self.pooling == "graph":
            names = [o.name for o in self.session.get_outputs()]
            return _normalise(out[names.index("sentence_embedding")] if "sentence_embedding" in names else out[-1])
        hidden = out[0]
        if self.pooling == "last":
            return _normalise(hidden[:, -1, :])
        summed = (hidden * mask[..., None]).sum(axis=1)
        return _normalise(summed / np.maximum(mask.sum(axis=1, keepdims=True), 1))

    def documents(self, texts: list[str]) -> Vectors:
        parts = [
            self._encode([self.spec.passage_prefix + t for t in texts[i : i + self.batch_size]])
            for i in range(0, len(texts), self.batch_size)
        ]
        return np.concatenate(parts) if parts else np.zeros((0, self.dim), dtype=np.float32)

    def query(self, text: str) -> Vectors:
        return self._encode([self.spec.query_prefix + text])[0]
