"""Clean third-party and own text before it is stored or embedded (architecture §4.3): minimise first.

Order: normalise → neutralise links → mask emails, IBANs, phone numbers, private @handles → flag injection-shaped
text. Masking is deterministic and counted; the counts go into the pack manifest. No NER model: first names inside
review text are a known residual risk (ADR 0027), so quotes stay short.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

CLEANER_VERSION = "1"

CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b")
# +49 / 0049 international, and German national formats such as 030 1234567, 0176-12345678, (030) 123 456
PHONE = re.compile(r"(?<![\w+])(?:(?:\+|00)\d{1,3}[\s./-]?)?\(?0?\d{2,5}\)?[\s./-]?\d{3,}(?:[\s./-]?\d{2,})*(?!\w)")
DATE = re.compile(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}")
TIME_RANGE = re.compile(r"\d{1,2}[:.]\d{2}\s?[-–]\s?\d{1,2}[:.]\d{2}")
HANDLE = re.compile(r"(?<![\w@])@([A-Za-z0-9_.]{2,30})")
INJECTION = re.compile(
    r"(ignore (all |the )?(previous|prior|above)|disregard (the |all )?(previous|above)|system prompt|"
    r"you are (now )?(chatgpt|claude|an? (ai|assistant))|</?(system|assistant|user)>|\btool[_ ]?call\b|"
    r"score_drafts|search_reviews|get_facts|vergiss (alle|die) (vorherigen|bisherigen))",
    re.I,
)


@dataclass
class Cleaned:
    text: str
    suspicious: bool
    masks: Counter[str] = field(default_factory=Counter)


def _digits(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


def clean(text: str, allowed_handles: frozenset[str] = frozenset()) -> Cleaned:
    masks: Counter[str] = Counter()
    t = unicodedata.normalize("NFKC", text or "")
    t = CONTROL.sub("", t)
    t, n = MD_IMAGE.subn(r"\1", t)
    masks["image"] += n
    t, n = MD_LINK.subn(r"\1", t)
    masks["md_link"] += n
    t, n = EMAIL.subn("[email]", t)
    masks["email"] += n
    t, n = URL.subn("[link]", t)
    masks["link"] += n
    t, n = IBAN.subn("[iban]", t)
    masks["iban"] += n

    def phone(m: re.Match[str]) -> str:
        s = m.group(0).strip()
        if DATE.fullmatch(s) or TIME_RANGE.fullmatch(s):
            return m.group(0)
        if _digits(s) >= 7:  # prices and years have fewer digits
            masks["phone"] += 1
            return "[phone]"
        return m.group(0)

    t = PHONE.sub(phone, t)

    def handle(m: re.Match[str]) -> str:
        if m.group(1).lower() in allowed_handles:
            return m.group(0)
        masks["handle"] += 1
        return "@[user]"

    t = HANDLE.sub(handle, t)
    t = re.sub(r"[ \t]+", " ", t).strip()
    return Cleaned(text=t, suspicious=bool(INJECTION.search(t)), masks=+masks)


def residual_pii(text: str) -> list[str]:
    """A second, independent scan used by the activation gate: anything still looking like contact data."""
    found = []
    if re.search(r"\S+@\S+\.\w{2,}", text):
        found.append("email")
    if re.search(r"https?://|www\.", text, re.I):
        found.append("link")
    if re.search(r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{12,}\b", text):
        found.append("iban")
    if any(_digits(m) >= 9 for m in re.findall(r"[\d][\d\s()./+-]{7,}\d", text)):
        found.append("phone")
    return found
