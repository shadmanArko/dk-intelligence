"""`intel market pull`: versioned snapshot of what the market data platform (mip) knows, for training.

Stored like a warehouse pull (Parquet + manifest + SHA256SUMS) under data/market/<id>/:
- content_feature_performance  peer-normalised lift by platform x tag (cue, language, hashtags, duration, ...)
                               from ~1.2M Reddit, 217k TikTok and 150k YouTube posts (marts layer)
- occasion_day                 occasion calendar per community (core layer)
- cues.yaml                    the cue dictionary used to tag market posts, so own captions are tagged the same way
"""

from __future__ import annotations

from pathlib import Path

from intel.pull import run_pull

ITEMS = [("marts", "content_feature_performance"), ("core", "occasion_day")]
CUES_FILE = "config/keywords/social_content.yaml"


def cue_dictionary_bytes(mip_dir: Path) -> bytes:
    """Only the `cues` section (the search queries are irrelevant here)."""
    import yaml

    data = yaml.safe_load((mip_dir / CUES_FILE).read_text())
    return yaml.safe_dump({"cues": data["cues"]}, allow_unicode=True, sort_keys=True).encode()


def run_market_pull(dsn: str, mip_dir: Path, market_dir: Path, keep: int, log=print) -> Path:
    return run_pull(
        dsn,
        market_dir,
        keep=keep,
        log=log,
        items=ITEMS,
        extra_files={"cues.yaml": cue_dictionary_bytes(mip_dir)},
        application_name="dk-intelligence-market",
    )
