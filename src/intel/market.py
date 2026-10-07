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
# Phase 2.3 community map: per LOR planning area (542). Deliberately NOT included: the census religion variable
# (areas are chosen by audience, food culture and reach, never by religion).
LOR_COMMUNITY_SQL = """select area_code, community_id, residents, reference_date from core.community_audience
    where level = 'lor_mh' and length(area_code) = 8
      and reference_date = (select max(reference_date) from core.community_audience where level = 'lor_mh')"""
LOR_STATS_SQL = """select area_id, variable, value, reference_date from core.area_stat_lor
    where variable in ('population.einwohner', 'age_10y.a20bis29', 'age_10y.a30bis39',
                       'age_10y.insgesamt_bevoelkerung', 'household_size.1_person', 'household_size.2_personen',
                       'household_size.insgesamt_haushalte', 'rent_net_cold.durchschnmietekalt')
       or variable like 'rent_net_cold.%'"""
LOR_PLACES_SQL = f"""select l.planning_area_id as area_id, max(l.planning_area) as name, mode() within group
       (order by l.district) as district, avg(l.lat) as lat, avg(l.lon) as lon, count(*) as listings,
       count(*) filter (where l.platform in ('wolt', 'lieferando', 'uber_eats')) as delivery_listings,
       count(*) filter (where l.listing_id in ({BIRYANI_LISTINGS})) as biryani_listings
    from core.listing l where l.planning_area_id is not null and l.lat is not null group by l.planning_area_id"""
POSTCODE_AREA_SQL = """select postcode, mode() within group (order by planning_area_id) as area_id,
       mode() within group (order by district) as district
    from core.listing where postcode is not null and planning_area_id is not null group by postcode"""
ITEMS = [
    ("marts", "content_feature_performance"),
    ("core", "occasion_day"),
    ("query", "biryani_reviews", REVIEWS_SQL),
    ("query", "biryani_listings", LISTINGS_SQL),
    ("query", "lor_community", LOR_COMMUNITY_SQL),
    ("query", "lor_stats", LOR_STATS_SQL),
    ("query", "lor_places", LOR_PLACES_SQL),
    ("query", "postcode_area", POSTCODE_AREA_SQL),
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
