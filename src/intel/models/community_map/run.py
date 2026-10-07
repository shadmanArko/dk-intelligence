"""Community map: a transparent score per Berlin planning area (LOR), plus district summary and a bubble map.

score = audience x reach. audience = weighted percentile ranks (spec.yaml weights) of: South Asian-origin residents,
meat-and-rice food cultures, international food explorers, young adults in small households. reach = delivery band
factor by straight-line distance from the pickup point. Inputs are public area statistics; output is used to choose
AREAS (delivery zones, area-targeted ads, pop-ups), never to target or exclude people.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path

import joblib
import pandas as pd
import yaml

from intel.judge import gate

SPEC_FILE = Path(__file__).with_name("spec.yaml")
SPEC = yaml.safe_load(SPEC_FILE.read_text())


def km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def reach_band(d: float) -> dict:
    return next(b for b in SPEC["reach"] if d <= b["max_km"])


def order_postcodes(pull_dir: Path | None) -> pd.Series:
    """Postcodes of direct orders (payload.address_postal_code). Only counted per area; never shown."""
    if pull_dir is None or not (pull_dir / "raw_orders_direct.parquet").exists():
        return pd.Series(dtype=str)
    raw = pd.read_parquet(pull_dir / "raw_orders_direct.parquet", columns=["payload"])
    pcs = [json.loads(p).get("address_postal_code") if isinstance(p, str) else None for p in raw["payload"]]
    return pd.Series([str(p).strip()[:5] for p in pcs if p], dtype=str)


def build(market_dir: Path, pull_dir: Path | None = None) -> pd.DataFrame:
    comm = pd.read_parquet(market_dir / "lor_community.parquet")
    stats = pd.read_parquet(market_dir / "lor_stats.parquet")
    places = pd.read_parquet(market_dir / "lor_places.parquet")
    places["lat"], places["lon"] = places["lat"].astype(float), places["lon"].astype(float)

    wide = comm.pivot_table(index="area_code", columns="community_id", values="residents", aggfunc="sum").fillna(0)
    df = pd.DataFrame(index=wide.index)
    for g, members in SPEC["groups"].items():
        df[g] = wide[[m for m in members if m in wide.columns]].sum(axis=1)
    st = stats.pivot_table(index="area_id", columns="variable", values="value", aggfunc="first")
    df = df.join(st, how="left")
    pop = df.get("population.einwohner")
    young = df.get("age_10y.a20bis29", 0).fillna(0) + df.get("age_10y.a30bis39", 0).fillna(0)
    small = (
        df.get("household_size.1_person", 0).fillna(0) + df.get("household_size.2_personen", 0).fillna(0)
    ) / df.get("household_size.insgesamt_haushalte").replace(0, pd.NA)
    df["residents"] = pop
    df["young_small_households"] = (young * small.astype(float)).fillna(0)
    df["rent_per_sqm"] = (
        df.filter(like="rent_net_cold").iloc[:, 0] if any(c.startswith("rent_net_cold") for c in df) else None
    )
    df = df.join(places.set_index("area_id"), how="left")
    o = SPEC["origin"]
    df["distance_km"] = [
        round(km(o["lat"], o["lon"], a, b), 1) if pd.notna(a) else None
        for a, b in zip(df["lat"], df["lon"], strict=True)
    ]
    bands = [reach_band(d) if pd.notna(d) else {"factor": 0.0, "label": "location unknown"} for d in df["distance_km"]]
    df["reach_factor"] = [b["factor"] for b in bands]
    df["reach_band"] = [b["label"] for b in bands]
    for g in SPEC["weights"]:
        df[f"rank_{g}"] = df[g].rank(pct=True).fillna(0)
    df["audience"] = sum(w * df[f"rank_{g}"] for g, w in SPEC["weights"].items())
    df["audience_rank"] = df["audience"].rank(pct=True)
    df["score"] = (df["audience"] * df["reach_factor"]).round(4)
    df["biryani_listings"] = df["biryani_listings"].fillna(0).astype(int)
    df["gap"] = (df["audience_rank"] >= SPEC["gap_audience_rank"]) & (df["biryani_listings"] == 0)

    pa = pd.read_parquet(market_dir / "postcode_area.parquet")
    pcs = order_postcodes(pull_dir)
    area_of = dict(zip(pa["postcode"].astype(str), pa["area_id"], strict=True))
    df["orders"] = 0
    for pc in pcs:
        if area_of.get(pc) in df.index:
            df.loc[area_of[pc], "orders"] += 1
    df.index.name = "area_id"
    return df.reset_index()


def summarise(df: pd.DataFrame) -> dict:
    cols = [
        "area_id",
        "name",
        "district",
        "score",
        "audience",
        "reach_band",
        "distance_km",
        "south_asian",
        "meat_rice_cultures",
        "internationals",
        "young_small_households",
        "residents",
        "delivery_listings",
        "biryani_listings",
        "gap",
        "orders",
        "lat",
        "lon",
    ]
    top = df.sort_values("score", ascending=False)
    areas = [
        {k: (None if pd.isna(v) else (round(v, 3) if isinstance(v, float) else v)) for k, v in r.items()}
        for r in top[cols].to_dict("records")
    ]
    for a in areas:
        for k in (
            "south_asian",
            "meat_rice_cultures",
            "internationals",
            "young_small_households",
            "residents",
            "delivery_listings",
            "biryani_listings",
            "orders",
        ):
            a[k] = None if a[k] is None else int(a[k])
        a["gap"] = bool(a["gap"])
    dist = (
        df.dropna(subset=["district"])
        .groupby("district")
        .agg(
            areas=("area_id", "count"),
            score=("score", "mean"),
            south_asian=("south_asian", "sum"),
            meat_rice_cultures=("meat_rice_cultures", "sum"),
            internationals=("internationals", "sum"),
            biryani_listings=("biryani_listings", "sum"),
            gaps=("gap", "sum"),
            orders=("orders", "sum"),
            distance_km=("distance_km", "median"),
        )
        .sort_values("score", ascending=False)
        .reset_index()
    )
    districts = [
        {k: (round(v, 3) if isinstance(v, float) else (int(v) if hasattr(v, "item") else v)) for k, v in r.items()}
        for r in dist.to_dict("records")
    ]
    return {
        "areas": areas,
        "districts": districts,
        "top20": [a["area_id"] for a in areas[:20]],
        "top_audience": [a["area_id"] for a in sorted(areas, key=lambda a: -(a["audience"] or 0))[:20]],
        "located": round(float(df["lat"].notna().mean()), 3),
        "n_areas": int(len(df)),
        "orders_located": int(df["orders"].sum()),
        "weights": SPEC["weights"],
        "origin": SPEC["origin"],
        "reach": SPEC["reach"],
    }


def run(market_dir: Path, pull_dir: Path | None, runs_dir: Path, log=print) -> Path:
    df = build(market_dir, pull_dir)
    ins = summarise(df)
    log(f"community map: {ins['n_areas']} areas, top: {ins['areas'][0]['name']} ({ins['areas'][0]['district']})")
    g = SPEC["gates"]
    prev = sorted(runs_dir.glob("*_community_map/insights.json"))
    if prev:
        old = set(json.loads(prev[-1].read_text())["top20"])
        overlap = len(old & set(ins["top20"]))
        stable = (overlap >= g["min_top20_overlap"], f"{overlap} of last run's top-20 areas still in the top 20")
    else:
        stable = (True, "first run, nothing to compare")
    now = dt.datetime.now(dt.UTC)
    version = (
        f"cm-{now:%Y%m%dT%H%M%S}-{hashlib.sha256((market_dir.name + SPEC_FILE.read_text()).encode()).hexdigest()[:6]}"
    )
    bundle = {"kind": "insights", "version": version, "insights": ins}
    buf = io.BytesIO()
    joblib.dump(bundle, buf)
    gates = [
        gate(
            "data",
            ins["n_areas"] >= g["min_areas"] and ins["located"] >= g["min_located_share"],
            f"{ins['n_areas']} areas, {ins['located']:.0%} located",
        ),
        gate("stable", *stable),
        gate("owner_review", True, "the ranked areas are shown in the approval: approving = your plausibility check"),
        gate("works", len(buf.getvalue()) / 1e6 <= g["max_package_mb"], f"{len(buf.getvalue()) / 1e6:.2f} MB"),
    ]
    failed = [x["gate"] for x in gates if x["status"] == "fail"]
    verdict = "fail" if failed else "pass"
    top5 = [f"{a['name']} ({a['district']})" for a in ins["areas"][:5]]
    report = {
        "kind": "community_map_run",
        "version": version,
        "pull_id": pull_dir.name if pull_dir else "-",
        "market_id": market_dir.name,
        "created_at": now.isoformat(timespec="seconds"),
        "split": {"train": 0, "test": ins["n_areas"], "cutoff": now.isoformat()},
        "judge": {"verdict": verdict, "failed": failed, "gates": gates, "gain": None},
        "challenger": {
            "model": "weighted area score",
            "params": SPEC["weights"],
            "features": "spec.yaml",
            "threshold": None,
            "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": ins["n_areas"]},
        },
        "champion": {
            "model": "none (first map)",
            "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": 0},
        },
        "top_complaints": top5,  # shown in the approval message as "top areas"
    }
    out = runs_dir / f"{now:%Y%m%dT%H%M%SZ}_community_map"
    out.mkdir(parents=True)
    (out / "model.joblib").write_bytes(buf.getvalue())
    (out / "insights.json").write_text(json.dumps(ins, indent=1, ensure_ascii=False, default=str))
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    return out
