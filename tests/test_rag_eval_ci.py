"""The retrieval ratchet in CI (architecture §7.6, §7.7): real reviews are not in git, so the same search code runs
on a synthetic corpus with the deterministic HashEmbedder. Its scores may never fall below evals/baselines.json
(`ci_synthetic`), which only rises. The real milestone numbers live in the same file, for the record.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from intel.rag import store
from intel.rag.chunk import review_chunks
from intel.rag.embed import HashEmbedder
from tests.test_rag import pg  # noqa: F401 - pg is a fixture

BASELINES = Path(__file__).resolve().parents[1] / "evals" / "baselines.json"
TOPICS = {
    "kalt": "Das Essen kam kalt an, der Reis war eiskalt und das Fleisch lauwarm.",
    "spaet": "Die Lieferung kam zwei Stunden zu spaet, niemand hat angerufen.",
    "wenig": "Viel zu wenig Fleisch im Biryani, fast nur Reis in der Box.",
    "salz": "Kein Salz, kein Geschmack, das Biryani war fade und langweilig.",
    "falsch": "Falsche Bestellung geliefert, das Getraenk fehlte komplett.",
    "teuer": "Fuer den Preis viel zu teuer, die Portion war winzig.",
    "lecker": "Super lecker, perfekt gewuerzt, das beste Essen der Woche.",
    "scharf": "Viel zu scharf, ich konnte es kaum essen, brennt im Mund.",
}
QUESTIONS = {
    "kalt": "Essen kam kalt an",
    "spaet": "Lieferung zu spaet",
    "wenig": "zu wenig Fleisch",
    "salz": "kein Geschmack fade",
    "falsch": "falsche Bestellung",
    "teuer": "zu teuer kleine Portion",
    "scharf": "viel zu scharf",
}


def _corpus() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for i, (topic, text) in enumerate(TOPICS.items()):
        for j in range(3):  # three places say the same thing a little differently
            rows.append((f"{topic}-{j}", f"l{j}", f"{text} Bestellung {i}{j}."))
    reviews = pd.DataFrame(
        {
            "review_id": [r[0] for r in rows],
            "listing_id": [r[1] for r in rows],
            "platform": ["lieferando"] * len(rows),
            "posted_at": pd.to_datetime(["2026-05-01"] * len(rows), utc=True),
            "rating_norm": ["0.2"] * len(rows),
            "text": [r[2] for r in rows],
            "has_owner_reply": [False] * len(rows),
            "lang": ["de"] * len(rows),
        }
    )
    listings = pd.DataFrame(
        {"listing_id": ["l0", "l1", "l2"], "platform": ["lieferando"] * 3, "name": ["A", "B", "C"],
         "district": ["Mitte"] * 3, "planning_area": [None] * 3, "postcode": ["10115"] * 3, "lat": [52.5] * 3,
         "lon": [13.4] * 3, "is_halal": [None] * 3, "status": ["open"] * 3, "biryani_items": [1] * 3}
    )  # fmt: skip
    return reviews, listings


def test_retrieval_never_gets_worse_than_its_baseline(pg):  # noqa: F811
    import psycopg

    emb = HashEmbedder()
    chunks = review_chunks(*_corpus())
    rows = [{**c.row(), "posted_at": c.posted_at.to_pydatetime()} for c in chunks]
    relevant = {t: {c.cite for c in chunks if c.source_id.startswith(f"{t}-")} for t in QUESTIONS}
    hit, rr, prec = [], [], []
    with psycopg.connect(pg, autocommit=True) as c:
        store.load_pack(c, "kp-ci", {}, rows, emb.documents([ch.embed_text for ch in chunks]))
        for topic, q in QUESTIONS.items():
            cites = [h.cite for h in store.search(c, "kp-ci", "reviews", q, emb.query(q), mode="dense", limit=5)]
            hit.append(bool(set(cites[:5]) & relevant[topic]))
            rr.append(next((1 / (i + 1) for i, x in enumerate(cites) if x in relevant[topic]), 0.0))
            prec.append(sum(x in relevant[topic] for x in cites[:3]) / 3)
    got = {"hit_at_5": sum(hit) / len(hit), "mrr": sum(rr) / len(rr), "precision_at_3": sum(prec) / len(prec)}
    base = json.loads(BASELINES.read_text())["ci_synthetic"]
    for k, floor in base.items():
        assert got[k] >= floor - 1e-9, f"{k} fell to {got[k]:.3f} (baseline {floor}): retrieval got worse"


def test_baselines_file_is_complete():
    data = json.loads(BASELINES.read_text())
    assert {"ci_synthetic", "milestones"} <= set(data)
    for name, m in data["milestones"].items():
        assert m["date"] and m["source"], name
    if not data["ci_synthetic"]:
        pytest.fail("ci_synthetic baselines missing")
