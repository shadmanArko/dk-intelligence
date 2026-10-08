"""ADR 0020: automatic rollback on paired evidence, with dry run, pins, cool-down and an audited trail."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from intel.serve.guardrail import Evidence, decide, mcnemar_p, reasons_for, run_guardrail
from tests.test_serve import DSN, db, drop, reg  # noqa: F401  (fixtures)

NOW = dt.datetime(2026, 11, 20, 12, tzinfo=dt.UTC)


def test_mcnemar_counts_only_the_posts_where_the_two_disagree():
    assert mcnemar_p(0, 0) == 1.0
    assert mcnemar_p(15, 2) < 0.01 and mcnemar_p(2, 15) < 0.01  # symmetric: direction is checked separately
    assert mcnemar_p(6, 4) > 0.5


def test_evidence_rules():
    worse = Evidence(
        "content_scorer", "v", paired=45, model_only_wrong=15, baseline_only_wrong=2, p_value=mcnemar_p(15, 2)
    )
    assert reasons_for(worse)[0].startswith("worse than the previous predictor on the same 45 posts")
    better = Evidence(
        "content_scorer", "v", paired=45, model_only_wrong=2, baseline_only_wrong=15, p_value=mcnemar_p(2, 15)
    )
    assert reasons_for(better) == []  # significantly BETTER is not a reason to roll back
    few = Evidence("content_scorer", "v", paired=20, model_only_wrong=12, baseline_only_wrong=0, p_value=0.0005)
    assert reasons_for(few) == []  # under 40 paired outcomes: no judgement
    collapsed = Evidence("content_scorer", "v", outcomes=50, live_balanced_accuracy=0.48, test_balanced_accuracy=0.69)
    miscal = Evidence("content_scorer", "v", outcomes=50, calibration_gap=-0.31)
    assert reasons_for(collapsed)[0].startswith("collapsed to chance") and "-31%" in reasons_for(miscal)[0]


@pytest.mark.parametrize(
    ("mode", "memory", "previous", "action", "held"),
    [
        ("on", {}, "v0", "rollback", None),
        ("dry_run", {}, "v0", "would_rollback", None),
        ("off", {}, "v0", "none", None),
        ("on", {"pins": {"content_scorer": "2026-11-30T00:00:00+00:00"}}, "v0", "hold", "pinned by the owner"),
        ("on", {"last_rollback": {"content_scorer": "2026-11-18T00:00:00+00:00"}}, "v0", "hold", "already rolled back"),
        ("on", {"last_rollback": {"content_scorer": "2026-11-01T00:00:00+00:00"}}, "v0", "rollback", None),
        ("on", {}, None, "hold", "no earlier approved version"),
    ],
)
def test_safety_rails_in_order(mode, memory, previous, action, held):
    ev = Evidence("content_scorer", "v1", outcomes=50, live_balanced_accuracy=0.45, test_balanced_accuracy=0.7)
    d = decide(ev, mode, memory, previous, NOW)
    assert d.action == action and (held is None or (d.held_by or "").startswith(held))


# ---------- end to end on a real database ----------

pytestmark_db = pytest.mark.skipif(not DSN, reason="INTEL_TEST_PG_DSN not set")


def _two_versions(reg, tmp_path):  # noqa: F811
    for v in ("cs-a", "cs-b"):
        drop(tmp_path, reg.inbox, v)
        (it,) = reg.scan_inbox()
        reg.approve(it.approval_id, "owner", "telegram")
    assert reg.active()["content_scorer"]["version"] == "cs-b"


def _live(dsn, version, *, model_wrong=15, baseline_wrong=2, agree=28):
    """Paired shadow predictions of `version` and the baseline with 7-day outcomes (label always 1)."""
    from intel.serve.db import connect

    cases = [(0, 1)] * model_wrong + [(1, 0)] * baseline_wrong + [(1, 1)] * agree  # (model, baseline) predictions
    with connect(dsn) as c:
        for m, b in cases:
            pid, at = uuid.uuid4(), NOW - dt.timedelta(days=20)
            c.execute(
                "insert into ledger_post (post_id, platform, posted_at, content_type, caption_sha256, "
                "caption_length) values (%s,'facebook',%s,'reel','x',5)",
                (pid, at),
            )
            for name, ver, lab in (("content_scorer", version, m), ("harness_predictor", "h1", b)):
                c.execute(
                    """insert into prediction (source, model_name, model_version, post_id, platform, planned_at,
                             input_sha256, probability, threshold, predicted_label) values
                             ('shadow',%s,%s,%s,'facebook',%s,'h',0.5,0.5,%s)""",
                    (name, ver, pid, at, lab),
                )
            c.execute(
                "insert into post_outcome (post_id, horizon, captured_at, age_hours, engagement, label) "
                "values (%s,'7d',%s,170,10,1)",
                (pid, at + dt.timedelta(days=7)),
            )


def _monitoring(version, n=45):
    return {
        "models": {
            "content_scorer": {
                "version": version,
                "live": {"n": n, "balanced_accuracy": None},
                "reference": {"test_balanced_accuracy": 0.69},
            }
        }
    }


@pytestmark_db
def test_a_significantly_worse_model_is_rolled_back_once_with_an_audited_reason(reg, tmp_path):  # noqa: F811
    from intel.serve.db import connect

    _two_versions(reg, tmp_path)
    _live(reg.dsn, "cs-b")
    said: list[str] = []
    (d,) = run_guardrail(reg.dsn, reg, _monitoring("cs-b"), "on", said.append, now=NOW)
    assert d.action == "rollback" and reg.active()["content_scorer"]["version"] == "cs-a"
    assert len(said) == 1 and "Rolled back content_scorer" in said[0] and "McNemar p=" in said[0]
    with connect(reg.dsn) as c:
        a = c.execute("select actor, channel, detail from audit_log where action='rolled_back'").fetchone()
    assert a["actor"] == "guardrail" and a["channel"] == "system" and "worse than the previous" in a["detail"]["reason"]
    # an hour later: cs-a is active again; even if cs-a looked bad, the cool-down prevents a ping-pong
    _live(reg.dsn, "cs-a")
    (d2,) = run_guardrail(reg.dsn, reg, _monitoring("cs-a"), "on", said.append, now=NOW + dt.timedelta(hours=1))
    assert d2.action == "hold" and d2.held_by.startswith("already rolled back automatically")
    assert reg.active()["content_scorer"]["version"] == "cs-a"


@pytestmark_db
def test_dry_run_reports_once_and_changes_nothing(reg, tmp_path):  # noqa: F811
    _two_versions(reg, tmp_path)
    _live(reg.dsn, "cs-b")
    said: list[str] = []
    for hour in range(3):
        run_guardrail(reg.dsn, reg, _monitoring("cs-b"), "dry_run", said.append, now=NOW + dt.timedelta(hours=hour))
    assert reg.active()["content_scorer"]["version"] == "cs-b"
    assert len(said) == 1 and said[0].startswith("⚠️ Guardrail would roll back")


@pytestmark_db
def test_pinned_first_and_better_models_stay(reg, tmp_path):  # noqa: F811
    from intel.serve.guardrail import pin

    drop(tmp_path, reg.inbox, "cs-only")
    (it,) = reg.scan_inbox()
    reg.approve(it.approval_id, "owner", "telegram")
    _live(reg.dsn, "cs-only")
    said: list[str] = []
    (d,) = run_guardrail(reg.dsn, reg, _monitoring("cs-only"), "on", said.append, now=NOW)
    assert d.action == "hold" and d.held_by == "no earlier approved version to return to"
    assert "holds (no earlier approved version" in said[0]

    _two_versions(reg, tmp_path)  # cs-a, cs-b: the latest (cs-b) is now active
    _live(reg.dsn, "cs-b", model_wrong=2, baseline_wrong=15)  # better than the baseline
    (d,) = run_guardrail(reg.dsn, reg, _monitoring("cs-b"), "on", said.append, now=NOW)
    assert d.action == "none"

    _live(reg.dsn, "cs-b", model_wrong=40, baseline_wrong=0)  # now clearly worse, but the owner pinned it
    pin(reg.dsn, "content_scorer", now=NOW)
    (d,) = run_guardrail(reg.dsn, reg, _monitoring("cs-b"), "on", said.append, now=NOW + dt.timedelta(hours=1))
    assert d.action == "hold" and d.held_by.startswith("pinned by the owner")
    assert reg.active()["content_scorer"]["version"] == "cs-b"


@pytestmark_db
def test_keep_command_pins_and_is_audited(reg, tmp_path):  # noqa: F811
    from intel.serve.db import connect, kv_get
    from intel.serve.telegram import Bot
    from tests.test_serve import _cb, _msg

    _two_versions(reg, tmp_path)

    class TG:
        chat_id = 8530058914
        sent: list = []

        def send(self, text, buttons=None):
            self.sent.append((text, buttons))
            return 1

        def answer(self, *a, **k):
            pass

        def edit(self, msg_id, text, buttons=None):
            self.sent.append((text, buttons))

    tg = TG()
    bot = Bot(tg, reg, None)
    bot.handle(_msg(tg.chat_id, "/keep"))
    assert tg.sent[-1][1][0] == [("📌 content_scorer", "kp:content_scorer")]
    bot.handle(_cb(tg.chat_id, "kp:content_scorer"))
    assert "kept: no automatic rollback until" in tg.sent[-1][0]
    assert "content_scorer" in kv_get(reg.dsn, "guardrail")["pins"]
    with connect(reg.dsn) as c:
        assert c.execute("select count(*) n from audit_log where action='guardrail_pinned'").fetchone()["n"] == 1
