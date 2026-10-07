"""Pipeline definition consistency, the Mac Control Room (guards, token, run start) and the VPS diagram API."""

import json

import pytest
from fastapi.testclient import TestClient

from intel.pipeline import EDGES, NODES, STEPS, definition
from intel.settings import Settings


def test_every_step_is_drawn_and_every_edge_connects_nodes():
    ids = {n["id"] for n in NODES}
    assert {n["step"] for n in NODES if n["step"]} == set(STEPS)  # a new step without a box fails here
    assert all(a in ids and b in ids for a, b in EDGES)
    assert len({(n["col"], n["row"]) for n in NODES}) == len(NODES)  # no two boxes on the same spot
    assert all(n["desc"] for n in definition()["nodes"])


@pytest.fixture
def control(tmp_path, monkeypatch):
    from intel import control as ctl

    started = []
    monkeypatch.setattr(ctl.subprocess, "Popen", lambda *a, **k: started.append(a[0]))
    s = Settings(_env_file=None, intel_data_dir=tmp_path / "data", intel_api_url="https://x.ts.net")
    app = ctl.create_control_app(s, tmp_path, 8765)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    return c, started, tmp_path


def test_control_page_guards_and_run(control):
    c, started, tmp = control
    page = c.get("/")
    assert page.status_code == 200 and 'data-run="/api/run"' in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    token = page.text.split('data-token="')[1].split('"')[0]
    assert c.get("/", headers={"host": "evil.example:8765"}).status_code == 400  # DNS rebinding
    assert c.post("/api/run").status_code == 403  # no token
    assert c.post("/api/run", headers={"x-intel-token": "guess"}).status_code == 403
    assert c.post("/api/run", headers={"x-intel-token": token}).json() == {"started": True}
    assert started and started[0][-1].endswith("scripts/weekly.sh")
    assert c.get("/static/pipeline.js").status_code == 200 and c.get("/static/../control.py").status_code == 404


def test_control_state_detects_interrupted_run(control):
    c, _, tmp = control
    w = tmp / "data" / "weekly"
    w.mkdir(parents=True)
    (w / "current.json").write_text(json.dumps({"run_id": "20261011T100000Z", "state": "running", "steps": {}}))
    st = c.get("/api/state").json()
    assert st["running"] is False and st["stalled"] is True and st["due_text"].startswith("DUE")
    assert c.get("/api/pipeline").json()["edges"]
