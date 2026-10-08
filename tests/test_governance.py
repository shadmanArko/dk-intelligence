"""ADR 0019: the lifecycle the database enforces, final decisions, append-only history, and the hash-chained audit."""

from __future__ import annotations

import itertools

import psycopg
import pytest

from intel.serve.db import MIGRATIONS, connect
from intel.serve.lifecycle import STATUSES, allowed, audit_verify, stage
from tests.test_serve import DSN, client, db, drop, reg  # noqa: F401  (fixtures)

pytestmark = pytest.mark.skipif(not DSN, reason="INTEL_TEST_PG_DSN not set")


def _approved(reg, tmp_path, version):  # noqa: F811
    drop(tmp_path, reg.inbox, version)
    (it,) = reg.scan_inbox()
    reg.approve(it.approval_id, "owner", "telegram")
    return it


def test_database_allows_exactly_the_python_lifecycle(reg):  # noqa: F811
    """Every (old, new) status pair, tried against the trigger; the allowed set must equal lifecycle.TRANSITIONS."""
    db_allowed = set()
    with connect(reg.dsn) as c:
        for i, (old, new) in enumerate(itertools.product(STATUSES, STATUSES)):
            if old == new:
                continue
            pid = c.execute(
                "insert into model_package (name, version, file_name, sha256, purpose, verdict, status) "
                "values ('m', %s, 'f', %s, 'candidate', 'pass', %s) returning id",
                (f"v{i}", f"sha{i}", old),
            ).fetchone()["id"]
            try:
                with c.transaction():
                    c.execute("update model_package set status=%s where id=%s", (new, pid))
                db_allowed.add((old, new))
            except psycopg.errors.CheckViolation:
                pass
    py_allowed = {(o, n) for o, n in itertools.product(STATUSES, STATUSES) if o != n and allowed(o, n)}
    assert db_allowed == py_allowed == {("pending_approval", s) for s in ("approved", "rejected", "expired")}


def test_decisions_are_final_and_activations_append_only(reg, tmp_path):  # noqa: F811
    it = _approved(reg, tmp_path, "cs-a")
    with connect(reg.dsn) as c, pytest.raises(psycopg.errors.CheckViolation, match="decisions are final"):
        c.execute("update approval set decision='rejected' where id=%s", (it.approval_id,))
    with connect(reg.dsn) as c, pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
        c.execute("delete from model_activation")
    with connect(reg.dsn) as c, pytest.raises(psycopg.errors.CheckViolation, match="illegal lifecycle"):
        c.execute("update model_package set status='rejected' where id=%s", (it.package_id,))  # un-approve
    with connect(reg.dsn) as c:  # bookkeeping on an open approval stays possible
        c.execute("update approval set telegram_message_id=42 where id=%s", (it.approval_id,))


def test_every_governance_action_is_audited_in_order_and_the_chain_verifies(reg, tmp_path):  # noqa: F811
    _approved(reg, tmp_path, "cs-a")
    _approved(reg, tmp_path, "cs-b")
    reg.rollback("content_scorer", "owner", "web")
    drop(tmp_path, reg.inbox, "cs-c")
    (it,) = reg.scan_inbox()
    reg.reject(it.approval_id, "owner", "telegram", "not_better")
    with connect(reg.dsn) as c:
        log = [(r["action"], r["subject"], r["actor"]) for r in c.execute("select * from audit_log order by id")]
        chain = audit_verify(c)
    assert [a for a, _, _ in log] == [
        "package_received",
        "approval_requested",
        "approved",
        "activated",
        "package_received",
        "approval_requested",
        "approved",
        "activated",
        "rolled_back",
        "package_received",
        "approval_requested",
        "rejected",
    ]
    assert log[2] == ("approved", "content_scorer cs-a", "owner") and log[8][1] == "content_scorer cs-a"
    assert chain == {"ok": True, "entries": 12, "first_bad": None}


def test_a_failed_action_leaves_no_audit_entry(reg, tmp_path):  # noqa: F811
    it = _approved(reg, tmp_path, "cs-a")
    with connect(reg.dsn) as c:
        before = c.execute("select count(*) n from audit_log").fetchone()["n"]
    with pytest.raises(Exception, match="already approved"):
        reg.approve(it.approval_id, "someone", "web")
    with connect(reg.dsn) as c:
        assert c.execute("select count(*) n from audit_log").fetchone()["n"] == before


@pytest.mark.parametrize("tamper", ["edit", "delete"])
def test_tampering_breaks_the_chain_where_it_happened(reg, tmp_path, tamper):  # noqa: F811
    _approved(reg, tmp_path, "cs-a")
    with connect(reg.dsn) as c:  # a superuser can bypass triggers; the hashes still tell
        c.execute("alter table audit_log disable trigger audit_append_only")
        if tamper == "edit":
            c.execute("update audit_log set actor='someone-else' where action='approved'")
        else:
            c.execute("delete from audit_log where action='approval_requested'")
        c.execute("alter table audit_log enable trigger audit_append_only")
        bad = audit_verify(c)
        approved_id = c.execute("select id from audit_log where action='approved'").fetchone()["id"]
    assert bad["ok"] is False and bad["first_bad"] == approved_id


def test_history_is_backfilled_when_governance_is_introduced(db, tmp_path):  # noqa: F811
    """A database that had packages, decisions and activations before migration 0006 starts its chain with them."""
    from intel.serve.registry import Registry

    with connect(db) as c:  # back to the state before 0006: drop what it created (test database only)
        c.execute("drop table audit_log cascade")
        for fn in (
            "audit_chain",
            "refuse_change",
            "audit_verify",
            "package_status_rule",
            "approval_decided_once",
            "audit_row_hash",
        ):
            c.execute(f"drop function {fn} cascade")
        c.execute("delete from schema_migrations where version='0006_governance'")
    (tmp_path / "inbox").mkdir()
    reg_ = Registry(db, tmp_path / "inbox", tmp_path / "store")
    (it,) = _scan_without_audit(reg_)
    _approve_without_audit(db, it.approval_id)
    from intel.serve.db import migrate

    assert migrate(db) == ["0006_governance"]
    with connect(db) as c:
        rows = c.execute("select action, detail from audit_log order by id").fetchall()
        assert audit_verify(c)["ok"]
    assert [r["action"] for r in rows] == ["package_received", "approved", "activated"]
    assert all(r["detail"]["backfilled"] for r in rows)


def _scan_without_audit(reg_):
    """Intake as the pre-0006 code did (no audit table yet): insert the package and its approval directly."""
    import json

    from intel.serve.registry import Intake

    with connect(reg_.dsn) as c:
        pid = c.execute(
            "insert into model_package (name, version, file_name, sha256, purpose, verdict, status, metadata) "
            "values ('content_scorer','cs-old','f','sha-old','baseline','baseline','pending_approval',%s) returning id",
            (json.dumps({}),),
        ).fetchone()["id"]
        aid = c.execute(
            "insert into approval (package_id, expires_at) values (%s, now() + interval '7 days') returning id", (pid,)
        ).fetchone()["id"]
    return [Intake(pid, "pending_approval", "content_scorer", "cs-old", [], aid)]


def _approve_without_audit(dsn, approval_id):
    with connect(dsn) as c:
        a = c.execute("select package_id from approval where id=%s", (approval_id,)).fetchone()
        c.execute(
            "update approval set decision='approved', decided_at=now(), decided_by='owner', channel='web' where id=%s",
            (approval_id,),
        )
        c.execute("update model_package set status='approved' where id=%s", (a["package_id"],))
        c.execute(
            "insert into model_activation (name, package_id, activated_by, reason) "
            "values ('content_scorer', %s, 'web:owner', 'approval')",
            (a["package_id"],),
        )


def test_stage_names():
    assert stage("approved", True) == "production" and stage("approved", False) == "retired"
    assert stage("pending_approval", False) == "candidate" and stage("incompatible", False) == "incompatible"
    assert (MIGRATIONS / "0006_governance.sql").exists()


def test_audit_page_export_and_metrics(client, tmp_path):  # noqa: F811
    st = client.app.state.intel
    _approved(st.registry, tmp_path, "cs-web")
    page = client.get("/audit").text
    assert "Audit trail intact: 4 entries" in page and "in production" in page and "cs-web" in page
    body = client.get("/v1/audit").json()
    assert body["chain"]["ok"] and body["packages"][0]["stage"] == "production"
    assert [e["action"] for e in body["entries"]][:2] == ["activated", "approved"]  # newest first
    metrics = client.get("/metrics").text
    assert "intel_audit_chain_ok 1.0" in metrics and "intel_audit_entries 4.0" in metrics


# ---------- model cards v2 ----------


def _meta(name="content_scorer", metrics=True):
    test = (
        {
            "balanced_accuracy": 0.69,
            "balanced_accuracy_ci95": [0.6, 0.78],
            "roc_auc": 0.74,
            "brier": 0.22,
            "n": 75,
            "confusion_matrix": [[19, 20], [4, 32]],
            "per_platform": {"facebook": {"n": 48, "balanced_accuracy": 0.65}},
        }
        if metrics
        else {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": 542}
    )
    return {
        "name": name,
        "version": "v1",
        "purpose": "candidate",
        "question": "Q?",
        "created_at": "2026-10-08",
        "judge": {"verdict": "pass", "gates": [{"gate": "better", "status": "pass", "detail": "d"}]},
        "scores": {"challenger": test, "champion": {**test, "balanced_accuracy": 0.63 if metrics else None}},
        "model": {"family": "logreg", "features": "champion", "threshold": 0.52},
        "data": {"pull_id": "p1", "market_id": "m1", "split": {"train": 300}},
        "git": {"commit": "abcdef1234567", "dirty": False},
        "runtime": {"python": "3.13", "scikit_learn": "1.9"},
    }


def test_model_card_v2_has_purpose_lineage_results_calibration_and_limits(tmp_path):
    import json as _json

    import pandas as pd

    from intel.package import model_card

    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    pd.DataFrame({"challenger_p": [0.1, 0.3, 0.5, 0.55, 0.7, 0.9], "label": [0, 0, 1, 0, 1, 1]}).to_csv(
        run / "test_predictions.csv", index=False
    )
    (run / "reference.json").write_text(
        _json.dumps(
            {
                "n_train": 300,
                "base_rate": 0.46,
                "features": {"platform": {"categories": ["facebook", "instagram"], "shares": [0.64, 0.36, 0.0]}},
            }
        )
    )
    q = tmp_path / "quality"
    q.mkdir()
    (q / "warehouse-p1.json").write_text(
        _json.dumps(
            {
                "card": {"a": {}, "b": {}},
                "findings": [{"severity": "warning", "table": "a", "check": "c", "detail": "d"}],
            }
        )
    )
    card = model_card(_meta(), run, q)
    for section in (
        "## Intended use",
        "## Out of scope",
        "## Lineage",
        "## Training data",
        "## Results",
        "## Calibration (test set)",
        "## Judge",
        "## Limitations",
        "## Ethical considerations",
    ):
        assert section in card, section
    assert "Platforms it never saw in training" in card  # out-of-scope text from the plug-in
    assert "sealed files match the lock" in card and "2 tables checked: 0 errors, 1 warnings" in card
    assert "0.69 (95% CI 0.6-0.78)" in card and "| facebook | 48 | 0.65 |" in card
    assert "platforms: facebook 64%, instagram 36%" in card
    assert "| 0.4-0.6 | 2 | 0.53 | 0.50 |" in card  # 2 test posts predicted 0.5 / 0.55, one positive
    assert "Never used to target or exclude people by ethnicity" in card


def test_model_card_for_an_insights_model_without_classifier_metrics(tmp_path):
    from intel.package import model_card

    card = model_card(_meta("community_map", metrics=False), None, None)
    assert "## Results" not in card and "## Calibration" not in card  # nothing invented
    assert "Chooses AREAS" not in card and "it chooses AREAS, from public statistics" in card
    assert "Data checks | not recorded" in card


# ---------- reproducibility ----------


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    from intel.models.content_scorer.run import run as train_run
    from tests.quality_fixtures import training_snapshots

    root = tmp_path_factory.mktemp("repro")
    pull, market = training_snapshots(root)
    run_dir = train_run(pull, market, root / "runs", None, log=lambda *_: None)
    return root, run_dir


def test_training_is_deterministic_and_reproducible_from_its_lineage(trained):
    from intel.reproduce import reproduce

    root, run_dir = trained
    r = reproduce(run_dir, root)
    assert r["identical"], r["differences"]
    assert r["max_prediction_difference"] == 0.0 and r["test_posts"] > 20
    assert r["sealed_code"] == "matches the lock" and r["original"] != r["reproduced"]  # a new run, same result


def test_reproduction_reports_what_differs(trained, tmp_path):
    import shutil

    import pandas as pd

    from intel.reproduce import compare_runs

    root, run_dir = trained
    copy = tmp_path / "tampered"
    shutil.copytree(run_dir, copy)
    tp = pd.read_csv(copy / "test_predictions.csv")
    tp.loc[0, "challenger_p"] += 0.01
    tp.to_csv(copy / "test_predictions.csv", index=False)
    r = compare_runs(run_dir, copy)
    assert not r["identical"] and r["differences"] == ["test predictions differ by up to 1.00e-02"]


def test_reproduce_refuses_what_it_cannot_honestly_redo(trained, tmp_path):
    import json as _json
    import shutil

    from intel.reproduce import reproduce

    root, run_dir = trained
    other = tmp_path / "insights"
    other.mkdir()
    (other / "report.json").write_text(_json.dumps({"kind": "community_map_run"}))
    with pytest.raises(ValueError, match="only scorer runs"):
        reproduce(other, root)
    gone = tmp_path / "data"
    shutil.copytree(root / "market", gone / "market")  # the pull was pruned
    with pytest.raises(FileNotFoundError, match="no longer kept"):
        reproduce(run_dir, gone)
