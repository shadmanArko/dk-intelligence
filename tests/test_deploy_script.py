"""deploy/deploy.sh (ADR 0014), run for real against a real git repo and a fake `docker` that records every call.

Covers: request validation, a superseded deploy, an image built from the wrong commit, a good deploy, and a failed
smoke test that rolls back to the previous image.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "a" * 64

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Fake docker: logs each call; behaviour from FAKE_* variables.
echo "docker $* | INTEL_IMAGE=${INTEL_IMAGE:-}" >> "$FAKE_LOG"
args="$*"
case "$args" in
  "compose ps -q intel-api") [[ -n "${FAKE_OLD:-}" ]] && echo "cid-old" ;;
  "inspect --format {{.Image}} cid-old") echo "sha256:oldimage" ;;
  "image inspect --format"*dk-intelligence:previous) echo "${FAKE_PREV_LABEL:-}" ;;
  "image inspect --format"*) echo "${FAKE_LABEL:-}" ;;
  *"python -c"*) echo '{"content_scorer": "cs-1"}' ;;
  *"python -"*)
    cat > /dev/null
    for a in "$@"; do [[ "$a" == EXPECT_SHA=* ]] && sha="${a#EXPECT_SHA=}"; done
    if [[ ",${FAKE_SMOKE_FAIL:-}," == *",$sha,"* ]]; then echo "FAIL smoke for $sha"; exit 1; fi
    echo "OK smoke for $sha" ;;
esac
exit 0
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def vps(tmp_path):
    repo = tmp_path / "dk-intelligence"
    (repo / "deploy").mkdir(parents=True)
    for f in ("deploy.sh", "smoke.py", "metrics.sh"):
        shutil.copy(ROOT / "deploy" / f, repo / "deploy" / f)
    (repo / "deploy" / ".env").write_text("INTEL_DB_PASSWORD=x\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "one")
    first = _git(repo, "rev-parse", "HEAD")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "two")
    head = _git(repo, "rev-parse", "HEAD")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    log, history = tmp_path / "docker.log", tmp_path / "deploys.log"
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()

    def run(request: str | None = None, *args: str, **fake: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "INTEL_DEPLOY_HISTORY": str(history),
            "INTEL_DEPLOY_SETTLE_S": "0",
            "INTEL_METRICS_DIR": str(metrics_dir),
            **fake,
        }
        if request is not None:
            env["SSH_ORIGINAL_COMMAND"] = request
        return subprocess.run(
            ["bash", str(repo / "deploy" / "deploy.sh"), *args], env=env, capture_output=True, text=True
        )

    def calls() -> list[str]:
        return log.read_text().splitlines() if log.exists() else []

    def outcomes() -> list[str]:
        return [line.split("\t")[2] for line in history.read_text().splitlines()] if history.exists() else []

    return {"run": run, "calls": calls, "outcomes": outcomes, "head": head, "first": first, "metrics": metrics_dir}


@pytest.mark.parametrize(
    "request_",
    ["rm -rf /", "deploy abc sha256:1", f"deploy {'a' * 40} {DIGEST}; reboot", "deploy  " + DIGEST],
)
def test_unexpected_requests_are_refused_without_touching_docker(vps, request_):
    r = vps["run"](request_)
    assert r.returncode == 2 and "refused" in r.stdout
    assert request_ not in r.stdout  # never echoed back
    assert vps["calls"]() == []


def test_an_older_commit_is_superseded_by_the_checked_out_one(vps):
    r = vps["run"](f"deploy {vps['first']} {DIGEST}")
    assert r.returncode == 0 and "newer deploy follows" in r.stdout
    assert vps["calls"]() == [] and vps["outcomes"]() == ["superseded"]


def test_an_image_from_another_commit_is_refused(vps):
    r = vps["run"](f"deploy {vps['head']} {DIGEST}", FAKE_LABEL="f" * 40, FAKE_OLD="1")
    assert r.returncode == 2 and "built from" in r.stdout
    assert not any("compose up" in c for c in vps["calls"]())  # nothing was switched
    assert vps["outcomes"]() == ["refused"]


def test_good_deploy_runs_the_exact_digest_and_keeps_the_previous_image(vps):
    head = vps["head"]
    r = vps["run"](f"deploy {head} {DIGEST}", FAKE_LABEL=head, FAKE_OLD="1")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = vps["calls"]()
    image = f"ghcr.io/shadmanarko/dk-intelligence@{DIGEST}"
    assert f"docker pull --quiet {image} | INTEL_IMAGE=" in calls
    assert "docker tag sha256:oldimage dk-intelligence:previous | INTEL_IMAGE=" in calls  # before switching
    assert any(c.startswith("docker compose up -d --no-build") and c.endswith(f"INTEL_IMAGE={image}") for c in calls)
    assert f"docker tag {image} dk-intelligence:current | INTEL_IMAGE=" in calls
    assert '{"content_scorer": "cs-1"}' in r.stdout  # models before the deploy were read for the smoke test
    assert vps["outcomes"]() == ["deployed"]
    prom = (vps["metrics"] / "intel_deploy.prom").read_text()  # published for the dashboard (textfile collector)
    assert f'intel_deploy_last_info{{outcome="deployed",commit="{head[:12]}"}} 1' in prom
    assert not list(vps["metrics"].glob(".*"))  # written atomically: no temp file left behind


def test_failed_smoke_test_rolls_back_to_the_previous_image(vps):
    head, prev = vps["head"], "b" * 40
    r = vps["run"](f"deploy {head} {DIGEST}", FAKE_LABEL=head, FAKE_OLD="1", FAKE_PREV_LABEL=prev, FAKE_SMOKE_FAIL=head)
    assert r.returncode == 1 and "ROLLBACK" in r.stdout and "previous version is serving" in r.stdout
    ups = [c for c in vps["calls"]() if c.startswith("docker compose up")]
    assert ups[-1].endswith("INTEL_IMAGE=dk-intelligence:previous")
    assert not any("dk-intelligence:current" in c for c in vps["calls"]())  # the failed image never becomes current
    assert vps["outcomes"]() == ["rolled_back"]


def test_manual_deploy_uses_the_ci_image_of_the_checked_out_commit(vps):
    head = vps["head"]
    r = vps["run"](None, FAKE_LABEL=head)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"docker pull --quiet ghcr.io/shadmanarko/dk-intelligence:sha-{head} | INTEL_IMAGE=" in vps["calls"]()


# ---------- deploy/smoke.py against a real running intel-api ----------


@pytest.fixture
def live_api(tmp_path):
    import socket
    import threading
    import time

    import psycopg
    import uvicorn

    from intel.serve.app import create_app
    from intel.serve.db import migrate
    from intel.settings import Settings
    from tests.test_measure import _dropdb, _newdb
    from tests.test_serve import DSN

    if not DSN:
        pytest.skip("INTEL_TEST_PG_DSN not set")
    name = _newdb()
    dsn = psycopg.conninfo.make_conninfo(DSN, dbname=name)
    migrate(dsn)
    (tmp_path / "inbox").mkdir()
    s = Settings(
        _env_file=None,
        intel_db_dsn=dsn,
        intel_inbox_dir=tmp_path / "inbox",
        intel_store_dir=tmp_path / "store",
        intel_git_sha="c" * 40,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(s, start_workers=False)
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    t.join(5)
    _dropdb(name)


def _smoke(base: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", str(ROOT / "deploy" / "smoke.py")],
        env={**os.environ, "SMOKE_BASE": base, "WAIT_S": "5", **env},
        capture_output=True,
        text=True,
    )


def test_smoke_passes_on_the_expected_version(live_api):
    url, _ = live_api
    r = _smoke(url, EXPECT_SHA="c" * 40, EXPECT_MODELS="{}")
    assert r.returncode == 0 and r.stdout.startswith("OK"), r.stdout


def test_smoke_checks_a_real_prediction_without_touching_the_ledger(live_api):
    from intel.serve.db import connect
    from tests.test_measure import Models

    url, app = live_api
    app.state.intel.models.loaded["content_scorer"] = Models().m
    r = _smoke(url, EXPECT_SHA="c" * 40, EXPECT_MODELS='{"content_scorer": "cs-t"}')
    assert r.returncode == 0 and "content_scorer" in r.stdout, r.stdout
    with connect(app.state.intel.dsn) as c:
        assert c.execute("select count(*) n from prediction").fetchone()["n"] == 0


def test_smoke_falls_back_to_predict_on_an_older_image():
    """A rollback target from before /health/deep existed is still verified through /v1/predict."""
    import http.server
    import json as _json
    import threading

    class Old(http.server.BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            data = _json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path == "/ready":
                self._send(200, {"version": "e" * 40, "models": {"content_scorer": "cs-old"}})
            else:
                self._send(404, {"detail": "Not Found"})

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self._send(200, {"probability": 0.42})

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Old)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = _smoke(
            f"http://127.0.0.1:{srv.server_port}", EXPECT_SHA="e" * 40, EXPECT_MODELS='{"content_scorer": "cs-old"}'
        )
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stdout


def test_smoke_fails_on_wrong_version_or_a_lost_model(live_api):
    url, _ = live_api
    r = _smoke(url, EXPECT_SHA="d" * 40, EXPECT_MODELS='{"content_scorer": "cs-1"}')
    assert r.returncode == 1
    assert "running version" in r.stdout and "not loaded: ['content_scorer']" in r.stdout


def test_smoke_fails_when_nothing_answers():
    r = _smoke("http://127.0.0.1:9", EXPECT_SHA="c" * 40)
    assert r.returncode == 1 and "did not answer" in r.stdout
