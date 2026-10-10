"""Turn sources into chunks: one review = one chunk; documents split by heading (architecture §4.0, §4.2).

Every chunk carries a stable citation id (prefix + 8 Crockford base32 characters of sha256(collection|source_id)),
its metadata for filters, and an `embed_text`: a deterministic context header (place, district, rating, date, or the
document and section) followed by the text. The header is embedded, not shown, so a chunk "knows" where it is from.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from intel.rag.clean import clean

CHUNKER_VERSION = "1"
PREFIX = {"reviews": "R", "brand": "B", "rules": "U", "own_posts": "P", "creator_posts": "C"}
CROCKFORD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567", "0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def cite_id(collection: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{collection}|{source_id}".encode()).digest()
    return f"{PREFIX[collection]}-{base64.b32encode(digest).decode().translate(CROCKFORD)[:8]}"


@dataclass
class Chunk:
    collection: str
    cite: str
    source_id: str
    text: str
    embed_text: str
    lang: str | None = None
    posted_at: pd.Timestamp | None = None
    platform: str | None = None
    district: str | None = None
    area: str | None = None
    listing_id: str | None = None
    listing_name: str | None = None
    rating_norm: float | None = None
    aspects: list[str] = field(default_factory=list)
    title: str | None = None
    suspicious: bool = False
    dup_count: int = 1
    masks: dict[str, int] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "masks"}


def _stars(rating_norm: float | None) -> str:
    return "unknown rating" if rating_norm is None else f"rating {rating_norm:.2f} of 1"


def review_chunks(reviews: pd.DataFrame, listings: pd.DataFrame) -> list[Chunk]:
    """Competitor reviews joined to their listing; identical texts at the same place collapse into one chunk."""
    from intel.models.weakness_miner.mine import analyse_text  # sealed code: imported, never edited

    by_id = listings.set_index("listing_id")
    groups: dict[tuple[str, str], list[pd.Series]] = {}
    for _, r in reviews.sort_values("posted_at").iterrows():
        key = (str(r["listing_id"]), re.sub(r"\W+", " ", str(r["text"]).casefold()).strip())
        groups.setdefault(key, []).append(r)
    out: list[Chunk] = []
    for (listing_id, _), rows in groups.items():
        r = rows[-1]  # the most recent copy represents the group
        c = clean(str(r["text"]))
        rating = float(r["rating_norm"]) if r["rating_norm"] not in (None, "") else None
        place = by_id.loc[listing_id] if listing_id in by_id.index else None
        name = None if place is None or pd.isna(place["name"]) else str(place["name"])
        district = None if place is None or pd.isna(place["district"]) else str(place["district"])
        area = None if place is None or pd.isna(place["planning_area"]) else str(place["planning_area"])
        aspects = sorted({a for clause in analyse_text(c.text, rating) for a in clause["aspects"]})
        posted = pd.Timestamp(r["posted_at"])
        header = (
            f"Kundenbewertung / customer review · {name or 'unknown place'} · {district or ''}"
            f"{' / ' + area if area else ''} · {_stars(rating)} · {posted:%Y-%m}"
        )
        out.append(
            Chunk(
                collection="reviews",
                cite=cite_id("reviews", str(r["review_id"])),
                source_id=str(r["review_id"]),
                text=c.text,
                embed_text=f"{header}\n{c.text}",
                lang=None if pd.isna(r["lang"]) else str(r["lang"]),
                posted_at=posted,
                platform=str(r["platform"]),
                district=district,
                area=area,
                listing_id=listing_id,
                listing_name=name,
                rating_norm=rating,
                aspects=aspects,
                suspicious=c.suspicious,
                dup_count=len(rows),
                masks=dict(c.masks),
            )
        )
    return out


HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.M)


def _sections(markdown: str) -> Iterator[tuple[str, str]]:
    """(heading path, body) per section; front matter dropped; long sections split at blank lines (~1200 chars)."""
    body = re.sub(r"\A---\n.*?\n---\n", "", markdown, flags=re.S)
    marks = list(HEADING.finditer(body))
    path: list[str] = []
    for i, m in enumerate(marks):
        level, title = len(m.group(1)), m.group(2).strip()
        path = [*path[: level - 1], title]
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        text = body[m.end() : end].strip()
        if not text:
            continue
        part: list[str] = []
        size = 0
        for para in re.split(r"\n\s*\n", text):
            if size + len(para) > 1200 and part:
                yield " > ".join(path), "\n\n".join(part)
                part, size = [], 0
            part.append(para)
            size += len(para)
        if part:
            yield " > ".join(path), "\n\n".join(part)


def document_chunks(collection: str, documents: Iterable[tuple[str, str]]) -> list[Chunk]:
    """Markdown documents (name, text) split by heading: the brand book (public) or voice and rules (internal)."""
    out = []
    for name, text in documents:
        for n, (path, body) in enumerate(_sections(text)):
            c = clean(body)
            source_id = f"{name}#{n:03d}"
            out.append(
                Chunk(
                    collection=collection,
                    cite=cite_id(collection, source_id),
                    source_id=source_id,
                    text=c.text,
                    embed_text=f"{name} · {path}\n{c.text}",
                    title=f"{name} · {path}",
                    suspicious=c.suspicious,
                    masks=dict(c.masks),
                )
            )
    return out
