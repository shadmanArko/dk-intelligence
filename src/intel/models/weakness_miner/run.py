"""Weekly run of the competitor weakness miner: mine -> summarise -> owner check -> judge -> run folder.

Output is an insights bundle (not a scorer): it is packaged, approved and shown on the VPS Insights page exactly
like a model, but it never answers /v1/predict.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import random
from pathlib import Path

import joblib
import pandas as pd
import yaml

from intel.judge import gate

from .mine import ASPECTS_FILE, dictionary, is_design, mine

SPEC = yaml.safe_load(Path(__file__).with_name("spec.yaml").read_text())
SAMPLE_PER_ASPECT = 10
SAMPLE_NO_ASPECT = 60
MIN_MENTIONS = 5
# Angles Dhaka Kacchi could answer a weakness with. SUGGESTIONS ONLY: each must be true and checked before use
# (brand book section 7: no superlatives, no health claims, halal only with a named source).
ANGLES = {
    "rice": "Fluffy, separate grains: the rice is layered raw with the meat and sealed on dum for 6+ hours.",
    "meat": "Fresh mutton marinated overnight, cooked with the rice (kacchi = raw), never pre-cooked.",
    "delivery_time": "One cooking day, fixed Saturday slots and a personal confirmation: you know when it arrives.",
    "temperature": "Packed straight from the handi; free pickup in Wedding for the hottest box.",
    "order_accuracy": "One signature dish, made one way, confirmed personally before Saturday.",
    "packaging": "Show how each 1000 ml box is sealed (only if true: check the containers).",
    "portion": "1000 ml regular box, 750 ml taster: the size is stated before you order.",
    "spice_level": "Aromatic, not fiery: say the spice level plainly in every caption.",
    "price_value": "Final price per box including the delivery band, stated up front (PAngV).",
    "taste": "Hand-roasted, hand-blended spices before every batch.",
    "sauce_oil": "Kacchi has no curry gravy: the flavour is in the rice; ghee, not cheap oil.",
    "hygiene_health": "Prepared with a physician's care for hygiene (no health claims about the food itself).",
    "service": "You message the people who cook.",
    "authenticity": "The Old Dhaka wedding kacchi, the founder's family recipe.",
}


def load_reviews(market_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    r = pd.read_parquet(market_dir / "biryani_reviews.parquet")
    r["rating_norm"] = pd.to_numeric(r["rating_norm"], errors="coerce")
    listings = pd.read_parquet(market_dir / "biryani_listings.parquet")
    return r, listings


def _examples(df: pd.DataFrame, n: int) -> list[str]:
    """Short, readable clauses only; no names (reviewers are never stored)."""
    ok = df[df["clause"].str.len().between(15, 140)]
    return ok.sort_values("posted_at", ascending=False)["clause"].drop_duplicates().head(n).tolist()


def summarise(clauses: pd.DataFrame, reviews: pd.DataFrame, listings: pd.DataFrame) -> dict:
    labels = dictionary()["labels"]
    ex = clauses.explode("aspects").dropna(subset=["aspects"])
    n_reviews = reviews["review_id"].nunique()

    def table(g: pd.DataFrame, base_reviews: int) -> list[dict]:
        out = []
        for a, ga in g.groupby("aspects"):
            rev = ga.drop_duplicates(["review_id"])
            neg = rev[rev["polarity"] == "negative"]
            out.append(
                {
                    "aspect": a,
                    "label": labels[a],
                    "mentions": len(rev),
                    "share_of_reviews": round(len(rev) / base_reviews, 4) if base_reviews else None,
                    "negative": len(neg),
                    "negative_rate": round(len(neg) / len(rev), 3),
                    "positive_rate": round((rev["polarity"] == "positive").mean(), 3),
                }
            )
        return sorted(out, key=lambda x: -x["negative"])

    overall = table(ex, n_reviews)
    for row in overall:
        g = ex[ex["aspects"] == row["aspect"]]
        row["complaints"] = _examples(g[g["polarity"] == "negative"], 4)
        row["praise"] = _examples(g[g["polarity"] == "positive"], 2)
        row["our_angle"] = ANGLES.get(row["aspect"])

    lst = listings.set_index("listing_id")
    rv = reviews.groupby("listing_id").agg(reviews=("review_id", "nunique"), rating=("rating_norm", "mean"))
    competitors = []
    for lid, g in ex.groupby("listing_id"):
        if lid not in rv.index or rv.loc[lid, "reviews"] < 20:
            continue
        rows = [t for t in table(g, int(rv.loc[lid, "reviews"])) if t["mentions"] >= MIN_MENTIONS]
        base = {t["aspect"]: t["negative_rate"] for t in overall}
        weak = sorted(
            (t for t in rows if t["negative_rate"] > base.get(t["aspect"], 1) + 0.10),
            key=lambda t: -(t["negative_rate"] - base[t["aspect"]]),
        )[:3]
        strong = sorted(
            (t for t in rows if t["negative_rate"] < base.get(t["aspect"], 0) - 0.10),
            key=lambda t: t["negative_rate"] - base[t["aspect"]],
        )[:3]
        info = lst.loc[lid].where(lst.loc[lid].notna(), None) if lid in lst.index else {}
        competitors.append(
            {
                "listing_id": str(lid),
                "name": info.get("name"),
                "district": info.get("district"),
                "reviews": int(rv.loc[lid, "reviews"]),
                "rating": round(float(rv.loc[lid, "rating"]) * 5, 2),
                "biryani_items": int(info.get("biryani_items") or 0),
                "weak": [
                    {
                        "aspect": t["aspect"],
                        "label": t["label"],
                        "negative_rate": t["negative_rate"],
                        "mentions": t["mentions"],
                    }
                    for t in weak
                ],
                "strong": [
                    {
                        "aspect": t["aspect"],
                        "label": t["label"],
                        "negative_rate": t["negative_rate"],
                        "mentions": t["mentions"],
                    }
                    for t in strong
                ],
            }
        )
    competitors.sort(key=lambda c: -c["reviews"])

    ex = ex.merge(listings[["listing_id", "district"]], on="listing_id", how="left")
    districts = []
    for dname, g in ex.dropna(subset=["district"]).groupby("district"):
        nrev = g["review_id"].nunique()
        if nrev < 50:
            continue
        rows = [t for t in table(g, nrev) if t["mentions"] >= MIN_MENTIONS * 2]
        districts.append(
            {
                "district": dname,
                "reviews": int(nrev),
                "listings": int(g["listing_id"].nunique()),
                "top_complaints": [
                    {
                        "aspect": t["aspect"],
                        "label": t["label"],
                        "negative_rate": t["negative_rate"],
                        "mentions": t["mentions"],
                    }
                    for t in rows[:3]
                ],
            }
        )
    districts.sort(key=lambda d: -d["reviews"])

    bir = clauses[clauses["biryani"]].explode("aspects").dropna(subset=["aspects"])
    biryani = table(bir, clauses.loc[clauses["biryani"], "review_id"].nunique()) if len(bir) else []
    for row in biryani:
        g = bir[(bir["aspects"] == row["aspect"]) & (bir["polarity"] == "negative")]
        row["complaints"] = _examples(g, 3)

    period = pd.to_datetime(reviews["posted_at"], utc=True)
    return {
        "reviews": int(n_reviews),
        "clauses": int(len(clauses)),
        "listings": int(reviews["listing_id"].nunique()),
        "period": [period.min().date().isoformat(), period.max().date().isoformat()],
        "clauses_with_aspect": round(float((clauses["aspects"].str.len() > 0).mean()), 3),
        "overall": overall,
        "biryani_mentions": biryani,
        "competitors": competitors[:60],
        "districts": districts,
    }


def check_sample(clauses: pd.DataFrame, path: Path, seed: int = 7) -> list[dict]:
    """Fixed owner-check sample, created once: stratified by predicted aspect, from the NON-design 90% only, plus
    clauses with no aspect (to measure what the dictionary misses). Re-used every week."""
    if path.exists():
        return json.loads(path.read_text())
    rnd = random.Random(seed)  # noqa: S311  (reproducible sampling, not security)
    pool = clauses[~clauses["review_id"].map(is_design) & clauses["clause"].str.len().between(12, 220)]
    picked: dict[str, dict] = {}
    for a in sorted(dictionary()["aspects"]):
        rows = pool[pool["aspects"].map(lambda x, a=a: a in x)]
        for r in rows.sample(min(SAMPLE_PER_ASPECT, len(rows)), random_state=rnd.randint(0, 10**6)).itertuples():
            picked.setdefault(f"{r.review_id}:{r.clause_idx}", r._asdict())
    none = pool[pool["aspects"].str.len() == 0]
    for r in none.sample(min(SAMPLE_NO_ASPECT, len(none)), random_state=rnd.randint(0, 10**6)).itertuples():
        picked.setdefault(f"{r.review_id}:{r.clause_idx}", r._asdict())
    items = [{"key": k, "clause": v["clause"], "rating": v["rating"]} for k, v in picked.items()]
    rnd.shuffle(items)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=1, ensure_ascii=False))
    return items


def check_metrics(clauses: pd.DataFrame, labels: list[dict]) -> dict:
    """Owner labels vs the CURRENT dictionary (so a dictionary change is re-measured on the same checked clauses)."""
    from .mine import analyse_text

    tp = fp = fn = pol_ok = pol_n = 0
    for lab in labels:
        if lab.get("skip"):
            continue
        rid, idx = lab["key"].rsplit(":", 1)
        row = clauses[(clauses["review_id"].astype(str) == rid) & (clauses["clause_idx"] == int(idx))]
        if row.empty:
            continue
        pred = analyse_text(row.iloc[0]["clause"], row.iloc[0]["rating"])
        pred = pred[0] if pred else {"aspects": [], "polarity": "neutral"}
        p, t = set(pred["aspects"]), set(lab.get("aspects", []))
        tp, fp, fn = tp + len(p & t), fp + len(p - t), fn + len(t - p)
        if lab.get("polarity"):
            pol_n += 1
            pol_ok += pred["polarity"] == lab["polarity"]
    checked = len([x for x in labels if not x.get("skip")])
    return {
        "checked": checked,
        "aspect_precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "aspect_recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "polarity_accuracy": round(pol_ok / pol_n, 3) if pol_n else None,
    }


def run(market_dir: Path, runs_dir: Path, checks_dir: Path, log=print) -> Path:
    reviews, listings = load_reviews(market_dir)
    log(f"mining {len(reviews):,} reviews of {reviews['listing_id'].nunique()} biryani listings")
    clauses = mine(reviews)
    insights = summarise(clauses, reviews, listings)
    sample = check_sample(clauses, checks_dir / "weakness_miner_sample.json")
    labels_path = checks_dir / "weakness_miner_labels.jsonl"
    labels = [json.loads(x) for x in labels_path.read_text().splitlines() if x.strip()] if labels_path.exists() else []
    m = check_metrics(clauses, labels)
    g = SPEC["gates"]

    prev = sorted(runs_dir.glob("*_weakness_miner/insights.json"))
    top5 = [r["aspect"] for r in insights["overall"][:5]]
    if prev:
        old = [r["aspect"] for r in json.loads(prev[-1].read_text())["overall"][:5]]
        changed = len(set(top5) - set(old))
        stable = (changed <= g["max_top5_change"], f"{changed} of the top-5 complaints changed since last run")
    else:
        stable = (True, "first run, nothing to compare")
    if m["checked"] < g["min_checked"]:
        human = (None, f"{m['checked']} of {len(sample)} clauses checked by you (need {g['min_checked']})")
    else:
        ok = (m["aspect_precision"] or 0) >= g["min_aspect_precision"] and (m["polarity_accuracy"] or 0) >= g[
            "min_polarity_accuracy"
        ]
        human = (
            ok,
            f"aspect precision {m['aspect_precision']}, recall {m['aspect_recall']}, polarity accuracy "
            f"{m['polarity_accuracy']} on {m['checked']} checked clauses",
        )
    now = dt.datetime.now(dt.UTC)
    dict_hash = hashlib.sha256((market_dir.name + ASPECTS_FILE.read_text()).encode()).hexdigest()[:6]
    version = f"wm-{now:%Y%m%dT%H%M%S}-{dict_hash}"
    bundle = {"kind": "insights", "version": version, "insights": insights, "check": m}
    buf = io.BytesIO()
    joblib.dump(bundle, buf)
    gates = [
        gate(
            "data",
            insights["reviews"] >= g["min_reviews"],
            f"{insights['reviews']:,} reviews, "
            f"{insights['listings']} listings, {insights['period'][0]} to {insights['period'][1]}",
        ),
        gate("owner_check", *human),
        gate("stable", *stable),
        gate("works", len(buf.getvalue()) / 1e6 <= g["max_package_mb"], f"{len(buf.getvalue()) / 1e6:.2f} MB"),
    ]
    failed = [x["gate"] for x in gates if x["status"] == "fail"]
    pending = [x["gate"] for x in gates if x["status"] == "pending"]
    verdict = "fail" if failed else "pending" if pending else "pass"
    report = {
        "kind": "weakness_miner_run",
        "version": version,
        "pull_id": "-",
        "market_id": market_dir.name,
        "created_at": now.isoformat(timespec="seconds"),
        "split": {"train": 0, "test": m["checked"], "cutoff": now.isoformat()},
        "judge": {"verdict": verdict, "failed": failed, "gates": gates, "gain": None},
        "challenger": {
            "model": "aspect dictionary",
            "params": {},
            "features": "aspects.yaml",
            "threshold": None,
            "test": {
                "balanced_accuracy": m["aspect_precision"],
                "roc_auc": m["aspect_recall"],
                "brier": m["polarity_accuracy"],
                "n": m["checked"],
            },
        },
        "champion": {
            "model": "none (first insights)",
            "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": 0},
        },
        "check": m,
        "top_complaints": top5,
    }
    out = runs_dir / f"{now:%Y%m%dT%H%M%SZ}_weakness_miner"
    out.mkdir(parents=True)
    (out / "model.joblib").write_bytes(buf.getvalue())
    (out / "insights.json").write_text(json.dumps(insights, indent=1, ensure_ascii=False, default=str))
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    return out
