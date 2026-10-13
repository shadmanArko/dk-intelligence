"""Performance learning (S4): one key per post across platforms, growth-normalised results, honest intervals, the
playbook, and our own posts joined to their metrics. Synthetic data with planted effects; no network."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from intel.performance import features, playbook, stats
from intel.performance.build import build_dataset, idea_features, join_agent_posts
from intel.performance.urls import post_key_of

UTC = dt.UTC


# ---------- one key per post ----------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.instagram.com/reel/DcVhDg5I06C/", ("instagram", "DcVhDg5I06C")),
        ("https://www.instagram.com/p/DcVhDg5I06C/?utm=x", ("instagram", "DcVhDg5I06C")),  # a reel is /p/ elsewhere
        ("https://www.facebook.com/reel/1404801384955724/", ("facebook", "1404801384955724")),
        ("https://www.facebook.com/122106931838489820/posts/122103513578489820", ("facebook", "122103513578489820")),
        ("https://www.facebook.com/permalink.php?story_fbid=99887766&id=1", ("facebook", "99887766")),
        ("https://www.threads.com/@dhakakacchi/post/DZ-FKjOCGMv", ("threads", "DZ-FKjOCGMv")),
        ("https://www.threads.net/@dhakakacchi/post/DZ-FKjOCGMv", ("threads", "DZ-FKjOCGMv")),  # .com or .net
        ("https://www.youtube.com/shorts/-LqhlADng8o", ("youtube", "-LqhlADng8o")),
        ("https://www.youtube.com/watch?v=Qmd82R-T8RA&t=3", ("youtube", "Qmd82R-T8RA")),
        ("https://youtu.be/Qmd82R-T8RA", ("youtube", "Qmd82R-T8RA")),
        ("https://example.com/x", None),
        (None, None),
    ],
)
def test_every_way_a_platform_writes_a_post_address_reduces_to_one_key(url, expected):
    assert post_key_of(url) == expected


# ---------- what we know before posting ----------


def test_the_posting_window_is_berlin_time_across_the_clock_change():
    assert features.band_of(dt.datetime(2026, 10, 13, 15, 0, tzinfo=UTC)) == "pre_dinner"  # 17:00 CEST
    assert features.band_of(dt.datetime(2026, 10, 27, 16, 0, tzinfo=UTC)) == "pre_dinner"  # 17:00 CET, after the change
    assert features.band_of(dt.datetime(2026, 10, 13, 9, 30, tzinfo=UTC)) == "lunch"
    assert features.band_of(dt.datetime(2026, 10, 13, 3, 0, tzinfo=UTC)) == "other"


def test_caption_features_describe_the_words_never_a_result():
    f = features.caption_features("Samstag gibt es Kacchi in Berlin 🍛\nSlow-cooked. Bestell jetzt?\n#a #b #c")
    assert (f["hashtags"], f["emojis"], f["asks_question"], f["calls_to_action"]) == ("1-3", "1-2", "yes", "yes")
    assert (f["german_first_line"], f["bilingual"], f["first_line"]) == ("yes", "yes", "short (up to 40)")
    empty = features.caption_features(None)  # a post with no caption (the warehouse has NaN there)
    assert empty["caption_length"] == "none" and empty["hashtags"] == "none"
    assert features.post_features(dt.datetime(2026, 10, 13, 15, 0, tzinfo=UTC), float("nan"), None)["format"] == "text"


# ---------- the dataset: growth-normalised, mature posts only ----------


def _pull(tmp_path: Path, n: int = 60, grow: bool = True) -> Path:
    """n Instagram posts, one a day. Reach grows 1% a day (an account growing); reels get 2x; the last 3 are young."""
    rows, snaps = [], []
    start = dt.datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
    for i in range(n):
        posted = start + dt.timedelta(days=i)
        reel = i % 2 == 0
        reach = int(100 * (1.01**i if grow else 1) * (2 if reel else 1))
        pid = f"p{i:03d}"
        rows.append({"id": pid, "platform": "instagram", "posted_at": posted,
                     "permalink": f"https://www.instagram.com/{'reel' if reel else 'p'}/C{i:04d}/",
                     "content_type": "reel" if reel else "image",
                     "caption": "Kacchi in Berlin 🍛" if i % 3 else "Samstag Kacchi\nin Berlin"})  # fmt: skip
        age = 2 if i >= n - 3 else 10
        snaps.append(
            {
                "social_post_id": pid,
                "captured_at": posted + dt.timedelta(days=age),
                "likes": reach // 20,
                "comments": 1,
                "shares": 0,
                "saves": 0,
                "impressions": reach * 2,
                "reach": reach,
                "views": None,
            }
        )
    d = tmp_path / "pull"
    d.mkdir()
    pd.DataFrame(rows).to_parquet(d / "social_post.parquet")
    pd.DataFrame(snaps).to_parquet(d / "social_metrics_snapshot.parquet")
    return d


def test_lift_compares_a_post_with_its_own_era_so_growth_is_not_mistaken_for_quality(tmp_path):
    df = build_dataset(_pull(tmp_path))
    old, new = df[df["matured"]].iloc[8:20], df[df["matured"]].iloc[-12:]
    assert old["lift_audience"].median() == pytest.approx(new["lift_audience"].median(), rel=0.35)  # not 1.5x apart
    assert new["audience"].median() > old["audience"].median() * 1.2  # while the raw reach did grow
    young = df[~df["matured"]]
    assert len(young) == 3 and young["lift_audience"].isna().all()  # a post of 2 days is not judged yet
    assert df["key"].iloc[0] == "instagram:C0000" and df["window"].iloc[0] == "pre_dinner"


def test_a_planted_effect_is_found_noise_is_not_and_small_groups_are_not_judged():
    rng = np.random.default_rng(1)
    n = 80
    df = pd.DataFrame({"fmt": ["reel"] * 40 + ["image"] * 40,
                       "lift": np.r_[rng.normal(2.0, 0.3, 40), rng.normal(1.0, 0.3, 40)],
                       "noise": rng.choice(["a", "b"], n), "rare": ["x"] * 5 + ["y"] * 75})  # fmt: skip
    df["lift"] = df["lift"].clip(0.1)
    found = {(f["feature"], f["value"]): f for f in stats.findings(df, ("fmt", "noise", "rare"), "lift")}
    assert found[("fmt", "reel")]["verdict"] == "works" and found[("fmt", "image")]["verdict"] == "hurts"
    assert found[("fmt", "reel")]["diff_ci"][0] > 0.5 and found[("fmt", "reel")]["n"] == 40
    assert found[("noise", "a")]["verdict"] == "no_clear_difference"  # a coin flip is not a finding
    assert found[("rare", "x")]["verdict"] == "too_few" and "diff" not in found[("rare", "x")]  # 5 posts: no claim
    assert stats.findings(df, ("fmt",), "lift") == stats.findings(df, ("fmt",), "lift")  # a fixed seed: repeatable


# ---------- the playbook ----------


def _frame(platform: str, n: int, rng: np.random.Generator) -> pd.DataFrame:
    fmt = np.where(np.arange(n) % 2 == 0, "reel", "image")
    lift = np.where(fmt == "reel", 2.0, 0.8) * rng.uniform(0.85, 1.15, n)
    return pd.DataFrame({
        "platform": platform, "matured": True, "engagement": 5.0, "audience": 100.0, "format": fmt,
        "lift_engagement": 1.0, "lift_audience": lift,
        "first_line": np.where(np.arange(n) % 3 == 0, "long (over 60)", "medium (41-60)"),
        "emojis": "none", "weekday": "tue", "window": "lunch", "caption_length": "short (under 100)", "hashtags": "1-3",
        "asks_question": "no", "calls_to_action": "no", "german_first_line": "yes", "bilingual": "yes",
        "mentions_price": "no",
    })  # fmt: skip


def test_the_playbook_says_what_the_numbers_support_and_what_it_cannot(tmp_path):
    rng = np.random.default_rng(3)
    data = pd.concat([_frame("instagram", 80, rng), _frame("youtube", 8, rng)])
    flat = _frame("threads", 60, rng)
    flat["audience"], flat["lift_audience"], flat["lift_engagement"] = (
        0.0,
        1.0,
        1.0,
    )  # Threads: no reach in the warehouse
    pb = playbook.build(pd.concat([data, flat]), "pull1", dt.datetime(2026, 10, 13, tzinfo=UTC))
    ig = pb["platforms"]["instagram"]
    assert ig["enough"] and any("format = reel" in line for line in ig["lines"]["works"])
    assert any("format = image" in line for line in ig["lines"]["hurts"])
    assert not any(
        "long (over 60)" in line for line in ig["lines"]["works"] + ig["lines"]["hurts"]
    )  # the checker blocks it
    assert ig["tests"] > 0 and ig["expected_false_alarms"] == round(0.05 * ig["tests"], 1)
    assert not pb["platforms"]["youtube"]["enough"] and "nothing is claimed" in pb["platforms"]["youtube"]["note"]
    assert "analytics" in pb["platforms"]["tiktok"]["note"]
    assert pb["platforms"]["threads"]["no_outcome"] == ["reach", "engagement"]  # no variation: no statement
    assert "no evidence yet" in playbook.for_prompt(pb, "threads")["status"]
    prompt = playbook.for_prompt(pb, "instagram", works=1, hurts=1)
    assert len(prompt["works"]) == 1 and "not orders" in prompt["status"] and "may be chance" in prompt["status"]
    assert playbook.for_prompt(pb, "tiktok")["status"] == pb["platforms"]["tiktok"]["note"]
    assert playbook.for_prompt(None, "instagram") == {"status": "no playbook yet"}
    folder = playbook.write(tmp_path, pb, data)
    assert playbook.latest(tmp_path) == pb and (folder / "playbook.md").read_text().startswith("# Performance playbook")


def test_language_and_price_findings_are_for_the_owner_never_offered_to_an_agent():
    rng = np.random.default_rng(5)
    d = _frame("instagram", 80, rng)
    d["bilingual"] = np.where(d["format"] == "reel", "no", "yes")  # a planted "effect" that a brand rule decides
    ig = playbook.build(d, "p")["platforms"]["instagram"]
    assert any("two languages" in line for line in ig["lines"]["info"])
    assert not any("two languages" in line for line in ig["lines"]["works"] + ig["lines"]["hurts"])


# ---------- our own posts, joined to their results ----------


def test_published_agent_posts_join_to_their_metrics_by_the_platform_post_id(tmp_path):
    data = build_dataset(_pull(tmp_path))
    calendar: list[dict[str, Any]] = [
        {"post_key": "2026w42-tue13-i1", "platform": "instagram", "state": "published", "slot": "2026-10-13",
         "live_url": "https://www.instagram.com/p/C0030/"},  # the warehouse holds this one as /reel/C0030/
        {"post_key": "2026w42-tue13-i2", "platform": "instagram", "state": "published",
         "live_url": "https://www.instagram.com/p/NOTYETCOLLECTED/"},
        {"post_key": "2026w42-wed14-i1", "platform": "instagram", "state": "awaiting_approval", "live_url": None},
    ]  # fmt: skip
    idea = {"job_id": "2026w42-tue13-i1", "format": "reel", "window": "pre_dinner"}
    ideas = {"ideas": {"d01i1": {"pillar": "food", "angle": "lid off", "idea": idea,
                                 "design": {"spec": {"content": {"hook": "Sealed.", "shots": [{}, {}, {}],
                                                                 "music": {"culture": "desi"}}}},
                                 "captions": {"instagram": "Kacchi in Berlin"}}}}  # fmt: skip
    joined = join_agent_posts(calendar, data, idea_features(ideas))
    assert list(joined["post_key"]) == ["2026w42-tue13-i1", "2026w42-tue13-i2"]  # the unpublished one is not a result
    first, second = joined.iloc[0], joined.iloc[1]
    assert first["collected"] and first["lift_audience"] > 0 and first["pillar"] == "food" and first["hook_length"] == 7
    assert not second["collected"]  # published, but the warehouse has not collected it yet: kept, with no result
    assert first["cap_hashtags"] == "none" and first["shots"] == 3
