"""Content scorer features. ONE function (`build_features`) used by training AND serving.

Inputs per post (all known before posting): platform, content_type, posted_at (planned time), caption.
Market knowledge comes from a `MarketContext` frozen at training time and shipped inside the model package,
so serving computes exactly the same numbers.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from intel.champions.harness_v1.features import build_features as champion_features

WORD = re.compile(r"\w+", re.UNICODE)
HASHTAG = re.compile(r"#\w+")
MENTION = re.compile(r"@\w+")
URL = re.compile(r"https?://|www\.", re.I)
PRICE = re.compile(r"€|\beur\b|\beuro\b|\d+[.,]\d{2}\b", re.I)
BENGALI = re.compile(r"[ঀ-৿]")
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF]")
OWN_CUES = ("halal", "opening_news", "dum_pot_reveal", "price_value", "family_sharing", "behind_scenes")
HOME_COMMUNITIES = ("bangladeshi", "indian", "pakistani", "german")
CAP_DAYS = 30
PRIOR_PLATFORM = "tiktok"  # closest market to Reels / short video; FB/IG have no market performance data

NOMINAL = ["platform", "content_type_filled", "is_video", "day_of_week", "hour_bucket", "hashtag_band"]
NUMERIC = [
    "caption_length",
    "hashtag_count",
    "emoji_count",
    "mention_count",
    "line_count",
    "first_line_length",
    "n_cues",
    "market_cue_lift",
    "days_to_feast",
    "days_since_feast",
]
BINARY = ["asks_question", "has_price", "script_bengali", "has_url"] + [f"cue_{c}" for c in OWN_CUES]
ALL = NOMINAL + NUMERIC + BINARY


def fold(s: str) -> str:
    """Same folding as the market data platform's tagger (mip.enrich.content.fold)."""
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("ı", "i")):
        s = s.replace(a, b)
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def tokens(s: str) -> tuple[str, ...]:
    return tuple(WORD.findall(fold(s)))


@dataclass
class MarketContext:
    cues: dict[str, list[str]]  # cue id -> phrases
    cue_lift: dict[str, float]  # cue id -> market lift minus platform baseline
    feast_days: list[str]  # ISO dates of home-community feasts
    source: dict = field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, market_dir: Path) -> MarketContext:
        import yaml

        cues = yaml.safe_load((market_dir / "cues.yaml").read_text())["cues"]
        perf = pd.read_parquet(market_dir / "content_feature_performance.parquet")
        p = perf[perf["platform"] == PRIOR_PLATFORM]
        base = float(p[p["tag_type"] == "post_type"]["mean_lift"].mean())
        lift = {r.tag_id: round(float(r.mean_lift) - base, 4) for r in p[p["tag_type"] == "cue"].itertuples()}
        occ = pd.read_parquet(market_dir / "occasion_day.parquet")
        feasts = occ[(occ["food_role"] == "feast") & occ["community_id"].isin(HOME_COMMUNITIES)]["day"]
        return cls(
            cues={k: list(v) for k, v in cues.items()},
            cue_lift=lift,
            feast_days=sorted({str(d) for d in feasts}),
            source={"market_snapshot": market_dir.name, "prior_platform": PRIOR_PLATFORM, "baseline_lift": base},
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> MarketContext:
        return cls(**d)

    def match_cues(self, caption: str) -> set[str]:
        toks = tokens(caption)
        if not toks:
            return set()
        grams = {toks[i : i + n] for n in range(1, 7) for i in range(len(toks) - n + 1)}
        return {cue for cue, phrases in self.cues.items() if any(tokens(ph) in grams for ph in phrases if ph)}


def _days(posted: pd.Series, feasts: list[str]) -> tuple[np.ndarray, np.ndarray]:
    f = np.array(sorted(pd.to_datetime(feasts).date)) if feasts else np.array([])
    to_next, since = [], []
    for d in posted.dt.tz_convert("Europe/Berlin").dt.date:
        ahead = [(x - d).days for x in f if x >= d]
        behind = [(d - x).days for x in f if x <= d]
        to_next.append(min(min(ahead, default=CAP_DAYS), CAP_DAYS))
        since.append(min(min(behind, default=CAP_DAYS), CAP_DAYS))
    return np.array(to_next), np.array(since)


def build_features(df: pd.DataFrame, ctx: MarketContext) -> pd.DataFrame:
    posted = pd.to_datetime(df["posted_at"], utc=True)
    base = champion_features(df.assign(posted_at=posted))  # the champion's 7 features, unchanged
    out = base.copy()
    cap = df["caption"].fillna("").astype(str)
    out["hashtag_band"] = pd.cut(out["hashtag_count"], [-1, 0, 3, 10_000], labels=["0", "1-3", "4+"]).astype(str)
    out["emoji_count"] = cap.map(lambda c: len(EMOJI.findall(c)))
    out["mention_count"] = cap.map(lambda c: len(MENTION.findall(c)))
    out["line_count"] = cap.map(lambda c: len([x for x in c.splitlines() if x.strip()]))
    out["first_line_length"] = cap.map(lambda c: len(c.strip().splitlines()[0]) if c.strip() else 0)
    out["asks_question"] = cap.str.contains("?", regex=False)
    out["has_price"] = cap.map(lambda c: bool(PRICE.search(c)))
    out["script_bengali"] = cap.map(lambda c: bool(BENGALI.search(c)))
    out["has_url"] = cap.map(lambda c: bool(URL.search(c)))
    matched = cap.map(ctx.match_cues)
    out["n_cues"] = matched.map(len)
    out["market_cue_lift"] = matched.map(
        lambda m: float(np.mean([ctx.cue_lift[c] for c in m if c in ctx.cue_lift])) if m & ctx.cue_lift.keys() else 0.0
    )
    for c in OWN_CUES:
        out[f"cue_{c}"] = matched.map(lambda m, c=c: c in m)
    out["days_to_feast"], out["days_since_feast"] = _days(posted, ctx.feast_days)
    for b in BINARY:
        out[b] = out[b].astype(bool)
    out["is_video"] = out["is_video"].astype(bool)
    return out[ALL]
