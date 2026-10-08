"""Occasion planner: the next 30 days for Dhaka Kacchi, with the content rules applied to every line.

Inputs: the data platform's occasion calendar (audience sizes, food roles, lead days, greetings, bridge dishes,
Berlin events), the content rules (neutral presentation of religious occasions, blocked words), market response per
occasion (TikTok peer lift), and our own posts (which weekdays / hours reached more people than our recent posts).
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
BERLIN = "Europe/Berlin"


def _blocked(text, words: list[str]) -> bool:
    return any(w.lower() in str(text).lower() for w in words)


def _scrub(value, words):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        kept = [v for v in value if not _blocked(v, words)]
        return kept or None
    return None if _blocked(value, words) else value


def _list(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    try:
        return [x for x in list(v) if x is not None]
    except TypeError:
        return []


def present(o: dict, rules: dict) -> dict:
    """Neutral label / greeting / audience per content_rules; blocked words removed everywhere."""
    words = rules.get("blocked_words") or []
    pres = (rules.get("occasion_presentation") or {}).get(o["occasion_type"])
    t = {
        "public_id": o["occasion_type"],
        "community_id": o["community_id"],
        "community": o["community"],
        "label": o["occasion_type"].replace("_", " "),
        "names": _scrub(_list(o.get("holiday_names")), words),
        "greeting": _scrub(o.get("greeting"), words),
        "greeting_translit": _scrub(o.get("greeting_translit"), words),
        "greeting_needs_review": bool(o.get("greeting_needs_review")),
        "hashtags": [],
    }
    if pres:
        t["public_id"] = pres.get("id", o["occasion_type"])
        t["label"] = pres["label"]
        t["names"] = [pres["label"]]
        if pres.get("community"):
            t["community_id"] = pres["community"]
        g = pres.get("greeting") or {}
        t["greeting"], t["greeting_translit"] = g.get("text"), g.get("translit")
        t["greeting_needs_review"] = bool(g.get("review"))
        t["hashtags"] = pres.get("hashtags") or []
    for k in ("greeting", "greeting_translit"):
        if isinstance(t[k], float):  # NaN from parquet = no greeting
            t[k] = None
    if t["greeting"] is None or _blocked(t["greeting"], words) or _blocked(t.get("greeting_translit") or "", words):
        t["greeting"] = t["greeting_translit"] = None
    if not t["names"]:
        t["names"] = [t["label"]]
    return t


def plan(
    occ: pd.DataFrame, rules: dict, today: dt.date, market_lift: dict[str, float], country_people: dict | None = None
) -> dict:
    country_people = country_people or {}
    horizon = today + dt.timedelta(days=SPEC["horizon_days"])
    occ = occ[(occ["day"] >= today) & (occ["day"] <= horizon)].copy()
    share_rules = rules.get("occasion_audience") or {}
    home = rules.get("home_community") or {}
    words = rules.get("blocked_words") or []
    picks, notes, events = {}, [], {}
    for o in occ.to_dict("records"):
        for e in _list(o.get("berlin_events")):
            events.setdefault(e, set()).add(o["day"])
        if o["occasion_type"] in SPEC["skip_types"] or o["community_id"] == "all":
            continue
        people = float(o.get("berlin_citizens") or 0)
        cc = [c for c in _list(o.get("countries")) if c]
        per = country_people.get(o["community_id"], {})
        if cc and len(per) > 1:  # e.g. Canadian Thanksgiving is not an occasion for every British/American Berliner
            known = [per.get(c) for c in cc if per.get(c) is not None]
            people = float(sum(known)) if known else people * len(cc) / len(per)
        people *= ((share_rules.get(o["occasion_type"]) or {}).get("share") or {}).get(o["community_id"], 1.0)
        if o["community_id"] == home.get("id"):
            people *= float(home.get("weight", 1.0))
        t = present(o, rules)
        if o["food_role"] == "remembrance" or o["tone"] == "solemn":
            notes.append(
                {
                    "day": o["day"].isoformat(),
                    "label": t["label"],
                    "community": t["community_id"],
                    "names": t["names"],
                    "advice": "respectful message only, no sales post",
                }
            )
            continue
        if people < SPEC["min_people"] or (
            o["occasion_type"] == "national_day" and people < SPEC["min_people_national_day"]
        ):
            continue
        w = SPEC["role_weight"].get(o["food_role"], 1.0)
        key = (t["label"], t["community_id"])
        cur = picks.get(key)
        start = o.get("content_window_start") or o["day"]
        if cur is None:
            dishes = [str(d).split(" — ")[0] for d in _list(o.get("bridge_dishes"))][:3]
            picks[key] = {
                **t,
                "first_day": o["day"],
                "last_day": o["day"],
                "start_posting": start,
                "people": int(people),
                "food_role": o["food_role"],
                "tone": o["tone"],
                "score": round(w * math.sqrt(max(people, 1)) / 100, 2),
                "market_lift": market_lift.get(o["occasion_type"]),
                "bridge_dishes": [d for d in dishes if not _blocked(d, words)],
                "districts": _list(o.get("top_districts_mh"))[:3],
                "estimated_date": bool(o.get("is_estimated")),
            }
        else:
            cur["first_day"], cur["last_day"] = min(cur["first_day"], o["day"]), max(cur["last_day"], o["day"])
            cur["start_posting"] = min(cur["start_posting"], start)
            cur["people"] = max(cur["people"], int(people))
    out = sorted(picks.values(), key=lambda p: -p["score"])
    for p in out:
        for k in ("first_day", "last_day", "start_posting"):
            p[k] = p[k].isoformat()
        p["start_posting"] = max(p["start_posting"], today.isoformat())
    ev = [{"event": e, "days": sorted(d.isoformat() for d in ds)} for e, ds in events.items() if not _blocked(e, words)]
    return {
        "from": today.isoformat(),
        "to": horizon.isoformat(),
        "picks": out,
        "respectful_notes": notes,
        "berlin_events": sorted(ev, key=lambda x: x["days"][0])[:15],
        "calendar_until": max(occ["day"]).isoformat() if len(occ) else None,
        "days_covered": (max(occ["day"]) - today).days + 1 if len(occ) else 0,
    }


def timing(pull_dir: Path | None) -> dict:
    """When did OUR posts reach more people than our recent posts? Share of posts above the recent median, with n."""
    if pull_dir is None:
        return {}
    from intel.datasets.content import build_content_dataset

    ds = build_content_dataset(pull_dir)
    ds = ds[ds["matured"] & ds["y_reach"].notna()].copy()
    local = pd.to_datetime(ds["posted_at"], utc=True).dt.tz_convert(BERLIN)
    ds["weekday"] = local.dt.day_name()
    ds["band"] = pd.cut(
        local.dt.hour, [-1, 10, 13, 16, 19, 23], labels=["morning", "midday", "afternoon", "late afternoon", "evening"]
    ).astype(str)
    out = {}
    for col in ("weekday", "band"):
        g = ds.groupby(["platform", col])["y_reach"].agg(["mean", "count"]).reset_index()
        out[col] = [
            {
                "platform": r.platform,
                col: getattr(r, col),
                "above_recent": round(float(r.mean), 3),
                "posts": int(r.count),
            }
            for r in g.itertuples()
            if r.count >= 8
        ]
    out["posts"] = int(len(ds))
    return out


def all_strings(obj) -> list[str]:
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in all_strings(v)] + [str(k) for k in obj]
    if isinstance(obj, list):
        return [s for v in obj for s in all_strings(v)]
    return [str(obj)] if obj is not None else []


def run(market_dir: Path, pull_dir: Path | None, runs_dir: Path, today: dt.date | None = None, log=print) -> Path:
    today = today or dt.datetime.now(dt.UTC).date()
    rules = yaml.safe_load((market_dir / "content_rules.yaml").read_text())
    occ = pd.read_parquet(market_dir / "occasions_next.parquet")
    occ["day"] = pd.to_datetime(occ["day"]).dt.date
    for c in ("content_window_start",):
        occ[c] = pd.to_datetime(occ[c]).dt.date
    perf = pd.read_parquet(market_dir / "content_feature_performance.parquet")
    tk = perf[perf["platform"] == "tiktok"]
    base = float(tk[tk["tag_type"] == "post_type"]["mean_lift"].mean())
    lift = {r.tag_id: round(float(r.mean_lift) - base, 3) for r in tk[tk["tag_type"] == "occasion"].itertuples()}
    cmap = yaml.safe_load((market_dir / "community_countries.yaml").read_text())
    cit = pd.read_parquet(market_dir / "afs_citizenship.parquet")
    cit = dict(zip(cit["citizenship"], cit["residents"], strict=True))
    country_people = {
        cid: {cc: cit.get(lab) for cc, lab in zip(v["countries"], v["afs_citizenship"], strict=False)}
        for cid, v in cmap.items()
        if v["countries"] and len(v["afs_citizenship"]) >= len(v["countries"])
    }
    ins = plan(occ, rules, today, lift, country_people)
    ins["timing"] = timing(pull_dir)
    log(f"occasion planner: {len(ins['picks'])} occasions, {len(ins['respectful_notes'])} respectful notes")

    words = rules.get("blocked_words") or []
    leaks = sorted({w for s in all_strings(ins) for w in words if w.lower() in s.lower()})
    now = dt.datetime.now(dt.UTC)
    version = (
        f"op-{now:%Y%m%dT%H%M%S}-{hashlib.sha256((market_dir.name + SPEC_FILE.read_text()).encode()).hexdigest()[:6]}"
    )
    bundle = {"kind": "insights", "version": version, "insights": ins}
    buf = io.BytesIO()
    joblib.dump(bundle, buf)
    g = SPEC["gates"]
    gates = [
        gate(
            "data",
            ins["days_covered"] >= g["min_days_covered"],
            f"calendar reaches {ins['calendar_until']} ({ins['days_covered']} days ahead); "
            f"{len(ins['picks'])} occasions",
        ),
        gate("content_rules", not leaks, "no blocked word anywhere" if not leaks else f"blocked words found: {leaks}"),
        gate("works", len(buf.getvalue()) / 1e6 <= g["max_package_mb"], f"{len(buf.getvalue()) / 1e6:.2f} MB"),
    ]
    failed = [x["gate"] for x in gates if x["status"] == "fail"]
    verdict = "fail" if failed else "pass"
    top = [
        f"{p['label']} · {p['community_id']} · {p['first_day']} (start {p['start_posting']})" for p in ins["picks"][:5]
    ]
    report = {
        "kind": "occasion_planner_run",
        "version": version,
        "pull_id": pull_dir.name if pull_dir else "-",
        "market_id": market_dir.name,
        "created_at": now.isoformat(timespec="seconds"),
        "split": {"train": 0, "test": len(ins["picks"]), "cutoff": now.isoformat()},
        "judge": {"verdict": verdict, "failed": failed, "gates": gates, "gain": None},
        "challenger": {
            "model": "occasion weighting",
            "params": {},
            "features": "spec.yaml",
            "threshold": None,
            "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": len(ins["picks"])},
        },
        "champion": {"model": "none", "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": 0}},
        "top_complaints": top,
    }
    out = runs_dir / f"{now:%Y%m%dT%H%M%SZ}_occasion_planner"
    out.mkdir(parents=True)
    (out / "model.joblib").write_bytes(buf.getvalue())
    (out / "insights.json").write_text(json.dumps(ins, indent=1, ensure_ascii=False, default=str))
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    return out
