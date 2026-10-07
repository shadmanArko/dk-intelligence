"""Real round trip against a throwaway schema. Runs when INTEL_TEST_PG_DSN points at a Postgres where the user
may CREATE SCHEMA (CI service container, or the local data-platform Postgres); skipped otherwise."""

import datetime as dt
import json
import os
import uuid
from decimal import Decimal

import psycopg
import pyarrow.parquet as pq
import pytest
from psycopg import sql

from intel.pull import run_pull, verify_pull

DSN = os.environ.get("INTEL_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="INTEL_TEST_PG_DSN not set")


@pytest.fixture
def schema():
    name = f"intel_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
        c.execute(
            sql.SQL("""
            create table {s}.kitchen (
              id uuid primary key, name text, price numeric(10,2), ratio numeric, qty int, big bigint,
              ok boolean, at timestamptz, day date, tags text[], payload jsonb, score double precision)
        """).format(s=sql.Identifier(name))
        )
        c.execute(sql.SQL("create table {s}.empty_table (id int, note text)").format(s=sql.Identifier(name)))
        c.execute(
            sql.SQL("""insert into {s}.kitchen values
            (%s,'Kacchi',15.00,0.123456789,2,9000000000,true,'2026-10-04 05:00+02','2026-10-04',
             array['halal','mutton'],'{{"lang":"bn","n":[1,2]}}',0.5),
            (%s,null,null,null,null,null,null,null,null,null,null,null)
        """).format(s=sql.Identifier(name)),
            (uuid.uuid4(), uuid.uuid4()),
        )
    yield name
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))


def test_round_trip(schema, tmp_path):
    out = run_pull(DSN, tmp_path, schema=schema, keep=3, log=lambda *_: None)
    assert verify_pull(out) == []
    m = json.loads((out / "manifest.json").read_text())
    assert m["tables"]["kitchen"]["rows"] == 2
    assert m["tables"]["empty_table"]["rows"] == 0
    assert (tmp_path / "LATEST").read_text().strip() == out.name

    t = pq.read_table(out / "kitchen.parquet").to_pylist()
    row = next(r for r in t if r["name"] == "Kacchi")
    assert row["price"] == Decimal("15.00")
    assert row["ratio"] == "0.123456789"
    assert row["big"] == 9_000_000_000
    assert row["at"] == dt.datetime(2026, 10, 4, 3, tzinfo=dt.UTC)
    assert row["tags"] == ["halal", "mutton"]
    assert json.loads(row["payload"]) == {"lang": "bn", "n": [1, 2]}
    assert pq.read_table(out / "empty_table.parquet").num_rows == 0


def test_unknown_table_fails_and_leaves_nothing(schema, tmp_path):
    with pytest.raises(RuntimeError, match="not readable or not found"):
        run_pull(DSN, tmp_path, tables=["nope"], schema=schema, log=lambda *_: None)
    assert list(tmp_path.iterdir()) == []


def test_items_across_schemas_and_extra_files(schema, tmp_path):
    out = run_pull(
        DSN, tmp_path, items=[(schema, "kitchen")], extra_files={"cues.yaml": b"cues: {}\n"}, log=lambda *_: None
    )
    assert verify_pull(out) == []
    m = json.loads((out / "manifest.json").read_text())
    assert list(m["tables"]) == ["kitchen"] and m["files"] == ["cues.yaml"] and m["source"]["schemas"] == [schema]
    (out / "cues.yaml").write_text("tampered")
    assert any("cues.yaml" in p for p in verify_pull(out))
