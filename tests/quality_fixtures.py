"""Small, VALID warehouse and market snapshots for the data-check tests: every contract passes as generated; a test
breaks exactly one thing. Written in the same layout as `intel.pull` (Parquet + manifest with Arrow types)."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

SNAP = dt.datetime(2026, 10, 8, 7, 0, tzinfo=dt.UTC)
TS = pa.timestamp("us", tz="UTC")


def _t(cols: dict[str, tuple[pa.DataType, list[Any]]]) -> pa.Table:
    return pa.table({k: pa.array(v, type=t) for k, (t, v) in cols.items()})


def warehouse_tables(n_posts: int = 60, snaps_per_post: int = 10) -> dict[str, pa.Table]:
    ids = [f"p{i:03d}" for i in range(n_posts)]
    posted = [SNAP - dt.timedelta(days=60 - i) for i in range(n_posts)]
    snap_post = [p for p in ids for _ in range(snaps_per_post)]
    captured = [posted[i] + dt.timedelta(hours=6 * k + 1) for i in range(n_posts) for k in range(snaps_per_post)]
    captured = [min(c, SNAP - dt.timedelta(hours=2)) - dt.timedelta(seconds=j) for j, c in enumerate(captured)]
    m = len(snap_post)
    return {
        "social_post": _t(
            {
                "id": (pa.string(), ids),
                "platform": (pa.string(), ["facebook", "instagram"] * (n_posts // 2)),
                "posted_at": (TS, posted),
                "content_type": (pa.string(), ["reel", "image", None] * (n_posts // 3)),
                "caption": (pa.string(), ["kacchi"] * n_posts),
            }
        ),
        "social_metrics_snapshot": _t(
            {
                "social_post_id": (pa.string(), snap_post),
                "captured_at": (TS, captured),
                **{
                    c: (pa.int64(), [k % 50 for k in range(m)])
                    for c in ("impressions", "reach", "likes", "comments", "shares")
                },
            }
        ),
        "raw_orders_direct": _t({"payload": (pa.string(), ['{"id": 1}'])}),
        "raw_posthog_events": _t(
            {
                "occurred_at": (TS, [SNAP - dt.timedelta(hours=3)]),
                "payload": (pa.string(), ['{"event": "$pageview"}']),
            }
        ),
        "tracked_link": _t(
            {
                "source": (pa.string(), []),
                "medium": (pa.string(), []),
                "campaign": (pa.string(), []),
                "content": (pa.string(), []),
                "social_post_id": (pa.string(), []),
                "link_created_at": (TS, []),
            }
        ),
    }


def market_tables() -> dict[str, pa.Table]:
    day0 = SNAP.date()
    days = [day0 + dt.timedelta(days=i - 200) for i in range(400)]  # reaches ~200 days ahead
    occ = [(d, c, o) for d in days for c, o in (("bd", "eid"), ("in", "diwali"), ("de", "xmas"))]
    listings = [f"l{i}" for i in range(120)]
    accounts = [f"a{i}" for i in range(120)]
    return {
        "biryani_reviews": _t(
            {
                "review_id": (pa.string(), [f"r{i}" for i in range(1200)]),
                "listing_id": (pa.string(), [listings[i % 120] for i in range(1200)]),
                "platform": (pa.string(), ["lieferando"] * 1200),
                "posted_at": (TS, [SNAP - dt.timedelta(days=1 + i % 30) for i in range(1200)]),
                "rating_norm": (pa.string(), [str(round((i % 5) / 4, 2)) for i in range(1200)]),
                "text": (pa.string(), ["rice dry"] * 1200),
            }
        ),
        "biryani_listings": _t(
            {
                "listing_id": (pa.string(), listings),
                "name": (pa.string(), ["x"] * 120),
                "district": (pa.string(), ["Mitte"] * 120),
                "lat": (pa.float64(), [52.52] * 120),
                "lon": (pa.float64(), [13.40] * 120),
            }
        ),
        "lor_community": _t(
            {
                "area_code": (pa.string(), [f"{i:08d}" for i in range(1100)]),
                "community_id": (pa.string(), ["bd"] * 1100),
                "residents": (pa.int32(), [i % 90 for i in range(1100)]),
            }
        ),
        "lor_stats": _t(
            {
                "area_id": (pa.string(), [f"{i:08d}" for i in range(1100)]),
                "variable": (pa.string(), ["pop"] * 1100),
                "value": (pa.float64(), [1.0] * 1100),
            }
        ),
        "lor_places": _t(
            {
                "area_id": (pa.string(), [f"{i:08d}" for i in range(450)]),
                "name": (pa.string(), ["a"] * 450),
                "district": (pa.string(), ["Mitte"] * 450),
                "lat": (pa.float64(), [52.5] * 450),
                "lon": (pa.float64(), [13.4] * 450),
            }
        ),
        "postcode_area": _t(
            {
                "postcode": (pa.string(), [str(10115 + i) for i in range(150)]),
                "area_id": (pa.string(), ["00000001"] * 150),
            }
        ),
        "content_feature_performance": _t(
            {
                "platform": (pa.string(), ["tiktok"] * 120),
                "tag_type": (pa.string(), ["dish"] * 120),
                "tag_id": (pa.string(), [f"t{i}" for i in range(120)]),
                "posts": (pa.int64(), [5] * 120),
                "median_lift": (pa.float64(), [1.1] * 120),
            }
        ),
        "occasion_day": _t(
            {
                "day": (pa.date32(), [d for d, _, _ in occ]),
                "community_id": (pa.string(), [c for _, c, _ in occ]),
                "occasion_type": (pa.string(), [o for _, _, o in occ]),
                "food_role": (pa.string(), ["feast"] * len(occ)),
            }
        ),
        "occasions_next": _t(
            {
                "day": (pa.date32(), [day0 + dt.timedelta(days=3)]),
                "community_id": (pa.string(), ["bd"]),
                "occasion_type": (pa.string(), ["eid"]),
                "food_role": (pa.string(), ["feast"]),
            }
        ),
        "afs_citizenship": _t(
            {
                "citizenship": (
                    pa.string(),
                    [f"Land {i}" for i in range(60)] + ["Asien zusammen", "Zusammen", "Zusammen", "Insgesamt"],
                ),
                "residents": (pa.int32(), [100] * 64),
            }
        ),
        "creators": _t(
            {
                "account_id": (pa.string(), accounts),
                "handle": (pa.string(), [f"h{i}" for i in range(120)]),
                "posts_90d": (pa.int64(), [3] * 120),
                "median_views": (pa.float64(), [1000.0] * 120),
            }
        ),
        "creator_posts": _t(
            {
                "account_id": (pa.string(), [accounts[i % 120] for i in range(240)]),
                "posted_at": (TS, [SNAP - dt.timedelta(days=i % 60 + 1) for i in range(240)]),
                "views": (pa.int64(), [100] * 240),
                "likes": (pa.int64(), [5] * 240),
            }
        ),
    }


def write_snapshot(
    root: Path,
    snapshot_id: str,
    tables: dict[str, pa.Table],
    snapshot_at: dt.datetime = SNAP,
    tweak: Callable[[dict[str, pa.Table]], None] | None = None,
) -> Path:
    tables = dict(tables)
    if tweak:
        tweak(tables)
    d = root / snapshot_id
    d.mkdir(parents=True)
    manifest: dict[str, Any] = {"pull_id": snapshot_id, "snapshot_at": snapshot_at.isoformat(), "tables": {}}
    for name, t in tables.items():
        pq.write_table(t, d / f"{name}.parquet")
        manifest["tables"][name] = {
            "rows": t.num_rows,
            "file": f"{name}.parquet",
            "columns": [{"name": f.name, "arrow": str(f.type)} for f in t.schema],
        }
    manifest["total_rows"] = sum(t.num_rows for t in tables.values())
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d


def replace_column(
    tables: dict[str, pa.Table], table: str, column: str, values: list[Any], type_: pa.DataType | None = None
) -> None:
    t = tables[table]
    i = t.schema.get_field_index(column)
    tables[table] = t.set_column(i, column, pa.array(values, type=type_ or t.schema.field(column).type))
