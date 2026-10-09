"""ADR 0021: the restore drill restores a real backup into a scratch container, verifies it, and fails loudly when the
backup is wrong (here: an audit trail edited before the backup). Needs Docker (CI has it); skipped otherwise."""

from __future__ import annotations

import gzip
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "pgvector/pgvector:pg16"


def _docker_usable() -> bool:
    """Docker installed and its daemon up. On CI a dead daemon must fail the run, so only skip off CI."""
    if shutil.which("docker") is None:
        return False
    if os.environ.get("CI"):
        return True
    return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_usable(), reason="docker not available (or its daemon is down)")


def _sh(*cmd: str, **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, **kw)


@pytest.fixture(scope="module")
def source_db():
    """A Postgres 16 'live' intel-db with the real migrations and a real approval flow."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    name = f"intel-drill-src-{os.getpid()}"
    r = _sh(
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "-e",
        "POSTGRES_PASSWORD=pw",
        "-e",
        "POSTGRES_USER=intel",
        "-e",
        "POSTGRES_DB=intel",
        "-p",
        f"127.0.0.1:{port}:5432",
        IMAGE,
    )
    if r.returncode:
        pytest.skip(f"cannot start {IMAGE}: {r.stderr[-200:]}")
    try:
        dsn = f"postgresql://intel:pw@127.0.0.1:{port}/intel"
        import psycopg

        for _ in range(60):
            try:
                psycopg.connect(dsn, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                time.sleep(1)
        yield name, dsn
    finally:
        _sh("docker", "rm", "-f", name)


def _backup(container: str, dest: Path, stamp: str) -> Path:
    """Exactly what deploy/backup.sh does: plain pg_dump of intel, gzipped."""
    dump = _sh("docker", "exec", container, "pg_dump", "-U", "intel", "--no-owner", "intel").stdout
    out = dest / f"intel_{stamp}.sql.gz"
    out.write_bytes(gzip.compress(dump.encode()))
    return out


def _drill(backups: Path, metrics: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "INTEL_BACKUP_DIR": str(backups), "INTEL_METRICS_DIR": str(metrics)}
    return _sh("bash", str(ROOT / "deploy" / "restore_drill.sh"), env=env, timeout=300)


def test_drill_restores_and_verifies_a_real_backup_then_catches_a_tampered_one(source_db, tmp_path):
    from intel.serve.db import connect, migrate
    from intel.serve.registry import Registry
    from tests.test_serve import drop

    container, dsn = source_db
    migrate(dsn)
    (tmp_path / "inbox").mkdir()
    reg = Registry(dsn, tmp_path / "inbox", tmp_path / "store")
    drop(tmp_path, reg.inbox, "cs-drill")
    (it,) = reg.scan_inbox()
    reg.approve(it.approval_id, "owner", "telegram")

    backups, metrics = tmp_path / "backups", tmp_path / "metrics"
    backups.mkdir()
    metrics.mkdir()
    _backup(container, backups, "2026-10-08_025000")
    ok = _drill(backups, metrics)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "audit entries (chain verified)" in ok.stdout and "drill OK" in ok.stdout
    prom = (metrics / "intel_restore_drill.prom").read_text()
    assert "intel_restore_drill_ok 1" in prom and "intel_restore_drill_restore_seconds" in prom

    with connect(dsn) as c:  # someone edits the record (bypassing the trigger), then the nightly backup runs
        c.execute("alter table audit_log disable trigger audit_append_only")
        c.execute("update audit_log set actor='someone-else' where action='approved'")
        c.execute("alter table audit_log enable trigger audit_append_only")
    time.sleep(1.1)  # the drill picks the newest backup by modification time
    _backup(container, backups, "2026-10-09_025000")
    bad = _drill(backups, metrics)
    assert bad.returncode == 1 and "audit chain broken at entry" in bad.stdout
    assert "intel_restore_drill_ok 0" in (metrics / "intel_restore_drill.prom").read_text()


def test_an_older_backup_is_checked_against_its_own_schema(source_db, tmp_path):
    """A dump taken before migrations 0005/0006 has no model_reference / audit_log, and that is not a failure."""
    container, _ = source_db
    _sh("docker", "exec", container, "psql", "-U", "intel", "-d", "postgres", "-c", "drop database if exists old")
    _sh("docker", "exec", container, "psql", "-U", "intel", "-d", "postgres", "-c", "create database old")
    from intel.serve.db import MIGRATIONS

    for f in sorted(MIGRATIONS.glob("*.sql"))[:4]:  # the schema as of migration 0004
        sql = f.read_text() + f"\ninsert into schema_migrations (version) values ('{f.stem}');"
        if f.stem == "0001_registry":
            sql = (
                "create table schema_migrations (version text primary key, applied_at timestamptz default now());\n"
                + sql
            )
        r = _sh(
            "docker", "exec", "-i", container, "psql", "-v", "ON_ERROR_STOP=1", "-U", "intel", "-d", "old", input=sql
        )
        assert r.returncode == 0, r.stderr
    _sh(
        "docker",
        "exec",
        container,
        "psql",
        "-U",
        "intel",
        "-d",
        "old",
        "-c",
        "insert into model_package (name, version, file_name, sha256, purpose, verdict, status) "
        "values ('m','v','f','s','baseline','baseline','approved')",
    )
    backups, metrics = tmp_path / "b", tmp_path / "m"
    backups.mkdir()
    metrics.mkdir()
    dump = _sh("docker", "exec", container, "pg_dump", "-U", "intel", "--no-owner", "old").stdout
    (backups / "intel_old.sql.gz").write_bytes(gzip.compress(dump.encode()))
    r = _drill(backups, metrics)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "schema 0004_reach_outcome" in r.stdout and "no audit trail yet" in r.stdout


def test_drill_fails_without_a_backup(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "m").mkdir()
    r = _drill(tmp_path / "b", tmp_path / "m")
    assert r.returncode == 1 and "no backup found" in r.stdout
