"""ADR 0024: data protection and responsible AI as tests. The policy file is checked against the real tables, the PII
scan against real-looking data (precise: ids, UUIDs, decimals and version numbers are not personal data), the pull
guard and the legacy minimiser against a written pull, the package gate against real model outputs, and the retention
job against a real database (with its audit entry)."""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from pathlib import Path
from types import SimpleNamespace

import joblib
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import intel.privacy as privacy_mod
from intel.privacy import PrivacyViolation, policy, pull_tables, tables
from intel.privacy.fairness import blocked_word_hits, bundle_documents, check, protected_inputs
from intel.privacy.guard import check as guard_check
from intel.privacy.guard import minimise_existing, pull_guard
from intel.privacy.pii import find_in_text, redact, scan_value
from intel.privacy.retention import rules, run_retention
from intel.privacy.ropa import ROPA_FILE, render
from intel.pull import sha256_file, verify_pull
from intel.quality.contracts import CONTRACTS
from intel.serve.db import MIGRATIONS
from tests.test_package import fake_run
from tests.test_serve import DSN, db  # noqa: F401  (fixture: a fresh migrated database)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def patched_policy(monkeypatch):
    """A deep copy of the policy the test may edit; every module sees it."""
    p = copy.deepcopy(policy())
    monkeypatch.setattr(privacy_mod, "policy", lambda: p)
    for mod in ("pii", "guard", "fairness", "retention", "ropa"):
        monkeypatch.setattr(f"intel.privacy.{mod}.policy", lambda: p, raising=False)
    monkeypatch.setattr("intel.privacy.tables", lambda section: p[section]["tables"])
    monkeypatch.setattr("intel.privacy.guard.tables", lambda section: p[section]["tables"])
    monkeypatch.setattr("intel.privacy.retention.tables", lambda section: p[section]["tables"])
    return p


# ---------------------------------------------------------------- the policy matches reality


def _migration_tables() -> dict[str, str]:
    ddl = "\n".join(f.read_text() for f in sorted(MIGRATIONS.glob("*.sql")))
    found = dict(re.findall(r"create table (\w+) \((.*?)\n\);", ddl, flags=re.S))
    found["schema_migrations"] = "version text, applied_at timestamptz"  # created by the migration runner
    for table, col in re.findall(r"alter table (\w+) add column (\w+)", ddl):
        found[table] += f"\n{col}"
    return found


def test_every_intel_db_table_is_declared_and_nothing_else():
    assert set(tables("intel_db")) == set(_migration_tables())


def test_retention_rules_name_real_columns():
    ddl = _migration_tables()
    assert {r.table for r in rules()} == {"prediction", "event_log", "training_run"}
    for r in rules():
        assert re.search(rf"\b{r.column}\b", ddl[r.table]), r


def test_every_snapshot_table_a_model_reads_is_declared():
    from intel.market import ITEMS

    for c in CONTRACTS:
        section = "warehouse_pull" if c.source == "warehouse" else "market_pull"
        assert c.table in tables(section), c.table
    assert {i[1] for i in ITEMS} == set(tables("market_pull"))
    assert pull_tables() == sorted(c.table for c in CONTRACTS if c.source == "warehouse")


def test_every_declared_table_has_a_purpose_and_a_level():
    for section in ("warehouse_pull", "market_pull", "intel_db"):
        for name, spec in tables(section).items():
            assert spec.get("purpose") and spec.get("personal") in {"none", "pseudonymous", "declared"}, name
            if spec["personal"] != "none":
                assert spec.get("categories"), f"{name}: personal data needs its categories listed"


def test_the_record_of_processing_is_rendered_from_the_policy():
    assert ROPA_FILE.read_text() == render(), "run `uv run intel privacy ropa --write`"
    assert "HASHING.md" in render() and (ROPA_FILE.parent / "HASHING.md").exists()


# ---------------------------------------------------------------- the PII detectors


@pytest.mark.parametrize(
    "text,kinds",
    [
        ("call +49 30 1234567", {"phone": 1}),
        ("ruf an: 0049 (0)30-12345678", {"phone": 1}),
        ("mail me at a.b-c@kitchen-x.de!", {"email": 1}),
        ("IBAN DE89 3704 0044 0532 0130 00", {"iban": 1}),
        ("DE89 3704 0044 0532 0130 01", {}),  # checksum wrong: not an IBAN
        ("0199a2b3-4c5d-7e8f-9a0b-1c2d3e4f5a6b", {}),  # UUID
        ("post 1234567890123_9876543210987", {}),  # Facebook ids
        ("version 1.2.3.4 and ratio 0.0012345678", {}),  # app version, decimal
        ("2026-10-08T07:20:13+0000", {}),  # timestamp
        ("dk_8f3a9c2b1d", {}),  # visitor id
    ],
)
def test_detectors_are_precise(text, kinds):
    assert dict(find_in_text(text)) == kinds


def test_the_business_number_is_allowed_by_hash(patched_policy):
    import hashlib

    patched_policy["pii"]["allow_phone_sha256"] = [hashlib.sha256(b"493099990000").hexdigest()]
    assert find_in_text("Order: +49 30 9999 0000") == {}
    assert find_in_text("Order: 0049 30 9999 0000") == {}  # same number, other prefix
    assert find_in_text("Order: +49 30 9999 0001") == {"phone": 1}


def test_identity_keys_in_json_and_redaction():
    payload = json.dumps({"comments": {"data": [{"from": {"id": "1", "name": "A B"}, "message": "nice"}]}})
    assert scan_value(payload) == {"identity:from": 1}
    assert scan_value(json.dumps({"from": "2026-10-01"})) == {}  # a date range, not a person
    text, n = redact("Bad food, call me +49 151 2345678 or x@y.de")
    assert n == 2 and "[phone]" in text and "[email]" in text and "+49" not in text


# ---------------------------------------------------------------- the pull guard and the minimiser


def _write(d: Path, name: str, rows: list[dict]) -> SimpleNamespace:
    path = d / f"{name}.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    return SimpleNamespace(table=name, rows=len(rows), file=path.name, sha256=sha256_file(path), bytes=1)


def test_guard_refuses_undeclared_tables_and_undeclared_personal_data(tmp_path):
    _write(tmp_path, "customer", [{"id": "1"}])
    with pytest.raises(PrivacyViolation, match="undeclared tables customer"):
        guard_check("warehouse_pull", tmp_path, ["customer"])
    _write(
        tmp_path, "raw_orders_direct", [{"payload": json.dumps({"email": "a@b.de", "address_postal_code": "13353"})}]
    )
    with pytest.raises(PrivacyViolation, match=r"raw_orders_direct.payload: 1 x email") as e:
        guard_check("warehouse_pull", tmp_path, ["raw_orders_direct"])
    assert "a@b.de" not in str(e.value)  # counts, never values


def test_guard_redacts_review_texts_and_records_declared_findings(tmp_path):
    reviews = _write(tmp_path, "biryani_reviews", [{"text": "cold rice, call +49 151 2345678"}, {"text": "great"}])
    creators = _write(tmp_path, "creators", [{"bio": "collabs: hi@creator.de"}])
    before = reviews.sha256
    part = pull_guard("market_pull")(tmp_path, [reviews, creators])
    assert part["redacted"] == {"biryani_reviews": {"text": 1}}
    assert reviews.sha256 != before == before  # the checksum follows the rewritten file
    assert pq.read_table(tmp_path / "biryani_reviews.parquet").column("text")[0].as_py() == "cold rice, call [phone]"
    assert part["declared_findings"] == [{"table": "creators", "column": "bio", "kind": "email", "count": 1}]


def _legacy_pull(root: Path) -> Path:
    d = root / "20261001T000000Z"
    d.mkdir(parents=True)
    results = [
        _write(d, "social_post", [{"id": "p1", "caption": "Kacchi today"}]),
        _write(
            d, "raw_social_posts_facebook", [{"payload": json.dumps({"comments": {"data": [{"from": {"id": 1}}]}})}]
        ),
    ]
    manifest = {
        "pull_id": d.name,
        "tables": {r.table: {"rows": r.rows, "file": r.file, "sha256": r.sha256, "bytes": 1} for r in results},
        "total_rows": 2,
        "files": [],
    }
    (d / "manifest.json").write_text(json.dumps(manifest))
    sums = [f"{r.sha256}  {r.file}" for r in results] + [f"{sha256_file(d / 'manifest.json')}  manifest.json"]
    (d / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return d


def test_minimiser_removes_undeclared_tables_and_keeps_the_pull_verifiable(tmp_path):
    d = _legacy_pull(tmp_path)
    keep_sha = sha256_file(d / "social_post.parquet")
    r = minimise_existing(d, "warehouse_pull", now=dt.datetime(2026, 10, 9, tzinfo=dt.UTC))
    assert r["removed_tables"] == ["raw_social_posts_facebook"]
    assert not (d / "raw_social_posts_facebook.parquet").exists()
    assert verify_pull(d) == []
    m = json.loads((d / "manifest.json").read_text())
    assert m["total_rows"] == 1 and m["privacy"]["removed_tables"] == ["raw_social_posts_facebook"]
    assert "raw_social_posts_facebook" in m["privacy"]["original_sha256"]
    assert sha256_file(d / "social_post.parquet") == keep_sha  # what models read is untouched
    assert minimise_existing(d, "warehouse_pull") == {"pull": d.name, "skipped": "already minimised"}


# ---------------------------------------------------------------- the responsible-AI gate


def test_every_model_reading_origin_data_has_a_reviewed_exception():
    from intel.plugins import model_sources

    for name in model_sources():
        sensitive = protected_inputs(name)
        assert check(name, {}, None) == [], name
        if sensitive:
            exc = next(e for e in policy()["fairness"]["exceptions"] if e["model"] == name)
            assert set(exc["reads"]) == sensitive and exc["reviewed_by"] == "owner"


def test_lineage_rule_needs_an_exception_and_exceptions_expire(patched_policy):
    assert check("community_map", {}, None) == []
    later = dt.date.fromisoformat(str(patched_policy["fairness"]["exceptions"][0]["review_again"])) + dt.timedelta(1)
    assert "expired" in check("community_map", {}, None, today=later)[0]
    patched_policy["fairness"]["exceptions"] = []
    (problem,) = check("community_map", {}, None)
    assert "reads ['lor_community']" in problem and "without a reviewed exception" in problem


def test_protected_names_blocked_words_and_pii_in_outputs():
    report = {"areas": [{"area_id": "1", "resident_religion": 3}], "features": ["hour", "ethnicity_share"]}
    (p,) = check("content_scorer", {"report.json": report}, None)
    assert "resident_religion (religion)" in p and "ethnicity_share (ethnic" in p
    community = {"by_group": {"turkish": 10}}
    assert check("funnel", {"x": community}, None, extra_terms=["turkish"])  # the data platform's community ids
    planner = {"items": [{"greeting": "Happy Diwali"}]}
    assert blocked_word_hits(planner, ["diwali"]) == {"diwali": 1}
    assert "blocked words" in check("occasion_planner", {"insights": planner}, ["diwali"])[0]
    assert check("weakness_miner", {"insights": {"quote": "Diwali menu was cold"}}, ["diwali"]) == []  # a quote
    assert "personal data {'email': 1}" in check("funnel", {"insights": {"note": "x@y.de"}}, None)[0]


def test_build_package_refuses_a_run_the_gate_rejects(tmp_path):
    from intel.package import build_package

    run = fake_run(tmp_path, "pass")
    report = json.loads((run / "report.json").read_text())
    report["challenger"]["features"] = ["hour", "citizenship_share"]
    (run / "report.json").write_text(json.dumps(report))
    with pytest.raises(PrivacyViolation, match="citizenship_share"):
        build_package(run, tmp_path / "out", ROOT)


def test_a_clean_package_records_the_gate(tmp_path):
    import tarfile

    from intel.package import build_package

    tgz = build_package(fake_run(tmp_path, "pass"), tmp_path / "out", ROOT)
    with tarfile.open(tgz) as t:
        meta = json.load(t.extractfile(next(m for m in t.getmembers() if m.name.endswith("metadata.json"))))  # type: ignore[arg-type]
    assert meta["responsible_ai"]["policy_version"] == 1 and meta["responsible_ai"]["exception"] is None


def test_bundle_documents_cover_what_the_owner_sees(tmp_path):
    joblib.dump({"kind": "insights", "insights": {"areas": [1]}, "check": {"n": 1}}, tmp_path / "m.joblib")
    assert bundle_documents(joblib.load(tmp_path / "m.joblib")) == {
        "model.joblib": {"insights": {"areas": [1]}, "check": {"n": 1}}
    }
    assert bundle_documents({"columns": ["hour"], "inputs": ["caption"], "pipeline": object()}) == {
        "model.joblib": {"columns": ["hour"], "inputs": ["caption"]}
    }


# ---------------------------------------------------------------- retention against a real database


@pytest.mark.skipif(not DSN, reason="INTEL_TEST_PG_DSN not set")
def test_retention_deletes_exactly_what_the_policy_says_and_audits_it(db):  # noqa: F811
    from intel.serve.db import connect
    from intel.serve.lifecycle import audit_verify

    now = dt.datetime(2026, 10, 9, 12, tzinfo=dt.UTC)
    old, recent = now - dt.timedelta(days=120), now - dt.timedelta(days=10)
    with connect(db) as c:
        c.execute(
            "insert into ledger_post (post_id, platform, posted_at, caption_sha256, caption_length) "
            "values ('00000000-0000-0000-0000-000000000001', 'instagram', %s, 'h', 1)",
            (old,),
        )
        for source, at in (("api", old), ("api", recent), ("shadow", old)):
            c.execute(
                "insert into prediction (source, model_name, model_version, post_id, platform, planned_at,"
                " input_sha256, probability, threshold, predicted_label, created_at)"
                " values (%s,'content_scorer','v',%s,'instagram',%s,'x',0.5,0.5,1,%s)",
                (source, "00000000-0000-0000-0000-000000000001" if source == "shadow" else None, at, at),
            )
        c.execute(
            "insert into event_log (at, kind) values (%s, 'old'), (%s, 'new')", (now - dt.timedelta(days=200), recent)
        )

    dry = {p.table: p.deleted for p in run_retention(db, now=now, dry_run=True) if p.deleted}
    assert dry == {"prediction": 1, "event_log": 1}
    with connect(db) as c:
        assert c.execute("select count(*) n from prediction").fetchone()["n"] == 3  # a dry run deletes nothing

    done = run_retention(db, now=now)
    assert sum(p.deleted for p in done) == 2
    with connect(db) as c:
        left = c.execute("select source, created_at from prediction order by source").fetchall()
        assert [(r["source"], r["created_at"] == old) for r in left] == [("api", False), ("shadow", True)]
        assert [r["kind"] for r in c.execute("select kind from event_log where kind in ('old','new')")] == ["new"]
        entry = c.execute("select * from audit_log where action = 'retention_purge'").fetchone()
        assert entry["actor"] == "system" and sum(entry["detail"]["deleted"].values()) == 2
        assert audit_verify(c)["ok"]
    assert sum(p.deleted for p in run_retention(db, now=now)) == 0  # idempotent
