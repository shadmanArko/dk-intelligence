"""intel-api: registry, approvals, rollback, scoring, web protections, Telegram bot.

Needs a Postgres where the user may CREATE DATABASE (INTEL_TEST_PG_DSN); each test gets a fresh database."""

import json
import os
import shutil
import uuid
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg import sql

from intel.champions import harness_v1 as champ
from intel.models.content_scorer.features import MarketContext, build_features
from intel.models.content_scorer.train import CHAMPION_COLS
from intel.package import build_package
from intel.serve import registry as registry_mod
from intel.serve.db import connect, migrate
from intel.serve.registry import Registry, RegistryError, load_bundle

DSN = os.environ.get("INTEL_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="INTEL_TEST_PG_DSN not set")
REPO = Path(__file__).resolve().parents[1]
CTX = MarketContext(cues={"halal": ["halal"]}, cue_lift={"halal": 0.1}, feast_days=["2026-05-27"])


@pytest.fixture
def db():
    name = f"intel_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    dsn = psycopg.conninfo.make_conninfo(DSN, dbname=name)
    migrate(dsn)
    yield dsn
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


def _posts(n=40):
    rng = np.random.default_rng(0)
    t0 = pd.Timestamp("2026-05-01 10:00", tz="UTC")
    return pd.DataFrame(
        {
            "platform": rng.choice(["facebook", "instagram"], n),
            "content_type": rng.choice(["reel", "image"], n),
            "posted_at": [t0 + pd.Timedelta(hours=int(h)) for h in rng.integers(0, 2000, n)],
            "caption": [("halal kacchi " * int(k)) for k in rng.integers(0, 5, n)],
        }
    )


def make_run(tmp: Path, version: str, verdict="baseline") -> Path:
    df = _posts()
    X = build_features(df, CTX)[CHAMPION_COLS]
    pipe = champ.recipe().fit(X, [0, 1] * (len(df) // 2))
    run = tmp / f"run_{version}"
    run.mkdir()
    joblib.dump(
        {
            "version": version,
            "pipeline": pipe,
            "columns": CHAMPION_COLS,
            "threshold": 0.49,
            "context": CTX.to_dict(),
            "inputs": list(df.columns),
        },
        run / "model.joblib",
    )
    score = {"balanced_accuracy": 0.62, "roc_auc": 0.7, "brier": 0.21, "n": 75}
    (run / "report.json").write_text(
        json.dumps(
            {
                "kind": "content_scorer_run",
                "version": version,
                "pull_id": "P",
                "market_id": "M",
                "split": {"train": 1, "test": 1},
                "judge": {"verdict": verdict, "gates": [{"gate": "better", "status": "pass", "detail": "d"}]},
                "challenger": {
                    "model": "champion_recipe",
                    "params": {},
                    "features": "champion",
                    "threshold": 0.49,
                    "test": score,
                },
                "champion": {"model": "harness", "test": score},
            }
        )
    )
    return run


def drop(tmp: Path, inbox: Path, version: str, **kw) -> Path:
    tgz = build_package(make_run(tmp, version, kw.pop("verdict", "baseline")), tmp / "pkg", REPO, **kw)
    shutil.copy(tgz, inbox)
    shutil.copy(tgz.with_name(tgz.name + ".sha256"), inbox)
    return inbox / tgz.name


@pytest.fixture
def reg(db, tmp_path):
    (tmp_path / "inbox").mkdir()
    return Registry(db, tmp_path / "inbox", tmp_path / "store")


def test_intake_statuses(reg, tmp_path):
    drop(tmp_path, reg.inbox, "cs-a")
    drop(tmp_path, reg.inbox, "cs-test", verdict="fail", allow_fail=True)
    bad = drop(tmp_path, reg.inbox, "cs-bad")
    data = bytearray(bad.read_bytes())
    data[-40] ^= 0xFF
    bad.write_bytes(bytes(data))
    got = {i.version: i for i in reg.scan_inbox()}
    assert got["cs-a"].status == "pending_approval" and got["cs-a"].approval_id
    assert got["cs-test"].status == "test_only" and got["cs-test"].approval_id is None
    bad_status = next(i for i in got.values() if i.version not in ("cs-a", "cs-test"))
    assert bad_status.status == "invalid"
    assert (reg.store / "content_scorer" / "cs-a" / "model.joblib").exists()
    assert reg.scan_inbox() == []  # idempotent


def test_incompatible_code(reg, tmp_path, monkeypatch):
    drop(tmp_path, reg.inbox, "cs-old")
    monkeypatch.setattr(registry_mod, "code_hashes", lambda name: {"models/content_scorer/features.py": "other"})
    (it,) = reg.scan_inbox()
    assert it.status == "incompatible" and "feature code differs" in it.problems[0]


def test_approve_reject_rollback(reg, tmp_path):
    drop(tmp_path, reg.inbox, "cs-1")
    (a1,) = reg.scan_inbox()
    reg.approve(a1.approval_id, "me", "telegram")
    assert reg.active()["content_scorer"]["version"] == "cs-1"
    with pytest.raises(RegistryError, match="already approved"):
        reg.reject(a1.approval_id, "me", "web", "other")
    with pytest.raises(RegistryError, match="first approved"):
        reg.rollback("content_scorer", "me", "web")

    drop(tmp_path, reg.inbox, "cs-2")
    drop(tmp_path, reg.inbox, "cs-3")
    a2, a3 = reg.scan_inbox()
    reg.approve(a2.approval_id, "me", "web")
    reg.reject(a3.approval_id, "me", "telegram", "suspicious")
    assert reg.active()["content_scorer"]["version"] == "cs-2"
    assert reg.rollback("content_scorer", "me", "telegram")["version"] == "cs-1"
    assert reg.active()["content_scorer"]["version"] == "cs-1"
    with pytest.raises(RegistryError, match="unknown reason"):
        reg.reject(a3.approval_id, "me", "web", "because")


def test_expiry(reg, tmp_path):
    drop(tmp_path, reg.inbox, "cs-x")
    (a,) = reg.scan_inbox()
    with connect(reg.dsn) as c:
        c.execute("update approval set expires_at = now() - interval '1 minute' where id=%s", (a.approval_id,))
    with pytest.raises(RegistryError, match="expired"):
        reg.approve(a.approval_id, "me", "web")
    assert [r["id"] for r in reg.expire_due()] == [a.approval_id]
    assert reg.package(a.package_id)["status"] == "expired"


def test_load_bundle_refuses_tampered_model(reg, tmp_path):
    drop(tmp_path, reg.inbox, "cs-t")
    (a,) = reg.scan_inbox()
    pkg = reg.package(a.package_id)
    assert load_bundle(pkg)["version"] == "cs-t"
    (Path(pkg["store_path"]) / "model.joblib").write_bytes(b"not a model")
    with pytest.raises(RegistryError, match="hash mismatch"):
        load_bundle(pkg)


# ---------- API + web ----------


@pytest.fixture
def client(db, tmp_path):
    from fastapi.testclient import TestClient

    from intel.serve.app import create_app
    from intel.settings import Settings

    (tmp_path / "inbox").mkdir(exist_ok=True)
    s = Settings(
        _env_file=None, intel_db_dsn=db, intel_inbox_dir=tmp_path / "inbox", intel_store_dir=tmp_path / "store"
    )
    with TestClient(create_app(s, start_workers=False)) as c:
        yield c


BODY = {
    "platform": "instagram",
    "content_type": "reel",
    "caption": "Halal kacchi #berlin",
    "planned_posted_at": "2026-10-10T17:00:00+02:00",
}


def test_predict_web_approval_and_protections(client, tmp_path):
    st = client.app.state.intel
    assert client.post("/v1/predict", json=BODY).status_code == 503
    drop(tmp_path, st.registry.inbox, "cs-web")
    (a,) = st.registry.scan_inbox()

    page = client.get("/")
    assert page.status_code == 200 and "cs-web" in page.text and page.headers["x-frame-options"] == "DENY"
    token = client.cookies.get("intel_csrf")
    url = f"/approvals/{a.approval_id}/approve"
    ts = {"Tailscale-User-Login": "owner@example.com"}
    assert client.post(url, data={"csrf": "wrong"}, headers=ts).status_code == 403  # CSRF
    assert client.post(url, data={"csrf": token}).status_code == 403  # not via tailnet
    assert client.post(url, data={"csrf": token}, headers={**ts, "Origin": "https://evil.example"}).status_code == 403
    r = client.post(url, data={"csrf": token}, headers=ts, follow_redirects=False)
    assert r.status_code == 303
    p = client.post("/v1/predict", json=BODY).json()
    assert p["model_version"] == "cs-web" and 0 <= p["probability"] <= 1
    assert p["label"] in ("likely_below_typical", "likely_at_or_above_typical") and p["top_reasons"]
    assert client.post("/v1/predict", json={**BODY, "planned_posted_at": "2026-10-10T17:00:00"}).status_code == 400
    assert client.post("/v1/predict", json={**BODY, "platform": "tiktok"}).status_code == 422
    assert client.get("/ready").json()["models"] == {"content_scorer": "cs-web"}


# ---------- Telegram bot ----------


class FakeTelegram:
    chat_id = 111

    def __init__(self):
        self.sent, self.edited, self.answers = [], [], []

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))
        return len(self.sent)

    def edit(self, mid, text, buttons=None):
        self.edited.append((mid, text, buttons))

    def answer(self, cid, text=""):
        self.answers.append(text)


def _msg(uid, text):
    return {"update_id": 1, "message": {"chat": {"id": uid}, "from": {"id": uid}, "text": text}}


def _cb(uid, data):
    return {
        "update_id": 2,
        "callback_query": {"id": "q", "from": {"id": uid}, "data": data, "message": {"message_id": 5}},
    }


def test_bot_obeys_only_owner(reg, tmp_path):
    from intel.serve.telegram import Bot

    tg = FakeTelegram()
    bot = Bot(tg, reg, "https://intel.example.ts.net")
    drop(tmp_path, reg.inbox, "cs-bot")
    (a,) = reg.scan_inbox()

    bot.handle(_cb(999, f"ap:{a.approval_id}"))  # stranger presses approve
    bot.handle(_msg(999, "/status"))
    assert tg.sent == [] and tg.edited == [] and reg.active() == {}

    bot.handle(_msg(111, "/status"))
    assert "cs-bot" in tg.sent[-1][0]
    bot.handle(_cb(111, f"rj:{a.approval_id}"))  # shows reasons, decides nothing yet
    assert reg.pending() and any("rr:" in b[0][1] for b in tg.edited[-1][2])
    bot.handle(_cb(111, f"ap:{a.approval_id}"))
    assert reg.active()["content_scorer"]["version"] == "cs-bot" and "Approved" in tg.edited[-1][1]
    bot.handle(_cb(111, f"ap:{a.approval_id}"))  # double tap
    assert "already approved" in tg.answers[-1]
    with connect(reg.dsn) as c:
        n = c.execute("select count(*) n from event_log where kind='telegram_unauthorised'").fetchone()["n"]
    assert n == 2


def test_approval_text_has_scores():
    from intel.serve.telegram import approval_text

    meta = {
        "purpose": "baseline",
        "name": "content_scorer",
        "version": "cs-1",
        "data": {"pull_id": "P"},
        "scores": {
            "challenger": {"balanced_accuracy": 0.62, "roc_auc": 0.7, "brier": 0.21, "n": 75},
            "champion": {"balanced_accuracy": 0.62, "roc_auc": 0.7, "brier": 0.21, "n": 75},
        },
        "judge": {"gates": [{"gate": "better", "status": "fail"}]},
    }
    t = approval_text(meta, 7, "https://x.ts.net")
    assert "Baseline" in t and "0.620" in t and "/approvals/7" in t and "❌ better" in t


def test_read_only_routes_write_nothing(client):
    """GET /v1/monitoring/latest and /v1/insights/{name} (ADR 0025) never compute, write or log."""
    st = client.app.state.intel

    def counts():
        with connect(st.dsn) as c:
            return {
                t: c.execute(f"select count(*) as n from {t}").fetchone()["n"]
                for t in ("kv", "event_log", "prediction", "audit_log")
            }

    before = counts()
    assert client.get("/v1/monitoring/latest").json()["status"] == "not_computed_yet"
    assert client.get("/v1/insights/funnel", params={"section": "summary"}).status_code == 404
    st.models.loaded["funnel"] = {
        "package_id": "p1",
        "version": "fn-x",
        "bundle": {"kind": "insights", "insights": {"totals": {"ordered": 2}, "sessions": 9}},
    }
    body = client.get("/v1/insights/funnel", params={"section": "summary"}).json()
    assert body["data"] == {"totals": {"ordered": "<5"}, "sessions": 9}
    assert counts() == before
    with connect(st.dsn) as c:
        c.execute("insert into kv (key, value) values ('monitoring', %s)", ('{"at": "2026-10-09T10:00:00+00:00"}',))
    assert client.get("/v1/monitoring/latest").json()["at"] == "2026-10-09T10:00:00+00:00"
