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
# Phase 2.4 occasion planner: the next 60 days of the occasion calendar (with audience sizes, greetings, bridge
# dishes and Berlin events). Presentation rules are applied in dk-intelligence (content_rules.yaml).
OCCASIONS_SQL = """select * from marts.occasion_content_calendar
    where day between current_date and current_date + 60"""
RULE_KEYS = ("occasion_presentation", "blocked_words", "home_community", "occasion_audience", "no_occasion_platforms")
PLAYBOOK_FILE = "config/content/playbook.yaml"
COMMUNITIES_FILE = "config/taxonomies/communities.yaml"
CITIZENSHIP_SQL = """select citizenship, residents from staging.stg_afs__citizenship
    where reference_date = (select max(reference_date) from staging.stg_afs__citizenship)"""
# Phase 2.6 creator matcher: non-business TikTok accounts with at least one Berlin post, aggregated per account
# (public account data only; no commenters). Engagement from the platform's per-post performance mart.
CREATOR_BASE = """with berlin as (
      select distinct p.account_id from core.post p
      where p.platform = 'tiktok' and (p.caption ilike '%berlin%' or 'berlin' = any(p.hashtags))),
    acc as (select a.* from core.social_account a join berlin b using (account_id)
            where a.platform = 'tiktok' and a.business_id is null)"""
CREATORS_SQL = (
    CREATOR_BASE
    + """,
    perf as (select cp.account_id, count(*) posts, max(cp.posted_at) last_post,
                    count(*) filter (where cp.posted_at > now() - interval '90 days') posts_90d,
                    percentile_cont(0.5) within group (order by cp.views) median_views,
                    percentile_cont(0.5) within group (order by (coalesce(cp.likes, 0) + coalesce(cp.comments, 0)
                        + coalesce(cp.shares, 0))::float / nullif(cp.views, 0)) median_engagement_rate,
                    max(cp.followers) followers
             from marts.content_performance cp join acc using (account_id) where cp.mature group by 1),
    posts as (select p.account_id, count(*) n,
                     count(*) filter (where p.caption ilike '%berlin%' or 'berlin' = any(p.hashtags)) berlin_posts
              from core.post p join acc using (account_id) group by 1),
    tags as (select p.account_id,
                    count(distinct p.post_id) filter (where ct.tag_type in ('dish', 'rice_dish')) rice_dish_posts,
                    count(distinct p.post_id) filter (where ct.tag_type in ('dish', 'rice_dish') or (ct.tag_type = 'cue'
                        and ct.tag_id in ('recipe', 'spicy', 'halal', 'street_food', 'mukbang_asmr', 'delivery',
                                          'price_value', 'review_test', 'dum_pot_reveal'))) food_posts,
                    count(distinct p.post_id) filter (where ct.tag_type = 'dish' and ct.tag_id ilike any
                        (array['%biryani%', '%kacchi%', '%pulao%', '%tehari%'])) biryani_posts,
                    count(distinct p.post_id) filter (where ct.tag_type = 'community' and ct.tag_id in
                        ('bangladeshi', 'indian', 'pakistani', 'nepali')) south_asian_posts,
                    count(distinct p.post_id) filter (where ct.tag_type = 'community' and ct.tag_id in
                        ('turkish', 'syrian', 'lebanese', 'iraqi', 'afghan', 'iranian', 'levant_gulf_yemen',
                         'egyptian', 'palestinian', 'kurdish')) meat_rice_posts,
                    count(distinct p.post_id) filter (where ct.tag_type = 'cue' and ct.tag_id = 'halal') halal_posts,
                    count(distinct p.post_id) filter (where ct.tag_type = 'cue' and ct.tag_id in
                        ('review_test', 'tour_vlog', 'ranking_list', 'mukbang_asmr')) review_posts,
                    count(distinct p.post_id) filter (where ct.tag_type = 'cue'
                        and ct.tag_id = 'street_food') street_food_posts
             from core.post p join acc using (account_id) join core.content_tag ct using (post_id) group by 1)
    select acc.account_id, acc.handle, acc.display_name, left(acc.bio, 200) as bio, acc.website, acc.is_business,
           acc.link_score, acc.link_decision, posts.n as posts_seen,
           posts.berlin_posts, perf.posts as measured_posts, perf.posts_90d, perf.last_post, perf.median_views,
           perf.median_engagement_rate, coalesce(perf.followers, (select s.followers from core.social_account_snapshot s
               where s.account_id = acc.account_id order by observed_at desc limit 1)) as followers,
           coalesce(tags.rice_dish_posts, 0) rice_dish_posts, coalesce(tags.food_posts, 0) food_posts,
           coalesce(tags.biryani_posts, 0) biryani_posts,
           coalesce(tags.south_asian_posts, 0) south_asian_posts, coalesce(tags.meat_rice_posts, 0) meat_rice_posts,
           coalesce(tags.halal_posts, 0) halal_posts, coalesce(tags.review_posts, 0) review_posts,
           coalesce(tags.street_food_posts, 0) street_food_posts
    from acc left join perf using (account_id) left join posts using (account_id) left join tags using (account_id)"""
)
CREATOR_POSTS_SQL = (
    CREATOR_BASE
    + """
    select account_id, permalink, left(coalesce(caption, title, ''), 120) as caption, posted_at, views, likes
    from (select cp.account_id, p.permalink, p.caption, p.title, cp.posted_at, cp.views, cp.likes, cp.engagement_rate,
                 row_number() over (partition by cp.account_id order by cp.views desc nulls last) rk
          from marts.content_performance cp join acc using (account_id) join core.post p using (post_id)
          where cp.mature and (p.caption ilike '%berlin%' or 'berlin' = any(p.hashtags))) x
    where rk <= 3"""
)
ITEMS = [
    ("marts", "content_feature_performance"),
    ("core", "occasion_day"),
    ("query", "biryani_reviews", REVIEWS_SQL),
    ("query", "biryani_listings", LISTINGS_SQL),
    ("query", "lor_community", LOR_COMMUNITY_SQL),
    ("query", "lor_stats", LOR_STATS_SQL),
    ("query", "lor_places", LOR_PLACES_SQL),
    ("query", "postcode_area", POSTCODE_AREA_SQL),
    ("query", "occasions_next", OCCASIONS_SQL),
    ("query", "afs_citizenship", CITIZENSHIP_SQL),
    ("query", "creators", CREATORS_SQL),
    ("query", "creator_posts", CREATOR_POSTS_SQL),
]
CUES_FILE = "config/keywords/social_content.yaml"


def cue_dictionary_bytes(mip_dir: Path) -> bytes:
    """Only the `cues` section (the search queries are irrelevant here)."""
    import yaml

    data = yaml.safe_load((mip_dir / CUES_FILE).read_text())
    return yaml.safe_dump({"cues": data["cues"]}, allow_unicode=True, sort_keys=True).encode()


def content_rules_bytes(mip_dir: Path) -> bytes:
    """The occasion-presentation and blocked-word rules from the data platform's content playbook."""
    import yaml

    pb = yaml.safe_load((mip_dir / PLAYBOOK_FILE).read_text())
    return yaml.safe_dump({k: pb.get(k) for k in RULE_KEYS}, allow_unicode=True, sort_keys=True).encode()


def community_countries_bytes(mip_dir: Path) -> bytes:
    """community -> its countries and the matching AfS citizenship labels (per-country Berlin counts)."""
    import yaml

    com = yaml.safe_load((mip_dir / COMMUNITIES_FILE).read_text())["communities"]
    out = {
        cid: {"countries": c.get("countries") or [], "afs_citizenship": c.get("afs_citizenship") or []}
        for cid, c in com.items()
    }
    return yaml.safe_dump(out, allow_unicode=True, sort_keys=True).encode()


def run_market_pull(dsn: str, mip_dir: Path, market_dir: Path, keep: int, log=print) -> Path:
    return run_pull(
        dsn,
        market_dir,
        keep=keep,
        log=log,
        items=ITEMS,
        extra_files={
            "cues.yaml": cue_dictionary_bytes(mip_dir),
            "content_rules.yaml": content_rules_bytes(mip_dir),
            "community_countries.yaml": community_countries_bytes(mip_dir),
        },
        application_name="dk-intelligence-market",
    )
