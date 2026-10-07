"""`intel market pull`: versioned snapshot of what the market data platform (mip) knows, for training.

Stored like a warehouse pull (Parquet + manifest + SHA256SUMS) under data/market/<id>/:
- content_feature_performance  peer-normalised lift by platform x tag (cue, language, hashtags, duration, ...)
                               from ~1.2M Reddit, 217k TikTok and 150k YouTube posts (marts layer)
- occasion_day                 occasion calendar per community (core layer)
- cues.yaml                    the cue dictionary used to tag market posts, so own captions are tagged the same way
- biryani_reviews / _listings  reviews (text >20 chars) and listings of the Berlin places that sell biryani (2.2)
"""

from __future__ import annotations

from pathlib import Path

from intel.pull import run_pull

# Reviews of the Berlin listings that sell biryani (Phase 2.2, competitor weakness miner). Reviewer identities are
# already salted hashes in the platform; we do not even copy the hash.
BIRYANI_LISTINGS = """select distinct o.listing_id from core.offering o
    where o.name ilike any (array['%biryani%','%biriyani%','%kacchi%'])"""
REVIEWS_SQL = f"""select r.review_id, r.listing_id, r.platform, r.posted_at, r.rating_norm, r.text, r.has_owner_reply,
       rl.lang
    from core.review r join ({BIRYANI_LISTINGS}) b using (listing_id)
    left join core.review_language rl using (review_id)
    where length(coalesce(r.text, '')) > 20"""
LISTINGS_SQL = f"""select l.listing_id, l.platform, l.name, l.district, l.planning_area, l.postcode, l.lat, l.lon,
       l.is_halal, l.status,
       (select count(*) from core.offering o where o.listing_id = l.listing_id
          and o.name ilike any (array['%biryani%','%biriyani%','%kacchi%'])) as biryani_items
    from core.listing l join ({BIRYANI_LISTINGS}) b using (listing_id)"""
ITEMS = [
    ("marts", "content_feature_performance"),
    ("core", "occasion_day"),
    ("query", "biryani_reviews", REVIEWS_SQL),
    ("query", "biryani_listings", LISTINGS_SQL),
]
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
