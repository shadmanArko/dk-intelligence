"""dk_brand: the knowledge pack (build, verify, activate, fail closed), facts, and every check_drafts rule.

The fixture pack is synthetic (made-up blocked words), so no real blocked word is written into the repo.
"""

import datetime as dt
from pathlib import Path

import pytest

from intel.adapters.pack import PackBrandSource
from intel.capabilities import brand, checks
from intel.capabilities.types import Draft, ToolProblem
from intel.rag import pack as kp

PLAYBOOK = """
brand:
  voice: warm host
  always_say: [Kacchi, Berlin]
  base_languages: [de, en]
  never: ["say 'halal' unless certified"]
  paid_partnership_label: Werbung / Anzeige
platforms:
  instagram: {daily: [{format: reel, weekdays: [1, 2], window: pre_dinner}]}
  facebook: {daily: [{format: reel, weekdays: [1], window: lunch}]}
  threads: {daily: [{format: text_photo, weekdays: [1], window: lunch}]}
  linkedin: {daily: [{format: multi_image, weekdays: [1], window: lunch}]}
  tiktok: {daily: [{format: short_video, weekdays: [1], window: evening}]}
formats:
  reel: {ratio: "9:16", hashtags: [3, 5]}
  short_video: {ratio: "9:16", hashtags: [3, 5]}
  text_photo: {ratio: "4:5", hashtags: [0, 1]}
  multi_image: {ratio: "4:5", hashtags: [0, 3]}
windows: {lunch: "11:00-12:00", pre_dinner: "16:30-17:30", evening: "19:30-21:00"}
hashtags: {core: [kacchibiryani, berlinfood], occasion: {x: [y]}}
no_occasion_platforms: [linkedin]
"""
RULES = "blocked_words: [forbiddenword, 禁词]\n"
FACTS = """version: 2026-10-06-draft
menu:
  - id: box
    name: Kacchi Box
    price: 15.00
  - id: taster
    name: Taster
    price: 9.99                   # [CONFIRM] still offered?
  - id: chutney
    name: Chutney
    price: null
fulfilment:
  home_delivery:
    fees: [{max_km: 5, fee: 5.00}]
claims_status:
  halal: { say: "halal mutton", certified: null }
"""


def _sources(root: Path) -> tuple[Path, Path]:
    mip = root / "mip"
    (mip / "docs" / "brand").mkdir(parents=True)
    (mip / "config" / "content").mkdir(parents=True)
    (mip / "docs" / "brand" / "brand-book.md").write_text("# Brand book\n[CONFIRM] one\n")
    (mip / "docs" / "brand" / "voice-and-rules.md").write_text("# Voice\n")
    (mip / "docs" / "brand" / "facts.yaml").write_text(FACTS)
    (mip / "config" / "content" / "playbook.yaml").write_text(PLAYBOOK)
    market = root / "market"
    (market / "20261009T000000Z").mkdir(parents=True)
    (market / "20261009T000000Z" / "content_rules.yaml").write_text(RULES)
    (market / "LATEST").write_text("20261009T000000Z\n")
    return mip, market


@pytest.fixture
def source(tmp_path) -> PackBrandSource:
    mip, market = _sources(tmp_path)
    packs = tmp_path / "knowledge"
    pack_id = kp.build_brand_pack(mip, market, packs, now=dt.datetime(2026, 10, 9, tzinfo=dt.UTC))
    kp.activate(packs, pack_id)
    return PackBrandSource(packs)


# ---------- the pack ----------


def test_pack_is_immutable_checksummed_and_activated_atomically(tmp_path):
    mip, market = _sources(tmp_path)
    packs = tmp_path / "knowledge"
    now = dt.datetime(2026, 10, 9, tzinfo=dt.UTC)
    pack_id = kp.build_brand_pack(mip, market, packs, now=now)
    assert pack_id.startswith("kp-20261009T000000-")
    manifest = kp.verify(packs / pack_id)
    assert manifest["market_snapshot"] == "20261009T000000Z" and len(manifest["files"]) == 5
    with pytest.raises(kp.PackError, match="already exists"):
        kp.build_brand_pack(mip, market, packs, now=now)
    assert kp.status(packs) == {"active": None, "packs": [pack_id]}
    kp.activate(packs, pack_id)
    assert kp.active_id(packs) == pack_id


def test_a_tampered_or_missing_pack_fails_closed(source, tmp_path):
    assert source.load().pack_id
    f = next((tmp_path / "knowledge").glob("kp-*/brand/facts.yaml"))
    f.chmod(0o644)
    f.write_text("version: forged\n")
    fresh = PackBrandSource(tmp_path / "knowledge")
    with pytest.raises(ToolProblem) as e:
        fresh.load()
    assert e.value.code == "BRAND_UNAVAILABLE"
    with pytest.raises(ToolProblem) as e:
        checks.check_drafts(fresh, [Draft(platform="instagram", caption="x")])
    assert e.value.code == "BRAND_UNAVAILABLE"
    with pytest.raises(ToolProblem):
        PackBrandSource(tmp_path / "nothing").load()


# ---------- facts and rules ----------


def test_facts_statuses_are_never_guessed(source):
    items = {i["key"]: i for i in brand.get_facts(source)["items"]}
    assert items["menu.box.price"]["status"] == "confirmed"
    assert items["menu.taster.price"]["status"] == "unconfirmed"
    assert items["menu.chutney.price"]["status"] == "unknown" and items["menu.chutney.price"]["value"] is None
    assert items["claims_status.halal.certified"]["status"] == "unknown"
    out = brand.get_facts(source, ["menu"])
    assert all(i["key"].startswith("menu.") for i in out["items"]) and out["never_answer"] == ["allergens"]
    assert out["status"] == "DRAFT"
    with pytest.raises(ToolProblem):
        brand.get_facts(source, ["recipes"])


def test_brand_rules_for_a_platform_point_to_check_drafts(source):
    out = brand.brand_rules(source, platform="instagram")
    assert out["platform"]["formats"] == {"reel": {"ratio": "9:16", "hashtags": [3, 5]}}
    assert out["platform"]["caption_max_chars"] == 2200 and "check_drafts" in out["blocked_words"]
    assert out["open_questions_count"] == 2  # one in the brand book, one in facts.yaml
    assert "forbiddenword" not in str(out)
    with pytest.raises(ToolProblem):
        brand.brand_rules(source, format="hologram")
    assert brand.brand_rules(source, platform="linkedin")["platform"]["occasions_allowed"] is False


# ---------- every check_drafts rule: one failing and one passing example ----------

GOOD = "Samstag gibt es frische Kacchi in Berlin.\nSlow-cooked dum. #kacchibiryani #berlinfood #biryaniberlin"
CASES = [
    ("BLOCKED_WORD", {"caption": GOOD + " forbiddenword"}, {}),
    ("BLOCKED_WORD", {"caption": GOOD + " 禁词"}, {}),
    ("MISSING_KACCHI", {"caption": GOOD.replace("Kacchi", "Biryani").replace("#kacchibiryani", "#biryani")}, {}),
    (
        "MISSING_BERLIN",
        {
            "caption": GOOD.replace("in Berlin", "hier")
            .replace("#berlinfood", "#food")
            .replace("#biryaniberlin", "#rice")
        },
        {},
    ),
    (
        "FIRST_LINE_LONG",
        {"caption": "Samstag gibt es frische Kacchi in Berlin, langsam gegart im versiegelten Topf.\n#a #b #c"},
        {},
    ),
    ("LENGTH_OVER_API", {"platform": "threads", "format": None, "caption": "Samstag Kacchi Berlin " + "x" * 500}, {}),
    ("HASHTAGS", {"caption": GOOD + " #a #b #c"}, {}),
    ("THREADS_TAG_FORMAT", {"platform": "threads", "format": None, "caption": "Samstag Kacchi in Berlin #kacchi"}, {}),
    ("EMOJIS", {"caption": GOOD + " 🍛🍛🍛🍛🍛"}, {}),
    ("LANG_ORDER", {"caption": "Fresh Kacchi in Berlin.\n#kacchibiryani #berlinfood #biryaniberlin"}, {}),
    (
        "LANG_ORDER",
        {"caption": "Why mutton? Kacchi needs time in Berlin.\n#kacchibiryani #berlinfood #biryaniberlin"},
        {"caption": "Hammel oder Lamm? Schreibt es uns!\nKacchi in Berlin. #kacchibiryani #berlinfood #biryaniberlin"},
    ),
    ("WERBUNG_MISSING", {"caption": GOOD, "paid": True}, {"caption": "Werbung: " + GOOD, "paid": True}),
    ("PRICE_UNCONFIRMED", {"caption": GOOD + " Taster nur 9,99 €"}, {"caption": GOOD + " Box 15,00 €"}),
    ("PRICE_MISMATCH", {"caption": GOOD + " Box 12,50 €"}, {"caption": GOOD + " Lieferung 5 €"}),
    ("SUPERLATIVE", {"caption": GOOD + " Die beste in Berlin!"}, {}),
    ("SUPERLATIVE", {"caption": GOOD + " Best biryani in Berlin!"}, {"caption": GOOD + " Best enjoyed hot. Berlin"}),
    ("SUPERLATIVE", {"caption": GOOD + " Die beste Kacchi Berlins"}, {}),
    ("ONLY_CLAIM", {"caption": GOOD + " Berlins einziges Old-Dhaka-Kacchi"}, {}),
    ("HALAL_UNCERTIFIED", {"caption": GOOD + " 100% halal"}, {}),
    ("HEALTH_CLAIM", {"caption": GOOD + " und gesund"}, {}),
    ("ALLERGEN_CLAIM", {"caption": GOOD + " glutenfrei"}, {}),
    (
        "SALES_ON_SOLEMN_DAY",
        {"caption": GOOD + " Jetzt bestellen!", "occasion_tone": "solemn"},
        {"caption": GOOD, "occasion_tone": "solemn"},
    ),
    ("LINKEDIN_LINK", {"platform": "linkedin", "format": None, "caption": "Samstag Kacchi Berlin https://x.de"}, {}),
]


def _codes(source, **kw) -> set[str]:
    d = Draft(**{"platform": "instagram", "format": "reel", **kw})
    (r,) = checks.check_drafts(source, [d])["results"]
    return {v["code"] for v in r["violations"]}


@pytest.mark.parametrize(("code", "failing", "passing"), CASES, ids=[c[0] + str(i) for i, c in enumerate(CASES)])
def test_each_rule_fires_on_its_example(source, code, failing, passing):
    assert code in _codes(source, **failing)
    assert code not in _codes(source, **{"caption": GOOD, **passing})


def test_every_rule_has_an_example():
    assert {c[0] for c in CASES} == set(checks.RULES)


def test_a_good_caption_passes_clean_and_blocked_words_are_never_echoed(source):
    (r,) = checks.check_drafts(source, [Draft(platform="instagram", format="reel", caption=GOOD)])["results"]
    assert r["ok"] and r["violations"] == [] and r["stats"]["hashtags"] == 3
    out = checks.check_drafts(source, [Draft(platform="instagram", caption=GOOD + " forbiddenword")])
    (v,) = [v for v in out["results"][0]["violations"] if v["code"] == "BLOCKED_WORD"]
    assert "forbiddenword" not in str(out) and v["span"] and not out["results"][0]["ok"]


def test_instagram_over_five_hashtags_blocks_and_unknown_formats_are_refused(source):
    out = checks.check_drafts(source, [Draft(platform="instagram", caption=GOOD + " #a #b #c")])
    (v,) = [v for v in out["results"][0]["violations"] if v["code"] == "HASHTAGS"]
    assert v["severity"] == "block"
    with pytest.raises(ToolProblem) as e:
        checks.check_drafts(source, [Draft(platform="instagram", format="hologram", caption=GOOD)])
    assert "reel" in e.value.hint
