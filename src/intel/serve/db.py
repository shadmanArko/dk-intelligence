"""intel-db access: one short-lived connection per operation (tiny load; no pool needed), SQL migrations."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS = Path(__file__).with_name("migrations")


@contextmanager
def connect(dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        yield conn


def migrate(dsn: str) -> list[str]:
    applied = []
    with connect(dsn) as c:
        c.execute(
            "create table if not exists schema_migrations "
            "(version text primary key, applied_at timestamptz not null default now())"
        )
        c.execute("select pg_advisory_xact_lock(4242)")  # two processes starting at once
        done = {r["version"] for r in c.execute("select version from schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.stem not in done:
                c.execute(f.read_text())
                c.execute("insert into schema_migrations (version) values (%s)", (f.stem,))
                applied.append(f.stem)
    return applied


def log_event(dsn: str, kind: str, **detail) -> None:
    with connect(dsn) as c:
        c.execute("insert into event_log (kind, detail) values (%s, %s)", (kind, json.dumps(detail, default=str)))


def kv_get(dsn: str, key: str, default=None):
    with connect(dsn) as c:
        row = c.execute("select value from kv where key = %s", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(dsn: str, key: str, value) -> None:
    with connect(dsn) as c:
        c.execute(
            "insert into kv (key, value) values (%s, %s) on conflict (key) do update set value = excluded.value",
            (key, json.dumps(value)),
        )
