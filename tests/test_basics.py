import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest

from intel.pull import ColumnSpec, arrow_type, convert, list_pulls, prune, resolve_pull, verify_pull
from intel.settings import Settings, redact
from intel.tunnel import port_open, ssh_command


def test_redact_hides_password_including_encoded():
    assert redact("postgresql://u:s3cr%40t@127.0.0.1:5435/db") == "postgresql://u:***@127.0.0.1:5435/db"
    assert redact("postgresql://u@h/db") == "postgresql://u@h/db"
    assert redact(None) == "<unset>"


def test_settings_port_and_tilde(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here
    s = Settings(dk_warehouse_dsn="postgresql://u:p@127.0.0.1:5435/warehouse", dk_warehouse_ssh_key="~/.ssh/k")
    assert s.warehouse_local_port == 5435
    assert not str(s.dk_warehouse_ssh_key).startswith("~")


def test_arrow_types():
    assert arrow_type(1700, 10, 2) == pa.decimal128(10, 2)
    assert arrow_type(1700, None, None) == pa.string()  # unconstrained numeric stays exact as text
    assert arrow_type(1184) == pa.timestamp("us", tz="UTC")
    assert arrow_type(999_999) == pa.string()  # unknown -> text


def test_convert():
    js = ColumnSpec("j", "jsonb", 3802, pa.string())
    assert convert({"b": 1, "a": "ü"}, js) == '{"a": "ü", "b": 1}'
    txt = ColumnSpec("n", "numeric", 1700, pa.string())
    assert convert(Decimal("1E+2"), txt) == "100"
    ts = ColumnSpec("t", "timestamptz", 1184, pa.timestamp("us", tz="UTC"))
    berlin = dt.timezone(dt.timedelta(hours=2))
    assert convert(dt.datetime(2026, 10, 7, 12, tzinfo=berlin), ts).hour == 10
    assert convert(None, ts) is None


def test_ssh_command_is_forward_only():
    cmd = ssh_command("intel@host", Path("/k"), 5435)
    assert "-N" in cmd and "5435:127.0.0.1:5432" in cmd and "BatchMode=yes" in cmd


def test_port_open_false_for_unused_port():
    assert port_open(1) is False


def _fake_pull(root: Path, pid: str) -> Path:
    import pyarrow.parquet as pq

    from intel.pull import sha256_file

    d = root / pid
    d.mkdir(parents=True)
    pq.write_table(pa.table({"x": [1, 2]}), d / "t.parquet")
    (d / "manifest.json").write_text(json.dumps({"tables": {"t": {"file": "t.parquet", "rows": 2}}}))
    (d / "SHA256SUMS").write_text(f"{sha256_file(d / 't.parquet')}  t.parquet\n")
    return d


def test_prune_keeps_newest_and_removes_partials(tmp_path):
    for pid in ["20261001T000000Z", "20261002T000000Z", "20261003T000000Z"]:
        _fake_pull(tmp_path, pid)
    (tmp_path / "20261004T000000Z.partial").mkdir()
    removed = prune(tmp_path, keep=2)
    assert [p.name for p in removed] == ["20261001T000000Z"]
    assert [p.name for p in list_pulls(tmp_path)] == ["20261002T000000Z", "20261003T000000Z"]
    assert not (tmp_path / "20261004T000000Z.partial").exists()


def test_verify_detects_tampering(tmp_path):
    d = _fake_pull(tmp_path, "20261001T000000Z")
    assert verify_pull(d) == []
    (d / "t.parquet").write_bytes(b"changed")
    assert any("checksum" in p for p in verify_pull(d))


def test_resolve_pull_latest(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_pull(tmp_path)
    _fake_pull(tmp_path, "20261001T000000Z")
    (tmp_path / "LATEST").write_text("20261001T000000Z\n")
    assert resolve_pull(tmp_path).name == "20261001T000000Z"
