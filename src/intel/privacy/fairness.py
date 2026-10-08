"""The responsible-AI gate on every package (ADR 0024), checked by `build_package` before anything is built:

1. protected attributes, by name: no field (key, feature or column name) about religion, ethnicity, nationality, ...
   in what a model uses or produces, except the fields of a reviewed exception;
2. protected attributes, by lineage: a model that reads a table marked `protected_derived` (residents by origin or
   citizenship) needs a reviewed exception for exactly those tables, whatever its fields are called; exceptions expire;
3. blocked words: text a model writes (policy `generated_text`) contains none of the content playbook's blocked words;
4. personal data: no email, phone number or IBAN in what is packaged (the business's own numbers are allow-listed).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from intel.privacy import policy
from intel.privacy.pii import find_in_text

NAME_LISTS = ("features", "columns", "feature_names", "inputs")  # lists of names inside a report, checked like keys


def _norm(name: str) -> str:
    return "_" + "_".join(re.findall(r"[a-z0-9]+", name.lower())) + "_"


def _protected(name: str, terms: list[str]) -> str | None:
    n = _norm(name)
    return next((t for t in terms if _norm(t) in n), None)


def _names(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """(path, name) for every dict key and every entry of a name list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, str(k)
            if k in NAME_LISTS and isinstance(v, list):
                yield from ((f"{path}.{k}", str(x)) for x in v if isinstance(x, str))
            yield from _names(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for v in obj:
            yield from _names(v, f"{path}[]")


def _strings(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def exception_for(model: str) -> dict[str, Any] | None:
    for e in policy()["fairness"]["exceptions"]:
        if e["model"] == model:
            return dict(e)
    return None


def protected_fields(model: str, obj: Any, extra_terms: list[str] | None = None) -> Counter[str]:
    """`name (term)` -> count, for protected-attribute names not covered by a reviewed exception."""
    terms = list(policy()["fairness"]["protected_terms"]) + list(extra_terms or [])
    allowed = set((exception_for(model) or {}).get("fields") or [])
    hits: Counter[str] = Counter()
    for _, name in _names(obj):
        term = _protected(name, terms)
        if term and name not in allowed:
            hits[f"{name} ({term})"] += 1
    return hits


def blocked_word_hits(obj: Any, words: list[str]) -> Counter[str]:
    pats = [
        (w, re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.IGNORECASE) if w.isascii() else re.compile(re.escape(w)))
        for w in words
        if w
    ]
    hits: Counter[str] = Counter()
    for s in _strings(obj):
        for w, p in pats:
            if p.search(s):
                hits[w] += 1
    return hits


def pii_hits(obj: Any) -> Counter[str]:
    found: Counter[str] = Counter()
    for s in _strings(obj):
        found.update(find_in_text(s))
    return found


def _market_yaml(market_dir: Path | None, name: str) -> dict[str, Any] | None:
    f = market_dir / name if market_dir else None
    if f is None or not f.exists():
        return None
    data: dict[str, Any] = yaml.safe_load(f.read_text()) or {}
    return data


def blocked_words(market_dir: Path | None) -> list[str] | None:
    """The playbook's blocked words from the run's market snapshot (None when the snapshot is not on this machine)."""
    rules = _market_yaml(market_dir, "content_rules.yaml")
    return None if rules is None else [str(w) for w in (rules.get("blocked_words") or [])]


def community_ids(market_dir: Path | None) -> list[str]:
    """The data platform's communities of origin (bangladeshi, turkish, ...): protected terms too."""
    return sorted((_market_yaml(market_dir, "community_countries.yaml") or {}).keys())


def reads(model: str) -> set[str]:
    """The snapshot tables a model reads, from the data contracts (`used_by`, ADR 0017)."""
    from intel.quality.contracts import CONTRACTS

    return {c.table for c in CONTRACTS if model in c.used_by}


def protected_inputs(model: str) -> set[str]:
    derived = {
        t
        for section in ("warehouse_pull", "market_pull")
        for t, spec in policy()[section]["tables"].items()
        if spec.get("protected_derived")
    }
    return reads(model) & derived


def check(
    model: str,
    documents: dict[str, Any],
    words: list[str] | None,
    today: dt.date | None = None,
    extra_terms: list[str] | None = None,
) -> list[str]:
    """Problems (empty = OK) for the package `model` built from `documents` (name -> parsed JSON)."""
    problems: list[str] = []
    exc = exception_for(model)
    sensitive = protected_inputs(model)
    if sensitive - set((exc or {}).get("reads") or []):
        problems.append(
            f"{model} reads {sorted(sensitive)} (residents by origin or citizenship) without a reviewed exception "
            "in src/intel/privacy/policy.yaml (fairness.exceptions)"
        )
    if exc and dt.date.fromisoformat(str(exc["review_again"])) < (today or dt.date.today()):
        problems.append(f"the fairness exception for {model} expired on {exc['review_again']}: review it again")
    writes_text = model in set(policy()["fairness"].get("generated_text") or [])
    for doc, obj in documents.items():
        if p := protected_fields(model, obj, extra_terms):
            problems.append(f"{doc}: protected-attribute fields {dict(p)}")
        if writes_text and words and (b := blocked_word_hits(obj, words)):
            problems.append(f"{doc}: blocked words {sorted(b)} in {sum(b.values())} texts")
        if pii := pii_hits(obj):
            problems.append(f"{doc}: personal data {dict(pii)} (counts only)")
    return problems


def bundle_documents(bundle: Any) -> dict[str, Any]:
    """What a served bundle shows or uses, as JSON: the insights (and owner check) of an insights model, the feature
    columns and inputs of a scorer. Fitted estimators are not inspected; their inputs are."""
    import json

    if not isinstance(bundle, dict):
        return {}
    if "insights" in bundle:
        part = {k: bundle[k] for k in ("insights", "check") if k in bundle}
    else:
        part = {"columns": list(bundle.get("columns") or []), "inputs": bundle.get("inputs")}
    doc: dict[str, Any] = json.loads(json.dumps(part, default=str))
    return {"model.joblib": doc}
