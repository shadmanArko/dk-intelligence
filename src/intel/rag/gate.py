"""The activation gate: a new knowledge pack goes live only if it is not worse than the active one (§4.8, ADR 0027).

Judged per question, not on means: with ~20 questions one question moves a mean by 0.05, so a mean tolerance would
flip on noise. On the dev split only (test is for milestones, the lesson of rule 9):
- at most `max_lost` answerable questions may lose the relevant hit they had in the active pack's top 10;
- the pack's own residual-PII scan found nothing, and it has a search index.
Relevance labels (qrels) are citation ids and grades only, so they live in git; cites are stable across rebuilds.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "evals" / "rag" / "golden.jsonl"
QRELS = ROOT / "evals" / "rag" / "qrels.jsonl"
K = 10

Search = Callable[[str, dict[str, Any]], list[str]]
"""(pack_id, golden question) -> cites in rank order."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def relevant(qrels: list[dict[str, Any]]) -> dict[str, set[str]]:
    return {q["qid"]: {c for c, g in q["grades"].items() if g >= 1} for q in qrels}


@dataclass
class GateResult:
    candidate: str
    active: str | None
    passed: bool
    reasons: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    gained: list[str] = field(default_factory=list)
    hit_rate: dict[str, float] = field(default_factory=dict)  # share of answerable dev questions with a hit in top K

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def evaluate(
    search: Search,
    candidate: str,
    active: str | None,
    manifest: dict[str, Any],
    questions: list[dict[str, Any]],
    qrels: dict[str, set[str]],
    max_lost: int = 1,
) -> GateResult:
    res = GateResult(candidate, active, passed=True)
    if manifest.get("kind") != "full":
        res.reasons.append("the pack has no search index")
    if manifest.get("residual_pii"):
        res.reasons.append(f"residual PII found: {manifest['residual_pii']}")
    asked = [q for q in questions if q["split"] == "dev" and q["kind"] == "answerable" and qrels.get(q["id"])]
    if not asked:
        res.reasons.append("no labelled dev questions: the gate can't judge (label them first)")
    found: dict[str, dict[str, bool]] = {"candidate": {}, "active": {}}
    for q in asked:
        rel = qrels[q["id"]]
        found["candidate"][q["id"]] = bool(rel & set(search(candidate, q)[:K]))
        if active is not None:
            found["active"][q["id"]] = bool(rel & set(search(active, q)[:K]))
    for name, f in found.items():
        if f:
            res.hit_rate[name] = round(sum(f.values()) / len(f), 3)
    if active is not None:
        res.lost = sorted(q for q, ok in found["active"].items() if ok and not found["candidate"][q])
        res.gained = sorted(q for q, ok in found["candidate"].items() if ok and not found["active"][q])
        if len(res.lost) > max_lost:
            res.reasons.append(
                f"{len(res.lost)} dev questions lost their relevant hit (at most {max_lost}): {res.lost}"
            )
    res.passed = not res.reasons
    return res
