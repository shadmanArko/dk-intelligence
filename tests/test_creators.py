"""Creator matcher: business detection, size bands, eligibility rules, ranking, page."""

import datetime as dt

import pandas as pd
import pytest

from intel.models.creator_matcher.run import is_business, score, size_band, summarise

TODAY = dt.date(2026, 10, 8)


@pytest.mark.parametrize(
    "handle,name,bio,biz",
    [
        ("goltzkebap", "Goltz Kebap", "", True),
        ("44chkn", "", "", True),
        ("wolt.deutschland", "", "", True),
        ("visitberlin", "", "", True),
        ("naomisberlinfood", "Naomi", "food lover in Berlin", False),
        ("anandberlin", "Anand", "Berlin food & life", False),
        ("someone", "", "Öffnungszeiten Mo-Fr 12-22", True),
    ],
)
def test_business_detection(handle, name, bio, biz):
    assert is_business({"handle": handle, "display_name": name, "bio": bio, "is_business": True}) == biz


def test_size_bands_use_typical_views():
    assert size_band(500)["label"].startswith("under") and size_band(5000)["factor"] == 1.0
    assert size_band(2_000_000)["factor"] == 0.4 and size_band(None)["label"].startswith("under")


def creators():
    base = {
        "display_name": None,
        "bio": "",
        "website": None,
        "is_business": True,
        "link_score": None,
        "link_decision": None,
        "posts_seen": 100,
        "measured_posts": 100,
        "followers": None,
        "last_post": pd.Timestamp("2026-10-01", tz="UTC"),
        "rice_dish_posts": 0,
        "biryani_posts": 0,
        "south_asian_posts": 0,
        "meat_rice_posts": 0,
        "halal_posts": 0,
        "review_posts": 0,
        "street_food_posts": 0,
    }
    rows = [
        {
            "account_id": "a1",
            "handle": "biryanibuddy",
            "berlin_posts": 90,
            "posts_90d": 20,
            "median_views": 20000,
            "median_engagement_rate": 0.06,
            "food_posts": 60,
            "biryani_posts": 12,
            "south_asian_posts": 30,
        },
        {
            "account_id": "a2",
            "handle": "berlinwalks",
            "berlin_posts": 95,
            "posts_90d": 30,
            "median_views": 8000,
            "median_engagement_rate": 0.05,
            "food_posts": 3,
        },  # 3% food -> not eligible
        {
            "account_id": "a3",
            "handle": "kebapking",
            "berlin_posts": 99,
            "posts_90d": 40,
            "median_views": 9000,
            "median_engagement_rate": 0.09,
            "food_posts": 90,
        },  # a business
        {
            "account_id": "a4",
            "handle": "oldfoodie",
            "berlin_posts": 80,
            "posts_90d": 0,
            "median_views": 9000,
            "median_engagement_rate": 0.07,
            "food_posts": 70,
            "last_post": pd.Timestamp("2025-01-01", tz="UTC"),
        },
        {
            "account_id": "a5",
            "handle": "streetfoodnina",
            "berlin_posts": 70,
            "posts_90d": 10,
            "median_views": 3000,
            "median_engagement_rate": 0.04,
            "food_posts": 50,
            "street_food_posts": 20,
        },
    ]
    return pd.DataFrame([base | r for r in rows])


def test_eligibility_and_ranking():
    df = score(creators(), TODAY).set_index("handle")
    assert df.loc["biryanibuddy", "eligible"] and df.loc["streetfoodnina", "eligible"]
    assert not df.loc["berlinwalks", "eligible"]  # too little food
    assert not df.loc["kebapking", "eligible"]  # business
    assert not df.loc["oldfoodie", "eligible"]  # inactive for > 120 days
    assert df.loc["biryanibuddy", "score"] > df.loc["streetfoodnina", "score"]


def test_summary_and_page():
    from intel.serve.views import creators_html

    posts = pd.DataFrame(
        {
            "account_id": ["a1"],
            "permalink": ["https://www.tiktok.com/@biryanibuddy/video/1"],
            "caption": ["Best kacchi in Berlin?"],
            "posted_at": [pd.Timestamp("2026-09-01", tz="UTC")],
            "views": [50000],
            "likes": [3000],
        }
    )
    ins = summarise(score(creators(), TODAY), posts)
    assert ins["top"][0]["handle"] == "biryanibuddy" and ins["top"][0]["examples"][0]["views"] == 50000
    assert [c["handle"] for c in ins["south_asian_specialists"]] == ["biryanibuddy"]
    assert [b["handle"] for b in ins["businesses"]] == ["kebapking"]
    page = creators_html({"version": "cr-1", "insights": ins})
    assert "@biryanibuddy" in page and "Werbung" in page and "Restaurants and brands" in page
