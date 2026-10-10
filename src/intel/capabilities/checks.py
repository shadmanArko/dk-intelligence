"""check_drafts: deterministic brand, legal and platform checks on drafts (rules v1, architecture §2.5).

The playbook is the source of truth where documents conflict (decision D4). Blocked words are reported as a
character span and never echoed back. Every rule has a passing and a failing example in tests/test_mcp_brand.py.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from intel.capabilities.brand import CAPTION_LIMIT, EMOJI_MAX, FIRST_LINE_MAX, get_facts, platform_formats
from intel.capabilities.ports import BrandPack, BrandSource
from intel.capabilities.types import Draft, ToolProblem, prov, refs

RULES_VERSION = "1.0"
MAX_DRAFTS = 20
Severity = Literal["block", "warn"]


@dataclass(frozen=True)
class Rule:
    severity: Severity
    message: str
    fix: str


RULES: dict[str, Rule] = {
    "BLOCKED_WORD": Rule(
        "block", "a blocked word (religious ritual or deity word)", "talk about the festive season, not the ritual"
    ),
    "MISSING_KACCHI": Rule("warn", "no 'Kacchi' in the caption", "name the dish: Kacchi Biryani"),
    "MISSING_BERLIN": Rule("warn", "no 'Berlin' in the caption", "add Berlin"),
    "FIRST_LINE_LONG": Rule("warn", f"first line over {FIRST_LINE_MAX} characters", "shorten the hook"),
    "LENGTH_OVER_API": Rule("block", "longer than the platform allows", "shorten the caption"),
    "HASHTAGS": Rule("warn", "hashtag count outside the playbook range", "use the playbook's range"),
    "THREADS_TAG_FORMAT": Rule("warn", "Threads uses one topic tag without '#'", "drop the '#'"),
    "EMOJIS": Rule("warn", f"more than {EMOJI_MAX} emojis", f"keep {EMOJI_MAX} or fewer"),
    "LANG_ORDER": Rule("warn", "no German line first", "start with a German line, then one English line"),
    "WERBUNG_MISSING": Rule("block", "a paid post must start with 'Werbung' or 'Anzeige'", "put 'Werbung' first"),
    "PRICE_UNCONFIRMED": Rule(
        "block", "a price the owner hasn't confirmed", "leave the price out or confirm it in facts.yaml"
    ),
    "PRICE_MISMATCH": Rule("block", "a price that isn't in facts.yaml", "use get_facts for prices"),
    "SUPERLATIVE": Rule("warn", "an unproven superlative (§5 UWG)", "describe what makes it special instead"),
    "ONLY_CLAIM": Rule("block", "an 'only one in Berlin' claim the owner hasn't confirmed", "drop the claim"),
    "HALAL_UNCERTIFIED": Rule(
        "block", "'halal' without a named certifier (§5 UWG)", "say what the facts allow, or leave it out"
    ),
    "HEALTH_CLAIM": Rule("block", "a health claim (EU 1924/2006)", "describe taste and craft instead"),
    "ALLERGEN_CLAIM": Rule(
        "block", "an allergen statement", "never state allergens in posts; customers ask us directly"
    ),
    "SALES_ON_SOLEMN_DAY": Rule("block", "sales wording on a solemn day", "post a respectful message only"),
    "LINKEDIN_LINK": Rule("warn", "an external link in a LinkedIn post", "put the link in the first comment"),
}

GERMAN = re.compile(r"[äöüß]|\b(und|der|die|das|mit|für|jetzt|bestell\w*|samstag|frisch|lecker|heute|wir|ihr)\b", re.I)
PRICE = re.compile(r"(?:€\s?(\d{1,3}(?:[.,]\d{1,2})?))|(?:(\d{1,3}(?:[.,]\d{1,2})?)\s?(?:€|eur\b|euro\b))", re.I)
SUPERLATIVE = re.compile(
    # "best in Berlin", also with words between ("best biryani in Berlin", "beste Kacchi Berlins")
    r"\b(nr\.?\s?1|no\.?\s?1|number one|best(?:e[nrs]?)?(?:\s+[^\s.!?]+){0,5}?\s+berlin\w*|der beste|"
    r"die beste|das beste|world'?s best|weltbeste\w*)\b",
    re.I,
)
ONLY = re.compile(r"(berlin'?s only|only\b.{0,40}\bin berlin|einzig\w*)", re.I)
HEALTH = re.compile(r"\b(gesund\w*|healthy|protein\w*|low[- ]fat|kalorienarm|superfood|detox|fettarm)\b", re.I)
ALLERGEN = re.compile(
    r"(allergen\w*|allergiker|gluten[- ]?(frei|free)|laktose[- ]?frei|lactose[- ]free|nuss[- ]?frei|nut[- ]free)", re.I
)
SALES = re.compile(r"(bestell\w*|order now|angebot|rabatt|discount|\bsale\b|\bdeal\b|%|€|\bpreis\w*)", re.I)
HASHTAG = re.compile(r"(?<!\w)#\w+")
URL = re.compile(r"https?://", re.I)
NON_LATIN = re.compile(r"[^\x00-ɏ]")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _emojis(text: str) -> int:
    return sum(1 for ch in text if ord(ch) >= 0x1F300 or 0x2600 <= ord(ch) <= 0x27BF)


def blocked_spans(text: str, words: list[str]) -> list[tuple[int, int]]:
    norm = _norm(text)
    spans: list[tuple[int, int]] = []
    for w in words:
        word = _norm(str(w))
        if not word:
            continue
        # Latin words match as whole words; other scripts (no word boundaries in \b's sense) as substrings.
        pattern = re.escape(word) if NON_LATIN.search(word) else rf"(?<!\w){re.escape(word)}(?!\w)"
        spans.extend((m.start(), m.end()) for m in re.finditer(pattern, norm))
    return sorted(set(spans))


def _known_prices(source: BrandSource) -> dict[float, str]:
    """Every price in facts.yaml -> its status (confirmed / unconfirmed)."""
    prices: dict[float, str] = {}
    for item in get_facts(source)["items"]:
        key, value = item["key"], item["value"]
        if (key.endswith(".price") or key.endswith(".fee")) and isinstance(value, int | float):
            prices[round(float(value), 2)] = item["status"]
    return prices


def _hashtag_range(pack: BrandPack, platform: str, fmt: str | None) -> tuple[int, int] | None:
    formats = pack.playbook.get("formats") or {}
    chosen = fmt or next(iter(platform_formats(pack, platform)), None)
    spec = formats.get(chosen) if chosen else None
    rng = (spec or {}).get("hashtags")
    return (int(rng[0]), int(rng[1])) if isinstance(rng, list) and len(rng) == 2 else None


def check_one(d: Draft, pack: BrandPack, prices: dict[float, str], halal_certified: bool) -> dict[str, Any]:
    text = d.caption
    low = _norm(text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    tags = HASHTAG.findall(text)
    found: list[dict[str, Any]] = []

    def hit(code: str, span: tuple[int, int] | None = None, severity: Severity | None = None, detail: str = "") -> None:
        rule = RULES[code]
        v: dict[str, Any] = {
            "code": code,
            "severity": severity or rule.severity,
            "message": rule.message + (f" ({detail})" if detail else ""),
            "fix": rule.fix,
        }
        if span is not None:
            v["span"] = list(span)
        found.append(v)

    for span in blocked_spans(text, list(pack.content_rules.get("blocked_words") or [])):
        hit("BLOCKED_WORD", span)
    if "kacchi" not in low:
        hit("MISSING_KACCHI")
    if "berlin" not in low:
        hit("MISSING_BERLIN")
    if len(first) > FIRST_LINE_MAX:
        hit("FIRST_LINE_LONG", detail=f"{len(first)} characters")
    if len(text) > CAPTION_LIMIT[d.platform]:
        hit("LENGTH_OVER_API", detail=f"{len(text)} > {CAPTION_LIMIT[d.platform]}")
    if d.platform == "threads":
        if tags:
            hit("THREADS_TAG_FORMAT")
    elif (rng := _hashtag_range(pack, d.platform, d.format)) is not None and not rng[0] <= len(tags) <= rng[1]:
        severity: Severity = "block" if d.platform == "instagram" and len(tags) > 5 else "warn"
        hit("HASHTAGS", severity=severity, detail=f"{len(tags)}, playbook {rng[0]}-{rng[1]}")
    if (n := _emojis(text)) > EMOJI_MAX:
        hit("EMOJIS", detail=str(n))
    if not GERMAN.search(first):
        hit("LANG_ORDER")
    if d.paid and not re.match(r"\s*(werbung|anzeige)\b", low):
        hit("WERBUNG_MISSING")
    for pm in PRICE.finditer(text):
        value = round(float((pm.group(1) or pm.group(2)).replace(",", ".")), 2)
        status = prices.get(value)
        if status is None:
            hit("PRICE_MISMATCH", (pm.start(), pm.end()), detail=f"{value:.2f} €")
        elif status != "confirmed":
            hit("PRICE_UNCONFIRMED", (pm.start(), pm.end()), detail=f"{value:.2f} €")
    if m := SUPERLATIVE.search(text):
        hit("SUPERLATIVE", (m.start(), m.end()))
    if m := ONLY.search(text):
        hit("ONLY_CLAIM", (m.start(), m.end()))
    if "halal" in low and not halal_certified:
        hit("HALAL_UNCERTIFIED")
    if m := HEALTH.search(text):
        hit("HEALTH_CLAIM", (m.start(), m.end()))
    if m := ALLERGEN.search(text):
        hit("ALLERGEN_CLAIM", (m.start(), m.end()))
    if d.occasion_tone == "solemn" and (m := SALES.search(text)):
        hit("SALES_ON_SOLEMN_DAY", (m.start(), m.end()))
    if d.platform == "linkedin" and URL.search(text):
        hit("LINKEDIN_LINK")
    return {
        "ok": not any(v["severity"] == "block" for v in found),
        "violations": found,
        "stats": {
            "chars": len(text),
            "first_line_chars": len(first),
            "hashtags": len(tags),
            "emojis": _emojis(text),
            "has_kacchi": "kacchi" in low,
            "has_berlin": "berlin" in low,
        },
    }


def check_drafts(source: BrandSource, drafts: list[Draft]) -> dict[str, Any]:
    if not 1 <= len(drafts) <= MAX_DRAFTS:
        raise ToolProblem("INVALID_ARGUMENT", f"send 1 to {MAX_DRAFTS} drafts, not {len(drafts)}")
    pack = source.load()  # fail closed: no rules, no verdict
    formats = pack.playbook.get("formats") or {}
    for d in drafts:
        if d.format is not None and d.format not in formats:
            raise ToolProblem(
                "INVALID_ARGUMENT", f"unknown format {d.format!r}", f"one of: {', '.join(sorted(formats))}"
            )
    prices = _known_prices(source)
    halal = (pack.facts.get("claims_status") or {}).get("halal") or {}
    certified = bool(halal.get("certified"))
    results = [
        {"ref": ref, **check_one(d, pack, prices, certified)} for ref, d in zip(refs(drafts), drafts, strict=True)
    ]
    return {"rules_version": RULES_VERSION, "results": results, "prov": prov("knowledge pack (brand)", pack.pack_id)}
