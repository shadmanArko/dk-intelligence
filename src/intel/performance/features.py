"""Features known BEFORE a post goes out (rule 9): when it was posted, its format, and what its caption looks like.
Nothing here uses a result. The same function describes a historical post and a post the studio is about to make."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any
from zoneinfo import ZoneInfo

from intel.capabilities.checks import GERMAN

BERLIN = ZoneInfo("Europe/Berlin")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HASHTAG = re.compile(r"(?<!\w)#\w+")
QUESTION = re.compile(r"\?")
CTA = re.compile(r"\b(bestell\w*|order|dm us|schreib\w*|write us|link in bio|jetzt|now|reserv\w*|buchen)\b", re.I)
PRICE = re.compile(r"€|\beur(o)?\b", re.I)


def band_of(moment: dt.datetime) -> str:
    """The posting window in Berlin time (the owner's playbook windows, a little wider): lunch, pre_dinner, evening, or
    other."""
    local = moment.astimezone(BERLIN)
    minutes = local.hour * 60 + local.minute
    if 10 * 60 + 30 <= minutes < 12 * 60 + 30:
        return "lunch"
    if 16 * 60 <= minutes < 18 * 60:
        return "pre_dinner"
    if 19 * 60 <= minutes < 22 * 60:
        return "evening"
    return "other"


def _bucket(value: float, edges: tuple[float, ...], labels: tuple[str, ...]) -> str:
    for edge, label in zip(edges, labels, strict=False):
        if value <= edge:
            return label
    return labels[-1]


def _emojis(text: str) -> int:
    return sum(1 for ch in text if ord(ch) >= 0x1F300 or 0x2600 <= ord(ch) <= 0x27BF)


def caption_features(caption: str | None) -> dict[str, Any]:
    text = caption.strip() if isinstance(caption, str) else ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    tags = len(HASHTAG.findall(text))
    return {
        "caption_length": _bucket(
            len(text), (0, 99, 249), ("none", "short (under 100)", "medium (100-249)", "long (250+)")
        )
        if text
        else "none",
        "first_line": _bucket(len(first), (0, 40, 60), ("none", "short (up to 40)", "medium (41-60)", "long (over 60)"))
        if first
        else "none",
        "hashtags": _bucket(tags, (0, 3, 5), ("none", "1-3", "4-5", "6 or more")),
        "emojis": _bucket(_emojis(text), (0, 2), ("none", "1-2", "3 or more")),
        "asks_question": "yes" if QUESTION.search(text) else "no",
        "calls_to_action": "yes" if CTA.search(text) else "no",
        "german_first_line": "yes" if first and GERMAN.search(first) else "no",
        "bilingual": "yes" if len(lines) >= 2 else "no",
        "mentions_price": "yes" if PRICE.search(text) else "no",
    }


def post_features(posted_at: dt.datetime, content_type: str | None, caption: str | None) -> dict[str, Any]:
    local = posted_at.astimezone(BERLIN)
    return {
        "weekday": WEEKDAYS[local.weekday()],
        "window": band_of(posted_at),
        "format": content_type if isinstance(content_type, str) and content_type else "text",
        **caption_features(caption),
    }


FEATURES = ("format", "weekday", "window", "caption_length", "first_line", "hashtags", "emojis", "asks_question",
            "calls_to_action", "german_first_line", "bilingual", "mentions_price")  # fmt: skip
