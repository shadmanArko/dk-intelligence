"""Data contracts for every table the models CONSUME (ADR 0017).

Consumer-driven: a table is contracted because a model reads it, and only the columns models read are required. Two
layers per table:

1. **Structure**, checked on the snapshot's manifest (the Parquet/Arrow types are fixed by `intel.pull`, unlike pandas
   dtypes, which change when a column has nulls): every required column exists with its Arrow type.
2. **Values**, checked with pandera on the data: uniqueness, nullability, ranges, allowed values (`errors`), plus
   softer expectations that only warn (`warnings`).

Around them: row-count floors, volume vs the previous snapshot, freshness of the newest row at snapshot time, and
cross-table links (`LINKS`). Errors stop the Sunday run before training; warnings travel in the run report.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
import pandera.pandas as pa

Source = Literal["warehouse", "market"]
Severity = Literal["error", "warning"]
TS = "timestamp[us, tz=UTC]"
DAY = dt.timedelta(days=1)


@dataclass(frozen=True)
class Freshness:
    """The newest value of `column` must be at most `warn_after` / `error_after` older than the snapshot time."""

    column: str
    warn_after: dt.timedelta
    error_after: dt.timedelta | None = None


@dataclass(frozen=True)
class TableContract:
    source: Source
    table: str
    used_by: tuple[str, ...]
    columns: dict[str, str]  # required column -> Arrow type as written in the manifest
    errors: pa.DataFrameSchema | None = None
    warnings: pa.DataFrameSchema | None = None
    min_rows: int = 1
    max_drop: float | None = 0.2  # error if rows < (1 - max_drop) x the previous snapshot's rows
    freshness: Freshness | None = None
    horizon: tuple[str, dt.timedelta] | None = None  # the newest `column` must reach snapshot time + this (calendars)


@dataclass(frozen=True)
class Link:
    """Every non-null `child.column` must exist in `parent.parent_column`."""

    source: Source
    child: str
    column: str
    parent: str
    parent_column: str
    severity: Severity = "error"


def _schema(columns: dict[str, pa.Column], unique: list[str] | None = None, name: str = "") -> pa.DataFrameSchema:
    return pa.DataFrameSchema(columns, unique=unique, strict=False, coerce=False, name=name)


def _nonneg(nullable: bool = True) -> pa.Column:
    return pa.Column(None, pa.Check.ge(0), nullable=nullable)


def _parses_as_unit_interval(s: pd.Series) -> pd.Series:
    """rating_norm arrives as text (unconstrained Postgres numeric is stored losslessly as a string)."""
    v = pd.to_numeric(s, errors="coerce")
    return v.between(0, 1) | s.isna()


AFS_AGGREGATE = r"(?i)(?:^|\s)(?:zusammen|insgesamt)$"


def _unique_countries(s: pd.Series) -> pd.Series:
    """True for subtotal rows and for country names that occur once."""
    aggregate = s.str.contains(AFS_AGGREGATE, regex=True, na=False)
    return aggregate | ~s.duplicated(keep=False)


BERLIN_LAT = pa.Check.in_range(52.2, 52.8)  # Berlin plus the delivery ring into Brandenburg
BERLIN_LON = pa.Check.in_range(12.8, 14.0)
PLATFORMS = ["facebook", "instagram", "threads", "youtube", "tiktok"]
CONTENT_TYPES = ["video", "image", "reel", "carousel", "short", "text", "story", "link"]

CONTRACTS: tuple[TableContract, ...] = (
    # ---------------- warehouse (the business's own data) ----------------
    TableContract(
        "warehouse",
        "social_post",
        used_by=("content_scorer", "reach_scorer", "funnel"),
        columns={"id": "string", "platform": "string", "posted_at": TS, "content_type": "string", "caption": "string"},
        errors=_schema(
            {
                "id": pa.Column(None, nullable=False),
                "platform": pa.Column(None, nullable=False),
                "posted_at": pa.Column(None, nullable=False),
            },
            unique=["id"],
        ),
        warnings=_schema(
            {
                "platform": pa.Column(None, pa.Check.isin(PLATFORMS)),
                "content_type": pa.Column(None, pa.Check.isin(CONTENT_TYPES), nullable=True),
            }
        ),
        min_rows=50,
        freshness=Freshness("posted_at", warn_after=21 * DAY),  # the owner may pause posting: never an error
    ),
    TableContract(
        "warehouse",
        "social_metrics_snapshot",
        used_by=("content_scorer", "reach_scorer"),
        columns={
            "social_post_id": "string",
            "captured_at": TS,
            "impressions": "int64",
            "reach": "int64",
            "likes": "int64",
            "comments": "int64",
            "shares": "int64",
        },
        errors=_schema(
            {
                "social_post_id": pa.Column(None, nullable=False),
                "captured_at": pa.Column(None, nullable=False),
                **{c: _nonneg() for c in ("impressions", "reach", "likes", "comments", "shares")},
            },
            unique=["social_post_id", "captured_at"],
        ),
        min_rows=500,
        freshness=Freshness("captured_at", warn_after=26 * dt.timedelta(hours=1), error_after=7 * DAY),
    ),
    TableContract(
        "warehouse",
        "raw_orders_direct",
        used_by=("funnel", "community_map"),
        columns={"payload": "string"},
        errors=_schema({"payload": pa.Column(None, nullable=False)}),
        min_rows=0,
    ),
    TableContract(
        "warehouse",
        "raw_posthog_events",
        used_by=("funnel",),
        columns={"occurred_at": TS, "payload": "string"},
        errors=_schema({"occurred_at": pa.Column(None, nullable=False), "payload": pa.Column(None, nullable=False)}),
        min_rows=0,
        freshness=Freshness("occurred_at", warn_after=2 * DAY, error_after=14 * DAY),
    ),
    TableContract(
        "warehouse",
        "tracked_link",
        used_by=("funnel",),
        columns={
            "source": "string",
            "medium": "string",
            "campaign": "string",
            "content": "string",
            "social_post_id": "string",
            "link_created_at": TS,
        },
        min_rows=0,
        max_drop=None,  # links can be deleted by the owner
    ),
    # ---------------- market (the data platform's snapshot) ----------------
    TableContract(
        "market",
        "biryani_reviews",
        used_by=("weakness_miner",),
        columns={
            "review_id": "string",
            "listing_id": "string",
            "platform": "string",
            "posted_at": TS,
            "rating_norm": "string",
            "text": "string",
        },
        errors=_schema(
            {
                "review_id": pa.Column(None, nullable=False),
                "listing_id": pa.Column(None, nullable=False),
                "rating_norm": pa.Column(
                    None, pa.Check(_parses_as_unit_interval, element_wise=False, name="parses_as_0_to_1"), nullable=True
                ),
            },
            unique=["review_id"],
        ),
        min_rows=1000,
        max_drop=0.5,
        freshness=Freshness("posted_at", warn_after=60 * DAY),
    ),
    TableContract(
        "market",
        "biryani_listings",
        used_by=("weakness_miner",),
        columns={"listing_id": "string", "name": "string", "district": "string", "lat": "double", "lon": "double"},
        errors=_schema({"listing_id": pa.Column(None, nullable=False)}, unique=["listing_id"]),
        warnings=_schema(
            {"lat": pa.Column(None, BERLIN_LAT, nullable=True), "lon": pa.Column(None, BERLIN_LON, nullable=True)}
        ),
        min_rows=100,
        max_drop=0.5,
    ),
    TableContract(
        "market",
        "lor_community",
        used_by=("community_map",),
        columns={"area_code": "string", "community_id": "string", "residents": "int32"},
        errors=_schema({"area_code": pa.Column(None, nullable=False), "residents": _nonneg(nullable=False)}),
        min_rows=1000,
        max_drop=0.5,
    ),
    TableContract(
        "market",
        "lor_stats",
        used_by=("community_map",),
        columns={"area_id": "string", "variable": "string", "value": "double"},
        errors=_schema({"area_id": pa.Column(None, nullable=False), "variable": pa.Column(None, nullable=False)}),
        min_rows=1000,
        max_drop=0.5,
    ),
    TableContract(
        "market",
        "lor_places",
        used_by=("community_map",),
        columns={"area_id": "string", "name": "string", "district": "string", "lat": "double", "lon": "double"},
        errors=_schema({"area_id": pa.Column(None, nullable=False)}, unique=["area_id"]),
        warnings=_schema(
            {"lat": pa.Column(None, BERLIN_LAT, nullable=True), "lon": pa.Column(None, BERLIN_LON, nullable=True)}
        ),
        min_rows=400,
        max_drop=0.5,
    ),
    TableContract(
        "market",
        "postcode_area",
        used_by=("community_map",),
        columns={"postcode": "string", "area_id": "string"},
        errors=_schema({"postcode": pa.Column(None, nullable=False)}, unique=["postcode"]),
        min_rows=100,
        max_drop=0.5,
    ),
    TableContract(
        "market",
        "content_feature_performance",
        used_by=("content_scorer", "reach_scorer", "occasion_planner"),
        columns={
            "platform": "string",
            "tag_type": "string",
            "tag_id": "string",
            "posts": "int64",
            "median_lift": "double",
        },
        errors=_schema(
            {
                "tag_type": pa.Column(None, nullable=False),
                "tag_id": pa.Column(None, nullable=False),
                "posts": _nonneg(nullable=False),
            }
        ),
        min_rows=100,
        max_drop=0.5,
    ),
    TableContract(
        "market",
        "occasion_day",
        used_by=("content_scorer", "reach_scorer"),
        columns={"day": "date32[day]", "community_id": "string", "occasion_type": "string", "food_role": "string"},
        errors=_schema({"day": pa.Column(None, nullable=False)}, unique=["day", "community_id", "occasion_type"]),
        min_rows=1000,
        max_drop=0.5,
        horizon=("day", 60 * DAY),  # the calendar must reach two months ahead, or the planner and features go blind
    ),
    TableContract(
        "market",
        "occasions_next",
        used_by=("occasion_planner",),
        columns={"day": "date32[day]", "community_id": "string", "occasion_type": "string", "food_role": "string"},
        min_rows=1,
        max_drop=None,  # the next 30-60 days: varies naturally
    ),
    TableContract(
        "market",
        "afs_citizenship",
        used_by=("occasion_planner",),
        columns={"citizenship": "string", "residents": "int32"},
        # The Berlin statistics office (AfS) mixes country rows with subtotals ("Europa zusammen", "Zusammen",
        # "Insgesamt"): summing the table counts people 3x. The pull drops them (mip's is_aggregate), and the planner
        # looks countries up by name, so the rule it relies on is: COUNTRY names are unique. Subtotals that slip
        # through (older snapshots) are recognised, not counted as duplicates.
        errors=_schema(
            {
                "citizenship": pa.Column(
                    None, pa.Check(_unique_countries, element_wise=False, name="country_names_unique"), nullable=False
                ),
                "residents": _nonneg(nullable=False),
            }
        ),
        min_rows=50,
        max_drop=0.5,
    ),
    TableContract(
        "market",
        "creators",
        used_by=("creator_matcher",),
        columns={"account_id": "string", "handle": "string", "posts_90d": "int64", "median_views": "double"},
        errors=_schema(
            {"account_id": pa.Column(None, nullable=False), "handle": pa.Column(None, nullable=False)},
            unique=["account_id"],
        ),
        min_rows=100,
        max_drop=0.5,
    ),
    TableContract(
        "market",
        "creator_posts",
        used_by=("creator_matcher",),
        columns={"account_id": "string", "posted_at": TS, "views": "int64", "likes": "int64"},
        errors=_schema({"account_id": pa.Column(None, nullable=False), "views": _nonneg(), "likes": _nonneg()}),
        min_rows=100,
        max_drop=0.5,
    ),
)

LINKS: tuple[Link, ...] = (
    Link("warehouse", "social_metrics_snapshot", "social_post_id", "social_post", "id"),
    Link("market", "biryani_reviews", "listing_id", "biryani_listings", "listing_id", severity="warning"),
    Link("market", "creator_posts", "account_id", "creators", "account_id", severity="warning"),
)


@dataclass(frozen=True)
class Finding:
    table: str
    check: str
    severity: Severity
    detail: str

    def line(self) -> str:
        return f"{self.table}: {self.check}: {self.detail}"


@dataclass
class Contracts:
    """The contracts for one source, looked up by table."""

    source: Source
    tables: dict[str, TableContract] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)

    @classmethod
    def for_source(cls, source: Source) -> Contracts:
        return cls(
            source,
            {c.table: c for c in CONTRACTS if c.source == source},
            [lk for lk in LINKS if lk.source == source],
        )
