"""Cross-encoder rerankers: re-read (query, passage) pairs and score how well the passage answers the query, 0..1.

Used twice: to sort the hybrid search's top results, and by `verify_citations` to score a claim against its cited
source. Scores are calibrated probabilities (sigmoid of a relevance logit, or P("yes") for Qwen3-Reranker), so one
threshold works across queries (the "not found" threshold is set on the golden set, ADR 0027).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

RERANKERS: dict[str, str] = {
    "bge-reranker-v2-m3": "BAAI/bge-reranker-v2-m3",
    "qwen3-reranker-0.6b": "Qwen/Qwen3-Reranker-0.6B",
}
Scorer = Callable[[list[tuple[str, str]]], list[float]]
QWEN_TASK = "Given a question about restaurant reviews or brand rules, judge whether the passage answers it"


def _bge(model_id: str, batch_size: int = 32) -> Scorer:
    from sentence_transformers import CrossEncoder  # bench group (P4.2); ONNX for serving once chosen

    model = CrossEncoder(model_id, max_length=512)

    def score(pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        out = model.predict(pairs, batch_size=batch_size, activation_fn=_sigmoid(), show_progress_bar=False)
        return [float(x) for x in out]

    return score


def _sigmoid() -> Any:
    import torch

    return torch.nn.Sigmoid()


def _qwen(model_id: str, batch_size: int = 16) -> Scorer:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(model_id, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.float16 if device == "mps" else None)
    model = model.to(device).eval()  # type: ignore[arg-type,unused-ignore]  # odd stubs; untyped in CI (no bench group)
    yes, no = tok.convert_tokens_to_ids("yes"), tok.convert_tokens_to_ids("no")
    prefix = (
        "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct "
        'provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
    )
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    def score(pairs: list[tuple[str, str]]) -> list[float]:
        out: list[float] = []
        for i in range(0, len(pairs), batch_size):
            batch = [
                f"{prefix}<Instruct>: {QWEN_TASK}\n<Query>: {q}\n<Document>: {d}{suffix}"
                for q, d in pairs[i : i + batch_size]
            ]
            enc = tok(batch, padding=True, truncation=True, max_length=1024, return_tensors="pt").to(device)
            with torch.no_grad():
                logits = model(**enc).logits[:, -1, :]
            two = torch.stack([logits[:, no], logits[:, yes]], dim=1).float()
            out.extend(torch.softmax(two, dim=1)[:, 1].tolist())
        return out

    return score


def load_reranker(key: str) -> Scorer:
    model_id = RERANKERS[key]
    return _qwen(model_id) if key.startswith("qwen3") else _bge(model_id)
