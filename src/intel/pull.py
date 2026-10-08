"""`intel pull`: copy the warehouse into an immutable, versioned Parquet snapshot.

Layout:
    data/pulls/<pull_id>/<table>.parquet
    data/pulls/<pull_id>/manifest.json     row counts, column types, sha256 per file, server snapshot time
    data/pulls/<pull_id>/SHA256SUMS        `shasum -a 256 -c SHA256SUMS` verifies the folder
    data/pulls/LATEST                      id of the newest complete pull

All tables are read inside ONE read-only REPEATABLE READ transaction, so the snapshot is consistent across
tables (an order and its lines come from the same moment). The pull is written to `<id>.partial/` and renamed
only when complete, so a half-written pull is never used for training.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql

from intel import __version__

FORMAT_VERSION = 1
BATCH_ROWS = 50_000
EXCLUDE = frozenset({"alembic_version"})

# Postgres type OIDs -> Arrow types. Domains arrive as their base type. Anything not listed is stored as text.
_OID_ARROW: dict[int, pa.DataType] = {
    16: pa.bool_(),
    20: pa.int64(),
    21: pa.int16(),
    23: pa.int32(),
    26: pa.int64(),
    700: pa.float32(),
    701: pa.float64(),
    25: pa.string(),
    1042: pa.string(),
    1043: pa.string(),
    19: pa.string(),
    2950: pa.string(),  # uuid
    114: pa.string(),
    3802: pa.string(),  # json / jsonb as canonical JSON text
    1082: pa.date32(),
    1114: pa.timestamp("us"),
    1184: pa.timestamp("us", tz="UTC"),
    1009: pa.list_(pa.string()),
    1015: pa.list_(pa.string()),  # text[] / varchar[]
    1007: pa.list_(pa.int32()),
    1016: pa.list_(pa.int64()),
}
_NUMERIC = 1700
_JSON = {114, 3802}


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    pg_type: str
    oid: int
    arrow: pa.DataType


@dataclass
class TableResult:
    table: str
    rows: int
    file: str
    sha256: str
    bytes: int
    columns: list[dict[str, str]] = field(default_factory=list)


def arrow_type(oid: int, precision: int | None = None, scale: int | None = None) -> pa.DataType:
    if oid == _NUMERIC:
        # numeric(p,s) keeps exact money values; unconstrained numeric has no fixed scale -> text, lossless
        if precision is not None and scale is not None and 0 < precision <= 38:
            return pa.decimal128(precision, scale)
        return pa.string()
    return _OID_ARROW.get(oid, pa.string())


def convert(value: Any, spec: ColumnSpec) -> Any:
    """Make a psycopg value fit the column's Arrow type."""
    if value is None:
        return None
    if spec.oid in _JSON:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    t = spec.arrow
    if pa.types.is_string(t) and not isinstance(value, str):
        return format(value, "f") if isinstance(value, Decimal) else str(value)
    if pa.types.is_timestamp(t) and t.tz and isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC)
    return value


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_tables(conn: psycopg.Connection, schema: str = "public") -> list[str]:
    """Tables and views this role may read (no point pulling what we can't SELECT)."""
    rows = conn.execute(
        """select c.relname from pg_class c
           where c.relnamespace = %s::regnamespace and c.relkind in ('r','p','v','m')
             and has_table_privilege(c.oid, 'SELECT')
           order by 1""",
        (schema,),
    ).fetchall()
    return [r[0] for r in rows if r[0] not in EXCLUDE]


def column_specs(cur: psycopg.Cursor) -> list[ColumnSpec]:
    out = []
    for d in cur.description or []:
        prec, scale = (d.precision, d.scale) if d.type_code == _NUMERIC else (None, None)
        out.append(ColumnSpec(d.name, d.type_display, d.type_code, arrow_type(d.type_code, prec, scale)))
    return out


def _batch(cols: list[list[Any]], specs: list[ColumnSpec], schema: pa.Schema) -> pa.RecordBatch:
    return pa.RecordBatch.from_arrays(
        [pa.array(c, type=s.arrow) for c, s in zip(cols, specs, strict=True)], schema=schema
    )


def _batches(rows: Iterable[tuple[Any, ...]], specs: list[ColumnSpec], schema: pa.Schema) -> Iterable[pa.RecordBatch]:
    cols: list[list[Any]] = [[] for _ in specs]
    n = 0
    for row in rows:
        for i, (v, s) in enumerate(zip(row, specs, strict=True)):
            cols[i].append(convert(v, s))
        n += 1
        if n == BATCH_ROWS:
            yield _batch(cols, specs, schema)
            cols, n = [[] for _ in specs], 0
    if n:  # an empty table yields nothing; ParquetWriter still writes a valid file with the schema
        yield _batch(cols, specs, schema)


def pull_table(
    conn: psycopg.Connection, table: str, out_dir: Path, schema: str = "public", query: str | None = None
) -> TableResult:
    """Copy a whole table, or (with `query`) the result of a fixed read-only SELECT saved as `table`.parquet."""
    path = out_dir / f"{table}.parquet"
    source = (
        sql.SQL(query)
        if query
        else sql.SQL("select * from {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    )
    counted = conn.execute(sql.SQL("select count(*) from ({}) q").format(source)).fetchone()
    assert counted is not None  # count(*) always returns one row
    expected = counted[0]
    with conn.cursor(name=f"pull_{table}") as cur:  # server-side cursor: constant memory
        cur.itersize = BATCH_ROWS
        cur.execute(source)
        specs = column_specs(cur)
        arrow_schema = pa.schema(
            [pa.field(s.name, s.arrow) for s in specs],
            metadata={"source": f"query:{table}" if query else f"{schema}.{table}"},
        )
        rows = 0
        with pq.ParquetWriter(path, arrow_schema, compression="zstd") as w:
            for batch in _batches(cur, specs, arrow_schema):
                rows += batch.num_rows
                w.write_batch(batch)
    if rows != expected:
        raise RuntimeError(f"{table}: read {rows} rows but count(*) said {expected} in the same snapshot")
    return TableResult(
        table=table,
        rows=rows,
        file=path.name,
        sha256=sha256_file(path),
        bytes=path.stat().st_size,
        columns=[{"name": s.name, "pg_type": s.pg_type, "arrow": str(s.arrow)} for s in specs],
    )


def new_pull_id(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%dT%H%M%SZ")


def run_pull(
    dsn: str,
    pulls_dir: Path,
    tables: list[str] | None = None,
    keep: int = 12,
    schema: str = "public",
    log: Callable[..., None] = print,
    items: list[tuple[Any, ...]] | None = None,
    extra_files: dict[str, bytes] | None = None,
    application_name: str = "dk-intelligence-pull",
) -> Path:
    """Snapshot `tables` of `schema` (default: all readable). `items` = explicit (schema, table) pairs across
    schemas instead (file name = table name). `extra_files` (name -> bytes) are stored and checksummed too."""
    pulls_dir.mkdir(parents=True, exist_ok=True)
    pull_id = new_pull_id()
    tmp = pulls_dir / f"{pull_id}.partial"
    final = pulls_dir / pull_id
    tmp.mkdir()
    try:
        with psycopg.connect(dsn, connect_timeout=15, application_name=application_name) as conn:
            conn.read_only = True
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            with conn.transaction():
                conn.execute("set local time zone 'UTC'")
                info = conn.execute(
                    "select now(), current_setting('server_version'), current_database(), current_user"
                ).fetchone()
                assert info is not None  # a select without FROM always returns one row
                snap_at, server, db, user = info
                if items is None:
                    available = list_tables(conn, schema)
                    wanted = tables or available
                    missing = sorted(set(wanted) - set(available))
                    if missing:
                        raise RuntimeError(f"not readable or not found: {', '.join(missing)}")
                    items = [(schema, t) for t in wanted]
                results = []
                for item in items:  # (schema, table) or ("query", name, sql)
                    if item[0] == "query":
                        _, t, q = item
                        r = pull_table(conn, t, tmp, query=q)
                    else:
                        sch, t = item
                        r = pull_table(conn, t, tmp, sch)
                    log(f"  {t:32s} {r.rows:>9,} rows")
                    results.append(r)

        manifest = {
            "format_version": FORMAT_VERSION,
            "pull_id": pull_id,
            "source": {
                "database": db,
                "schemas": sorted({i[0] for i in items if i[0] != "query"}),
                "queries": sorted(i[1] for i in items if i[0] == "query"),
                "role": user,
                "server_version": server,
            },
            "snapshot_at": snap_at.astimezone(dt.UTC).isoformat(),
            "created_by": f"dk-intelligence {__version__}",
            "tables": {r.table: {k: v for k, v in r.__dict__.items() if k != "table"} for r in results},
            "total_rows": sum(r.rows for r in results),
            "files": sorted(extra_files or {}),
        }
        for name, data in (extra_files or {}).items():
            (tmp / name).write_bytes(data)
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        sums = [f"{r.sha256}  {r.file}" for r in results]
        sums += [f"{sha256_file(tmp / name)}  {name}" for name in sorted(extra_files or {})]
        sums.append(f"{sha256_file(tmp / 'manifest.json')}  manifest.json")
        (tmp / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    (pulls_dir / "LATEST").write_text(pull_id + "\n")
    prune(pulls_dir, keep)
    return final


def list_pulls(pulls_dir: Path) -> list[Path]:
    """Complete pulls, oldest first (ids sort by time)."""
    if not pulls_dir.exists():
        return []
    return sorted(
        p
        for p in pulls_dir.iterdir()
        if p.is_dir() and not p.name.endswith(".partial") and (p / "manifest.json").exists()
    )


def prune(pulls_dir: Path, keep: int) -> list[Path]:
    old = list_pulls(pulls_dir)[:-keep] if keep > 0 else []
    for p in old:
        shutil.rmtree(p)
    for p in pulls_dir.glob("*.partial"):  # leftovers of a crashed run
        shutil.rmtree(p, ignore_errors=True)
    return old


def verify_pull(pull_dir: Path) -> list[str]:
    """Return a list of problems (empty = OK): checksums, manifest row counts vs Parquet metadata."""
    problems = []
    manifest = json.loads((pull_dir / "manifest.json").read_text())
    for line in (pull_dir / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        f = pull_dir / name
        if not f.exists():
            problems.append(f"missing file {name}")
        elif sha256_file(f) != digest:
            problems.append(f"checksum mismatch {name}")
    for table, meta in manifest["tables"].items():
        f = pull_dir / meta["file"]
        if not f.exists():
            continue
        try:
            if pq.ParquetFile(f).metadata.num_rows != meta["rows"]:
                problems.append(f"row count mismatch {table}")
        except pa.ArrowException:
            problems.append(f"unreadable parquet {meta['file']}")
    return problems


def resolve_pull(pulls_dir: Path, pull_id: str | None = None) -> Path:
    if pull_id is None:
        latest = pulls_dir / "LATEST"
        if not latest.exists():
            raise FileNotFoundError("no pulls yet: run `intel pull`")
        pull_id = latest.read_text().strip()
    p = pulls_dir / pull_id
    if not (p / "manifest.json").exists():
        raise FileNotFoundError(f"pull {pull_id} not found or incomplete")
    return p
