"""P4.7 (ADR 0030): knowledge packs and models travel through the upload-only inbox, intel-api verifies and installs
them, intel-mcp's loader syncs intel-db to `packs/ACTIVE`, and the migration's roles can touch schema rag only."""

import datetime as dt
import io
import json
import tarfile
import uuid
from pathlib import Path

import pytest

from intel.rag import pack as kp
from intel.rag import ship
from intel.rag.build import make_indexer
from intel.rag.embed import HashEmbedder
from tests.test_rag import _snapshot, pg  # noqa: F401 - pg is a fixture

UTC = dt.UTC


def _built_pack(tmp_path: Path, day: int = 10, gate_passed: bool = True) -> tuple[Path, str]:
    mip, market = _snapshot(tmp_path) if not (tmp_path / "mip").exists() else (tmp_path / "mip", tmp_path / "market")
    root = tmp_path / "knowledge"
    pid = kp.build_brand_pack(
        mip, market, root, now=dt.datetime(2026, 10, day, tzinfo=UTC),
        indexer=make_indexer(HashEmbedder(), tmp_path / "cache"),
    )  # fmt: skip
    gates = root / "gates"
    gates.mkdir(exist_ok=True)
    (gates / f"{pid}.json").write_text(json.dumps({"candidate": pid, "passed": gate_passed}))
    return root, pid


# ---------- the package: build on the Mac, intake on the VPS ----------


def test_a_gated_pack_round_trips_and_becomes_active(tmp_path):
    root, pid = _built_pack(tmp_path)
    tgz = ship.pack_package(root, pid, tmp_path / "out")
    assert tgz.name == f"knowledge-pack-{pid}.tar.gz" and tgz.with_name(tgz.name + ".sha256").exists()
    vps = tmp_path / "vps"
    it = ship.intake(tgz, vps)
    assert (it.status, it.kind, it.ident, it.problems) == ("activated", "pack", pid, [])
    assert kp.active_id(vps / "packs") == pid and kp.verify(vps / "packs" / pid)["pack_id"] == pid
    assert not list(vps.glob(".incoming-*"))  # the work folder is always cleaned up


def test_ungated_tampered_and_escaping_packages_are_refused(tmp_path):
    root, pid = _built_pack(tmp_path, gate_passed=False)
    with pytest.raises(ship.ShipError, match="gate"):
        ship.pack_package(root, pid, tmp_path / "out")
    (root / "gates" / f"{pid}.json").write_text(json.dumps({"candidate": pid, "passed": True}))
    tgz = ship.pack_package(root, pid, tmp_path / "out")
    data = bytearray(tgz.read_bytes())
    data[-40] ^= 0xFF  # one flipped byte: the outer checksum no longer matches
    bad = tmp_path / "bad" / tgz.name
    bad.parent.mkdir()
    bad.write_bytes(bytes(data))
    bad.with_name(bad.name + ".sha256").write_text(tgz.with_name(tgz.name + ".sha256").read_text())
    assert ship.intake(bad, tmp_path / "vps").status == "invalid"

    def forged(name: str, members: dict[str, bytes]) -> Path:  # a package with a valid outer checksum
        out = tmp_path / "forged" / name
        out.parent.mkdir(exist_ok=True)
        with tarfile.open(out, "w:gz") as tar:
            for path, content in members.items():
                info = tarfile.TarInfo(path)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        out.with_name(out.name + ".sha256").write_text(f"{ship._sha_file(out)}  {out.name}\n")
        return out

    meta = json.dumps({"kind": "models", "id": "x"}).encode()
    escape = forged("knowledge-models-x.tar.gz", {"models-x/knowledge.json": meta, "models-x/../../evil": b"x"})
    assert "refusing path" in ship.intake(escape, tmp_path / "vps").problems[0]
    sums = f"{'0' * 64}  knowledge.json\n".encode()  # an inner checksum that doesn't match
    inner = forged("knowledge-models-y.tar.gz", {"models-y/knowledge.json": meta, "models-y/SHA256SUMS": sums})
    assert ship.intake(inner, tmp_path / "vps").status == "invalid"
    assert kp.active_id(tmp_path / "vps" / "packs") is None  # nothing refused ever becomes active


def test_rollback_marker_and_retention(tmp_path):
    vps = tmp_path / "vps"
    root, first = _built_pack(tmp_path, day=10)
    ship.intake(ship.pack_package(root, first, tmp_path / "out"), vps)
    _, second = _built_pack(tmp_path, day=11)
    ship.intake(ship.pack_package(root, second, tmp_path / "out"), vps)
    assert kp.active_id(vps / "packs") == second
    it = ship.intake(ship.activate_package(first, tmp_path / "out"), vps)
    assert it.status == "activated" and kp.active_id(vps / "packs") == first
    _, third = _built_pack(tmp_path, day=12)
    ship.intake(ship.pack_package(root, third, tmp_path / "out"), vps)
    assert sorted(p.name for p in (vps / "packs").glob("kp-*")) == sorted([third, second])  # active + one before
    assert ship.intake(ship.activate_package("kp-missing", tmp_path / "out"), vps).status == "invalid"


def test_models_travel_once_and_land_where_the_server_looks(tmp_path):
    models = tmp_path / "models"
    for repo in ("org--embedder", "org--reranker"):
        (models / repo / "onnx").mkdir(parents=True)
        (models / repo / "onnx" / "model.onnx").write_bytes(repo.encode() * 100)
        (models / repo / "tokenizer.json").write_text("{}")
    tgz = ship.models_package(models, ["org/embedder", "org/reranker"], tmp_path / "out")
    it = ship.intake(tgz, tmp_path / "vps")
    assert it.status == "installed" and it.kind == "models"
    assert (tmp_path / "vps" / "models" / "org--embedder" / "onnx" / "model.onnx").read_bytes()[:3] == b"org"


# ---------- intel-db: the migration's roles, the loader, the intake loop ----------


def _role_dsn(dsn: str, role: str, password: str) -> str:
    import psycopg

    return psycopg.conninfo.make_conninfo(dsn, user=role, password=password)


def test_migration_roles_touch_schema_rag_only_and_the_loader_follows_active(pg, tmp_path):  # noqa: F811
    import psycopg
    from psycopg import sql

    from intel.rag import lifecycle, store
    from intel.serve.db import migrate
    from intel.serve.knowledge import scan_knowledge
    from intel.settings import McpSettings

    with psycopg.connect(pg, autocommit=True) as c:  # the pg fixture created rag via schema.sql: start clean
        c.execute("drop schema rag cascade")
    assert "0007_rag" in migrate(pg)
    pw = uuid.uuid4().hex
    with psycopg.connect(pg, autocommit=True) as c:
        for role in ("intel_mcp_reader", "intel_mcp_loader"):  # what deploy.sh does from deploy/.env
            c.execute(sql.SQL("alter role {} password {}").format(sql.Identifier(role), sql.Literal(pw)))
        mac = {r[0] for r in c.execute("select column_name from information_schema.columns where table_schema='rag' "
                                         "and table_name='chunk'")}  # fmt: skip
    reader, loader = _role_dsn(pg, "intel_mcp_reader", pw), _role_dsn(pg, "intel_mcp_loader", pw)
    with psycopg.connect(reader) as c:
        c.execute("select count(*) from rag.chunk")
        for forbidden in ("select * from schema_migrations", "insert into rag.pack values ('x','{}',now(),'staged')"):
            with pytest.raises(psycopg.Error):
                c.execute(forbidden)
            c.rollback()
    with psycopg.connect(loader, autocommit=True) as c:
        with pytest.raises(psycopg.Error):
            c.execute("create table rag.evil (x int)")
        with pytest.raises(psycopg.Error):
            c.execute("select * from model_package")

    # the same columns as the Mac's schema.sql (the two DDLs never drift)
    name = f"mac_{uuid.uuid4().hex[:6]}"
    with psycopg.connect(pg, autocommit=True) as c:
        c.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    other = psycopg.conninfo.make_conninfo(pg, dbname=name)
    with psycopg.connect(other, autocommit=True) as c:
        c.execute("create extension vector")
        store.ensure_schema(c)
        cols = {r[0] for r in c.execute("select column_name from information_schema.columns "
                                         "where table_schema='rag' and table_name='chunk'")}  # fmt: skip
    with psycopg.connect(pg, autocommit=True) as c:
        c.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))
    assert cols == mac

    # intel-api's intake (once per package), then intel-mcp's loader as the loader role
    root, pid = _built_pack(tmp_path)
    inbox, vps = tmp_path / "inbox", tmp_path / "vps"
    inbox.mkdir()
    for f in ship.pack_package(root, pid, tmp_path / "out").parent.glob("knowledge-*"):
        (inbox / f.name).write_bytes(f.read_bytes())
    assert [i.status for i in scan_knowledge(pg, inbox, vps)] == ["activated"]
    assert scan_knowledge(pg, inbox, vps) == []  # already done: never twice
    s = McpSettings(intel_pack_dir=vps / "packs", intel_rag_admin_dsn=loader)
    assert lifecycle.sync_to_active(s)["status"] == "activated"
    assert lifecycle.sync_to_active(s)["status"] == "in_sync"
    with psycopg.connect(reader) as c:
        assert store.active_pack(c) == pid and c.execute("select count(*) from rag.chunk").fetchone()[0] == 6
