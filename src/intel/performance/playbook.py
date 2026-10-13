"""The per-platform playbook: what the numbers support, in words an agent can use, with the evidence beside each line.

Artifact: `data/performance/<pull id>/playbook.json` (and `LATEST`). The studio reads it from code and puts the few
supported lines into the strategist's and the adapter's tasks; dk_intel serves it to interactive agents.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from intel.performance import stats
from intel.performance.features import FEATURES

PLATFORMS = ("instagram", "facebook", "threads", "youtube", "tiktok")
ENOUGH = 30  # matured posts before a platform gets any statement at all
FORMAT_VERSION = 1
LABEL = {
    "format": "format", "weekday": "weekday", "window": "posting window", "caption_length": "caption length",
    "first_line": "first line", "hashtags": "hashtags", "emojis": "emojis", "asks_question": "asks a question",
    "calls_to_action": "calls to action", "german_first_line": "German first line", "bilingual": "two languages",
    "mentions_price": "mentions a price",
}  # fmt: skip


# Features the brand rules decide (language, prices) or the business cares about beyond reach (calls to action): shown
# to the owner for information, never offered to an agent as a lever.
INFO_ONLY = frozenset({"bilingual", "german_first_line", "mentions_price", "calls_to_action"})
# Values the checker would block anyway (a first line over 60 characters, more than 5 hashtags): not levers.
INFO_ONLY_VALUES = frozenset({("first_line", "long (over 60)"), ("hashtags", "6 or more")})
BINARY = frozenset({"asks_question", "calls_to_action", "german_first_line", "bilingual", "mentions_price"})
TABLE_KEYS = ("feature", "value", "outcome", "n", "rest_n", "median_lift", "verdict", "diff", "diff_ci", "ci")
OUTCOMES = {"lift_audience": "reach", "lift_engagement": "engagement"}


def sentence(platform: str, f: dict[str, Any], outcome_label: str = "engagement") -> str:
    """One finding as a plain sentence with its evidence."""
    name = LABEL.get(f["feature"], f["feature"])
    value = f["value"] if f["feature"] not in BINARY else ("yes" if f["value"] == "yes" else "no")
    lo, hi = f["ci"]
    d_lo, d_hi = f["diff_ci"]
    return (f"{platform.capitalize()}, {name} = {value}: median {outcome_label} {f['median_lift']}x a usual post "
            f"(95% interval {lo}-{hi}x); {f['diff']:+} against the other posts (interval {d_lo:+} to {d_hi:+}); "
            f"{f['n']} posts with it, {f['rest_n']} without.")  # fmt: skip


def _usable(df: pd.DataFrame, outcome: str) -> bool:
    """An outcome with no variation (Threads has no reach in the warehouse: every value is 0) says nothing."""
    return bool(df[outcome].notna().sum() >= ENOUGH and df[outcome].nunique() > 3)


def build(dataset: pd.DataFrame, pull_id: str, now: dt.datetime | None = None) -> dict[str, Any]:
    platforms: dict[str, Any] = {}
    for platform in PLATFORMS:
        d = dataset[dataset["platform"] == platform] if len(dataset) else dataset
        matured = d[d["matured"]] if len(d) else d
        entry: dict[str, Any] = {"posts": len(d), "matured": len(matured), "enough": len(matured) >= ENOUGH}
        if platform == "tiktok":
            entry["note"] = "no performance data: posts go out by hand and TikTok has no analytics in the warehouse yet"
        elif not entry["enough"]:
            entry["note"] = f"only {len(matured)} matured posts: nothing is claimed below {ENOUGH}"
        else:
            raw = matured[["engagement", "audience"]].astype(float)
            entry["baseline"] = {"median_engagement": float(raw["engagement"].median()),
                                 "median_audience": float(raw["audience"].median())}  # fmt: skip
            entry["findings"], entry["unclear"], entry["tests"] = [], [], 0
            entry["lines"] = {"works": [], "hurts": [], "info": []}
            for outcome, label in OUTCOMES.items():
                if not _usable(matured, outcome):
                    entry.setdefault("no_outcome", []).append(label)
                    continue
                found = [
                    f
                    for f in stats.findings(matured, FEATURES, outcome)
                    if f["feature"] not in BINARY or f["value"] == "yes"
                ]  # a yes/no feature is one test, not two
                entry["tests"] += sum(1 for f in found if f["verdict"] != "too_few")
                for f in found:
                    f["outcome"] = label
                    entry.setdefault("table", []).append({k: f[k] for k in TABLE_KEYS if k in f})
                    if f["verdict"] in ("works", "hurts"):
                        entry["findings"].append(f)
                    elif f["verdict"] == "no_clear_difference":
                        entry["unclear"].append(f"{f['feature']}={f['value']} ({label})")
            for f in sorted(entry["findings"], key=lambda x: -abs(x["diff"])):
                info = f["feature"] in INFO_ONLY or (f["feature"], f["value"]) in INFO_ONLY_VALUES
                kind = "info" if info else f["verdict"]
                entry["lines"][kind].append(sentence(platform, f, f["outcome"]))
            entry["expected_false_alarms"] = round(0.05 * entry["tests"], 1)
        platforms[platform] = entry
    return {"format_version": FORMAT_VERSION, "pull_id": pull_id,
            "built_at": (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds"),
            "method": "median lift vs the previous 20 matured posts of the platform (reach and engagement), bootstrap "
                      "95% intervals",
            "caveats": ["reach and engagement are not orders: tracked links are empty, nothing is tied to sales",
                        "findings overlap (reels also have short captions): read them as a bundle, not levers",
                        "brand rules decide language and prices: those lines are information for the owner only"],
            "platforms": platforms}  # fmt: skip


def for_prompt(playbook: dict[str, Any] | None, platform: str, works: int = 3, hurts: int = 2) -> dict[str, Any]:
    """The small piece an agent's task carries: a few supported lines, or the honest 'not enough data'."""
    entry = ((playbook or {}).get("platforms") or {}).get(platform)
    if not entry:
        return {"status": "no playbook yet"}
    if not entry.get("enough"):
        return {"status": entry.get("note", "not enough data")}
    if {"reach", "engagement"} <= set(entry.get("no_outcome", [])):
        return {
            "status": "no usable results for this platform in the warehouse (no reach or engagement varies): "
            "no evidence yet"
        }
    lines = entry.get("lines", {})
    return {"status": f"from {entry['matured']} matured posts (reach and engagement, not orders); findings overlap, "
                      "are things to try, and "
                      f"things to try: about {entry.get('expected_false_alarms', 0)} of {entry.get('tests', 0)} "
                      "comparisons may be chance",
            "works": lines.get("works", [])[:works], "hurts": lines.get("hurts", [])[:hurts]}  # fmt: skip


# ---------- the artifact on disk ----------


def write(root: Path, playbook: dict[str, Any], dataset: pd.DataFrame) -> Path:
    folder = root / str(playbook["pull_id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "playbook.json").write_text(json.dumps(playbook, ensure_ascii=False, indent=1))
    dataset.to_parquet(folder / "posts.parquet", index=False)
    (folder / "playbook.md").write_text(markdown(playbook))
    (root / "LATEST").write_text(str(playbook["pull_id"]) + "\n")
    return folder


def latest(root: Path) -> dict[str, Any] | None:
    marker = root / "LATEST"
    if not marker.exists():
        return None
    path = root / marker.read_text().strip() / "playbook.json"
    return dict(json.loads(path.read_text())) if path.exists() else None


def markdown(pb: dict[str, Any]) -> str:
    out = [
        f"# Performance playbook (pull {pb['pull_id']}, built {pb['built_at']})",
        "",
        f"Method: {pb['method']}.",
        *[f"Caveat: {c}." for c in pb.get("caveats", [])],
        "",
    ]
    for platform, e in pb["platforms"].items():
        out.append(f"## {platform.capitalize()}: {e['posts']} posts, {e['matured']} matured")
        if not e.get("enough"):
            out += [f"- {e.get('note')}", ""]
            continue
        out.append(f"- Baseline: a usual post gets {e['baseline']['median_engagement']:.0f} likes, comments, shares, "
                   f"saves and reaches {e['baseline']['median_audience']:.0f} (medians).")  # fmt: skip
        out.append(f"- {e['tests']} comparisons made; about {e['expected_false_alarms']} findings below may be chance.")
        for kind in ("works", "hurts", "info"):
            for line in e["lines"][kind]:
                out.append(f"- **{'for the owner (a brand rule decides)' if kind == 'info' else kind}**: {line}")
        if not any(e["lines"].values()):
            out.append("- Nothing separates the posts clearly yet.")
        if e.get("no_outcome"):
            out.append(f"- No usable data for: {', '.join(e['no_outcome'])}.")
        out.append("")
    return "\n".join(out) + "\n"
