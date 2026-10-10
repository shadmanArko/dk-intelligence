"""Turn sources into chunks: one review = one chunk; documents split by heading (architecture §4.0, §4.2).

Every chunk carries a stable citation id (prefix + 8 Crockford base32 characters of sha256(collection|source_id)),
its metadata for filters, and an `embed_text`: a deterministic context header (place, district, rating, date, or the
document and section) followed by the text. The header is embedded, not shown, so a chunk "knows" where it is from.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from intel.rag.clean import clean

CHUNKER_VERSION = "2"  # 2: own and creator posts
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
    content_type: str | None = None
    account: str | None = None
    url: str | None = None
    metrics: dict[str, float] | None = None
    suspicious: bool = False
    dup_count: int = 1
    masks: dict[str, int] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "masks"}
        out["metrics"] = None if self.metrics is None else json.dumps(self.metrics, sort_keys=True)
        return out


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


OWN_ACCOUNT = "dhakakacchi"
ENGAGEMENT = ("likes", "comments", "shares", "saves")
MIN_REACH = 50  # below this, 1 like swings the rate (reach 88 + 4 likes looked 12x better than our median)
TEST_POST = re.compile(r"\(test(?: post)?\)|\btest upload\b", re.I)  # publishing tests, not content


def _num(v: Any) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _aspects(text: str) -> list[str]:
    from intel.models.weakness_miner.mine import analyse_text  # sealed code: imported, never edited

    return sorted({a for clause in analyse_text(text, None) for a in clause["aspects"]})


def own_post_chunks(posts: pd.DataFrame, metrics: pd.DataFrame, allowed_handles: frozenset[str]) -> list[Chunk]:
    """Our own posts with a caption, each with its latest metrics. engagement_rate = (likes + comments + shares +
    saves) / reach (only from a reach of MIN_REACH); vs_our_median = that rate over the median of our posts on the
    same platform (1.0 = typical)."""
    latest = metrics.sort_values("captured_at").groupby("social_post_id").tail(1).set_index("social_post_id")
    rows = []
    for _, p in posts.iterrows():
        if pd.isna(p["caption"]) or not str(p["caption"]).strip() or TEST_POST.search(str(p["caption"])):
            continue
        m = latest.loc[p["id"]] if p["id"] in latest.index else None
        perf: dict[str, float] = {}
        if m is not None:
            for k in ("reach", "views", *ENGAGEMENT):
                if (v := _num(m.get(k))) is not None:
                    perf[k] = v
            if perf.get("reach", 0.0) >= MIN_REACH:
                perf["engagement_rate"] = round(sum(perf.get(k, 0.0) for k in ENGAGEMENT) / perf["reach"], 4)
        rows.append((p, perf))
    medians = (
        pd.DataFrame(
            [
                {"platform": p["platform"], "er": perf["engagement_rate"]}
                for p, perf in rows
                if "engagement_rate" in perf
            ]
        )
        .groupby("platform")["er"]
        .median()
        .to_dict()
        if any("engagement_rate" in perf for _, perf in rows)
        else {}
    )
    out = []
    for p, perf in rows:
        median = medians.get(p["platform"])
        if "engagement_rate" in perf and median:
            perf["vs_our_median"] = round(perf["engagement_rate"] / median, 2)
        c = clean(str(p["caption"]), allowed_handles)
        posted = pd.Timestamp(p["posted_at"])
        ctype = None if pd.isna(p["content_type"]) else str(p["content_type"])
        header = f"Dhaka Kacchi own post · {p['platform']}{' · ' + ctype if ctype else ''} · {posted:%Y-%m}"
        out.append(
            Chunk(
                collection="own_posts",
                cite=cite_id("own_posts", str(p["id"])),
                source_id=str(p["id"]),
                text=c.text,
                embed_text=f"{header}\n{c.text}",
                posted_at=posted,
                platform=str(p["platform"]),
                aspects=_aspects(c.text),
                content_type=ctype,
                account=OWN_ACCOUNT,
                url=None if pd.isna(p["permalink"]) else str(p["permalink"]),
                metrics=perf or None,
                suspicious=c.suspicious,
                masks=dict(c.masks),
            )
        )
    return out


def creator_post_chunks(posts: pd.DataFrame, creators: pd.DataFrame, allowed_handles: frozenset[str]) -> list[Chunk]:
    """Berlin food creators' TikTok posts (public business accounts). Captions are cut at 120 characters by the source.
    like_rate = likes / views; vs_account_median = like_rate over the account's median engagement rate."""
    by_id = creators.set_index("account_id")
    out = []
    for _, p in posts.iterrows():
        if pd.isna(p["caption"]) or not str(p["caption"]).strip():
            continue
        acc = by_id.loc[p["account_id"]] if p["account_id"] in by_id.index else None
        handle = None if acc is None or pd.isna(acc["handle"]) else str(acc["handle"])
        views, likes = _num(p["views"]), _num(p["likes"])
        perf: dict[str, float] = {k: v for k, v in (("views", views), ("likes", likes)) if v is not None}
        if views:
            perf["like_rate"] = round((likes or 0.0) / views, 4)
            median = None if acc is None else _num(acc["median_engagement_rate"])
            if median:
                perf["vs_account_median"] = round(perf["like_rate"] / median, 2)
        c = clean(str(p["caption"]), allowed_handles)
        posted = pd.Timestamp(p["posted_at"])
        source_id = f"{p['account_id']}|{posted.isoformat()}"
        header = f"Berlin food creator post · TikTok · @{handle or 'unknown'} · {posted:%Y-%m}"
        out.append(
            Chunk(
                collection="creator_posts",
                cite=cite_id("creator_posts", source_id),
                source_id=source_id,
                text=c.text,
                embed_text=f"{header}\n{c.text}",
                posted_at=posted,
                platform="tiktok",
                aspects=_aspects(c.text),
                content_type="video",
                account=handle,
                url=None if pd.isna(p["permalink"]) else str(p["permalink"]),
                metrics=perf or None,
                suspicious=c.suspicious,
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
