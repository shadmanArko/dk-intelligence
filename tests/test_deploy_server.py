"""deploy/server (ADR 0044): secret files become the settings the Sunday run reads, the run script only starts a due
run after the preflight, and the Compose file is valid and hardened."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "deploy" / "server"
SH = shutil.which("sh") or "/bin/sh"


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MIP_", "INTEL_", "DK_"))}
    return {**env, "INTEL_SECRETS_DIR": str(tmp_path / "secrets"), **extra}


def test_entrypoint_builds_dsns_from_secret_files(tmp_path):
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "mip_reader_password").write_text("p@ss:w/rd%\n")
    (sec / "rag_builder_password").write_text("rb\n")
    (sec / "dk_warehouse_dsn").write_text("postgresql://intelligence_reader:x@127.0.0.1:5435/warehouse\n")
    (sec / "dk_warehouse_ssh_key").write_text("k")
    (sec / "dk_inbox_ssh_key").write_text("")  # empty = unset
    out = subprocess.run(
        [SH, str(SERVER / "entrypoint.sh"), "env"],
        env=_env(tmp_path, MIP_PG_HOST="mip-postgres", RAG_PG_HOST="dk-rag", PATH=os.environ["PATH"]),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    got = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    assert got["MIP_READER_DSN"] == "postgresql://mip_reader:p%40ss%3Aw%2Frd%25@mip-postgres:5432/mip"
    assert got["INTEL_RAG_ADMIN_DSN"] == "postgresql://rag_builder:rb@dk-rag:5432/rag"
    assert got["DK_WAREHOUSE_DSN"].endswith("@127.0.0.1:5435/warehouse")
    assert got["DK_WAREHOUSE_SSH_KEY"] == str(sec / "dk_warehouse_ssh_key")
    assert "DK_INBOX_SSH_KEY" not in got


def _fake_intel(tmp_path: Path, due: bool, preflight_rc: int) -> Path:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "intel").write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {tmp_path}/calls\n'
        'case "$1" in\n'
        f"  weekly-status) echo 'this week: {'DUE - no run yet' if due else 'done 20261122T110000Z'}' ;;\n"
        f"  server-preflight) exit {preflight_rc} ;;\n"
        "  weekly) exit 0 ;;\n"
        "esac\n"
    )
    (bin_ / "intel").chmod(0o755)
    return bin_


@pytest.mark.parametrize(
    ("due", "preflight_rc", "rc", "ran_weekly"),
    [(False, 0, 0, False), (True, 75, 75, False), (True, 0, 0, True)],
)
def test_run_script(tmp_path, due, preflight_rc, rc, ran_weekly):
    bin_ = _fake_intel(tmp_path, due, preflight_rc)
    env = _env(tmp_path, PATH=f"{bin_}:{os.environ['PATH']}")
    r = subprocess.run([SH, str(SERVER / "run-weekly.sh")], env=env, capture_output=True, text=True, check=False)
    calls = (tmp_path / "calls").read_text()
    assert r.returncode == rc
    assert ("weekly --if-due --wait-hours 8" in calls) is ran_weekly
    assert ("server-preflight" in calls) is due


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker compose")
def test_compose_file_is_valid_and_hardened(tmp_path):
    import yaml

    sec = tmp_path / "secrets"
    sec.mkdir()
    for name in ("mip_reader_password", "rag_builder_password", "dk_warehouse_dsn"):
        (sec / name).write_text("")
    for name in ("dk_warehouse_ssh_key", "dk_inbox_ssh_key"):
        (sec / name).write_text("")
    kh = tmp_path / "known_hosts"
    kh.write_text("")
    envf = tmp_path / "weekly.env"
    envf.write_text(
        "GIT_SHA=abc1234\nMIP_DB_NETWORK=mipnet\nMIP_PG_HOST=mip-postgres\nRAG_DB_NETWORK=ragnet\nRAG_PG_HOST=dk-rag\n"
        "INTEL_API_URL=https://api.example\nDK_WAREHOUSE_SSH=intel@vps\nDK_INBOX_SSH=intelinbox@vps\n"
        f"INTEL_WEEKLY_SECRETS={sec}\nINTEL_WEEKLY_KNOWN_HOSTS={kh}\n"
    )
    r = subprocess.run(
        ["docker", "compose", "--env-file", str(envf), "-f", str(SERVER / "compose.yaml"), "config"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0 and "docker daemon" in r.stderr.lower():
        pytest.skip("docker daemon not running")
    assert r.returncode == 0, r.stderr
    svc = yaml.safe_load(r.stdout)["services"]["weekly"]
    assert svc["cap_drop"] == ["ALL"] and "no-new-privileges:true" in svc["security_opt"]
    assert svc["read_only"] is True and svc["user"] == "10004:10004" and "ports" not in svc
    assert svc["image"] == "intel-weekly:abc1234" and svc["mem_limit"]
    assert {v["target"]: v.get("read_only", False) for v in svc["volumes"]} == {
        "/data": False,
        "/mip-repo": True,
        "/mip-data": True,
        "/home/intel/.ssh/known_hosts": True,
    }
    assert svc["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_systemd_units():
    svc = (SERVER / "intel-weekly.service").read_text()
    timer = (SERVER / "intel-weekly.timer").read_text()
    assert "Type=oneshot" in svc and "--name intel-weekly-run weekly" in svc and "SuccessExitStatus=75" in svc
    assert "[Install]" not in svc  # only the timer is enabled
    assert "OnCalendar=Sun *-*-* 12:00:00 Europe/Berlin" in timer and "Persistent=true" in timer


def test_rebase_mlflow_moves_only_the_artifact_prefix(tmp_path):
    import importlib.util
    import sqlite3

    spec = importlib.util.spec_from_file_location("rebase_mlflow", SERVER / "rebase_mlflow.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db = tmp_path / "mlflow.db"
    con = sqlite3.connect(db)
    con.execute("create table experiments (experiment_id integer, artifact_location varchar(256))")
    con.execute("create table runs (run_uuid varchar(32), artifact_uri varchar(200), status varchar(20))")
    old = "/Users/me/dk intelligence/mlruns"
    con.executemany("insert into experiments values (?, ?)", [(0, f"{old}/0"), (1, "/elsewhere/mlruns/1")])
    con.executemany("insert into runs values (?, ?, ?)", [("a", f"{old}/1/a/artifacts", "FINISHED"), ("b", old, "X")])
    con.execute("insert into runs values ('c', '/Users/me/dk intelligence/mlruns-other/1', 'X')")  # not under it
    con.commit()
    con.close()
    assert mod.rebase(str(db), old + "/", "/data/mlruns") == {
        "experiments.artifact_location": 1,
        "runs.artifact_uri": 2,
    }
    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("select artifact_location from experiments order by 1")] == [
        "/data/mlruns/0",
        "/elsewhere/mlruns/1",
    ]
    assert [r[0] for r in con.execute("select artifact_uri from runs order by run_uuid")] == [
        "/data/mlruns/1/a/artifacts",
        "/data/mlruns",
        "/Users/me/dk intelligence/mlruns-other/1",
    ]
