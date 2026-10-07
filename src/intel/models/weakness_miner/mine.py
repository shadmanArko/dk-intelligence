"""Competitor weakness miner: aspects + polarity per clause of each review, then summaries.

Transparent by design: every aspect decision is a dictionary phrase you can read in aspects.yaml, every polarity a
cue word (with negation) or, when a clause has none, the review's star rating.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from intel.models.content_scorer.features import fold

ASPECTS_FILE = Path(__file__).with_name("aspects.yaml")
WORD = re.compile(r"\w+", re.UNICODE)  # hyphens split words: "Salsa-Soße" -> salsa, sosse
SUFFIXES = ("", "e", "es", "er", "en", "em", "n", "s")  # German adjective/noun endings: kalt -> kalte, kaltes
CLAUSE_SPLIT = re.compile(r"[.!?;\n]+|\s(?:aber|but|jedoch|allerdings|however|dafuer)\s", re.I)
ZU_NEG = {
    "scharf",
    "wenig",
    "spaet",
    "teuer",
    "salzig",
    "suess",
    "trocken",
    "klein",
    "lange",
    "lang",
    "kalt",
    "fettig",
    "oelig",
    "viel",
    "sauer",
    "matschig",
    "weich",
    "hart",
    "lauwarm",
}
COMPLAINT_ASPECTS = {"order_accuracy", "hygiene_health"}  # topics that are only ever mentioned to complain
DESIGN_MOD = 10  # review_id hash % 10 == 0 -> design slice (dictionary built from it); never used for the check sample


def is_design(review_id) -> bool:
    return int(hashlib.sha256(str(review_id).encode()).hexdigest(), 16) % DESIGN_MOD == 0


def tokens(text: str) -> list[str]:
    return WORD.findall(fold(text))


@lru_cache
def dictionary() -> dict:
    cfg = yaml.safe_load(ASPECTS_FILE.read_text())

    def compile_phrases(phrases):
        out = []
        for ph in phrases:
            prefix = ph.endswith("*")
            toks = tokens(ph.rstrip("*"))
            if toks:
                out.append((tuple(toks), prefix))
        return out

    pol = cfg["polarity"]
    return {
        "aspects": {k: compile_phrases(v["phrases"]) for k, v in cfg["aspects"].items()},
        "labels": {k: v["label"] for k, v in cfg["aspects"].items()},
        "mentions": {k: compile_phrases(v) for k, v in cfg["mentions"].items()},
        "pos": {t for p in pol["positive"] for t in tokens(p)},
        "neg": {t for p in pol["negative"] for t in tokens(p)},
        "negators": {t for p in pol["negators"] for t in tokens(p)},
    }


def _word_eq(tok: str, word: str) -> bool:
    """Exact, or the same word with a usual German ending (only for words of 4+ letters, to avoid 'alt' ~ 'alte')."""
    if tok == word:
        return True
    return len(word) >= 4 and tok.startswith(word) and tok[len(word) :] in SUFFIXES


def _match(toks: list[str], phrases) -> bool:
    n = len(toks)
    for ph, prefix in phrases:
        k = len(ph)
        for i in range(n - k + 1):
            head_ok = all(_word_eq(toks[i + j], ph[j]) for j in range(k - 1))
            last = toks[i + k - 1]
            if head_ok and (last.startswith(ph[-1]) if prefix else _word_eq(last, ph[-1])):
                return True
    return False


def _in(tok: str, words: set[str]) -> bool:
    return tok in words or any(_word_eq(tok, w) for w in words if tok.startswith(w))


def clause_polarity(toks: list[str], rating: float | None) -> tuple[str, str]:
    """('positive'|'negative'|'neutral', how): cue words with negation flip; else the review rating decides."""
    d = dictionary()
    score = 0
    for i, t in enumerate(toks):
        val = 1 if _in(t, d["pos"]) else -1 if _in(t, d["neg"]) else 0
        if i > 0 and toks[i - 1] == "zu" and _in(t, ZU_NEG):
            val = -1
        if val and any(w in d["negators"] for w in toks[max(0, i - 3) : i]):
            val = -val
        score += val
    if score:
        return ("positive" if score > 0 else "negative"), "cue words"
    if rating is not None:
        if rating <= 0.4:
            return "negative", "rating"
        if rating >= 0.8:
            return "positive", "rating"
    return "neutral", "none"


def split_clauses(text: str) -> list[str]:
    return [c.strip(" ,:-") for c in CLAUSE_SPLIT.split(text or "") if len(c.strip(" ,:-")) >= 3]


def analyse_text(text: str, rating: float | None) -> list[dict]:
    d = dictionary()
    out = []
    for i, c in enumerate(split_clauses(text)):
        toks = tokens(c)
        aspects = sorted(a for a, ph in d["aspects"].items() if _match(toks, ph))
        pol, how = clause_polarity(toks, rating)
        if how != "cue words" and set(aspects) & COMPLAINT_ASPECTS:
            pol, how = "negative", "topic"  # "Lieferung kam nicht an", "Haar im Essen": always a complaint
        out.append(
            {
                "clause_idx": i,
                "clause": c,
                "aspects": aspects,
                "polarity": pol,
                "polarity_from": how,
                "biryani": _match(toks, d["mentions"]["biryani"]),
            }
        )
    return out


def mine(reviews: pd.DataFrame) -> pd.DataFrame:
    """One row per clause: review_id, listing_id, posted_at, rating, clause, aspects, polarity, biryani."""
    rows = []
    for r in reviews.itertuples(index=False):
        rating = None if pd.isna(r.rating_norm) else float(r.rating_norm)
        for c in analyse_text(r.text, rating):
            rows.append(
                {
                    "review_id": r.review_id,
                    "listing_id": r.listing_id,
                    "posted_at": r.posted_at,
                    "rating": rating,
                    "lang": r.lang,
                    **c,
                }
            )
    return pd.DataFrame(rows)
