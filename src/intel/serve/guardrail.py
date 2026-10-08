"""Automatic rollback of a degraded production scorer (ADR 0020).

Acting on production needs stronger evidence than asking for retraining (ADR 0018), and must never flap. Hourly,
after the monitor, for every active scorer:

Evidence (any one is enough, each needs >= MIN_OUTCOMES matured 7-day outcomes of THIS version):
- **worse than the previous system on the same posts**: every post is also scored by the harness predictor (the
  shadow baseline). On posts where exactly one of the two was right, McNemar's exact test asks whether the model is
  wrong more often than chance would explain (p < P_WORSE, and more often, not less);
- **collapsed**: live balanced accuracy at or below chance (0.5) although the test result was >= 0.6;
- **miscalibrated**: mean predicted probability off the observed share by more than CALIBRATION_GAP.

Safety rails, checked in this order:
- mode `off` does nothing; `dry_run` reports what it would do (once per version) and never acts;
- a model the owner pinned (Telegram /keep, PIN_DAYS) is held;
- at most one automatic rollback per model per COOLDOWN (no ping-pong between two bad versions);
- there must be an earlier approved version to return to; otherwise the owner is told, and nothing changes.

A rollback goes through the registry (audit trail, actor `guardrail`, reason recorded) and Telegram says why and how
to undo it. Promotion is never automatic: the guardrail can only return to a version a human approved before.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from scipy.stats import binomtest

from intel.serve.db import connect, kv_get, kv_set
from intel.serve.measure import MODEL_LABEL

MIN_OUTCOMES = 40
P_WORSE = 0.05
CHANCE = 0.5
GOOD_TEST = 0.6
CALIBRATION_GAP = 0.25
COOLDOWN = dt.timedelta(days=7)
PIN_DAYS = 14
BASELINE = "harness_predictor"
Mode = Literal["on", "dry_run", "off"]


@dataclass
class Evidence:
    model: str
    version: str
    paired: int = 0  # posts with a 7-day outcome scored by both this version and the baseline
    model_only_wrong: int = 0  # discordant pairs: the model wrong, the baseline right
    baseline_only_wrong: int = 0  # discordant pairs: the baseline wrong, the model right
    p_value: float | None = None
    outcomes: int = 0
    live_balanced_accuracy: float | None = None
    test_balanced_accuracy: float | None = None
    calibration_gap: float | None = None


@dataclass
class Decision:
    model: str
    version: str
    action: Literal["rollback", "would_rollback", "hold", "none"]
    reasons: list[str] = field(default_factory=list)
    held_by: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def mcnemar_p(model_only_wrong: int, baseline_only_wrong: int) -> float:
    """Exact McNemar test on the discordant pairs (two-sided)."""
    n = model_only_wrong + baseline_only_wrong
    return 1.0 if n == 0 else float(binomtest(model_only_wrong, n, 0.5).pvalue)


def paired_evidence(dsn: str, name: str, version: str, health: dict[str, Any] | None) -> Evidence:
    label = MODEL_LABEL.get(name, "label")
    with connect(dsn) as c:
        rows = c.execute(
            f"""select p.predicted_label as m, h.predicted_label as b, o.{label} as y
                from prediction p
                join prediction h on h.post_id = p.post_id and h.model_name = %s and h.source = 'shadow'
                join post_outcome o on o.post_id = p.post_id and o.horizon = '7d'
                where p.source = 'shadow' and p.model_name = %s and p.model_version = %s and o.{label} is not null""",  # noqa: S608
            (BASELINE, name, version),
        ).fetchall()
    ev = Evidence(name, version, paired=len(rows))
    ev.model_only_wrong = sum(1 for r in rows if r["m"] != r["y"] and r["b"] == r["y"])
    ev.baseline_only_wrong = sum(1 for r in rows if r["b"] != r["y"] and r["m"] == r["y"])
    ev.p_value = round(mcnemar_p(ev.model_only_wrong, ev.baseline_only_wrong), 6)
    live = (health or {}).get("live") or {}
    ev.outcomes = int(live.get("n") or 0)
    ev.live_balanced_accuracy = live.get("balanced_accuracy")
    ev.calibration_gap = live.get("calibration_gap")
    ev.test_balanced_accuracy = ((health or {}).get("reference") or {}).get("test_balanced_accuracy")
    return ev


def reasons_for(ev: Evidence) -> list[str]:
    out = []
    if ev.paired >= MIN_OUTCOMES and ev.model_only_wrong > ev.baseline_only_wrong and (ev.p_value or 1) < P_WORSE:
        out.append(
            f"worse than the previous predictor on the same {ev.paired} posts: wrong alone on "
            f"{ev.model_only_wrong}, right alone on {ev.baseline_only_wrong} (McNemar p={ev.p_value})"
        )
    if ev.outcomes >= MIN_OUTCOMES:
        ba, test = ev.live_balanced_accuracy, ev.test_balanced_accuracy
        if ba is not None and test is not None and ba <= CHANCE and test >= GOOD_TEST:
            out.append(f"collapsed to chance: live balanced accuracy {ba} (test {test})")
        if ev.calibration_gap is not None and abs(ev.calibration_gap) > CALIBRATION_GAP:
            out.append(f"miscalibrated: predictions off the observed share by {ev.calibration_gap:+.0%}")
    return out


def _previous_version(dsn: str, name: str) -> str | None:
    """The approved version a rollback would return to (the registry's rule: the latest different one)."""
    with connect(dsn) as c:
        hist = c.execute(
            """select a.package_id, p.version from model_activation a join model_package p on p.id = a.package_id
               where a.name = %s order by a.id desc""",
            (name,),
        ).fetchall()
    if not hist:
        return None
    current = hist[0]["package_id"]
    return next((str(h["version"]) for h in hist[1:] if h["package_id"] != current), None)


def decide(ev: Evidence, mode: Mode, memory: dict[str, Any], previous: str | None, now: dt.datetime) -> Decision:
    reasons = reasons_for(ev)
    d = Decision(ev.model, ev.version, "none", reasons, evidence=asdict(ev))
    if not reasons or mode == "off":
        return d
    pin = memory.get("pins", {}).get(ev.model)
    last = memory.get("last_rollback", {}).get(ev.model)
    if pin and dt.datetime.fromisoformat(pin) > now:
        d.action, d.held_by = "hold", f"pinned by the owner until {pin[:10]}"
    elif last and now - dt.datetime.fromisoformat(last) < COOLDOWN:
        d.action, d.held_by = "hold", f"already rolled back automatically on {last[:10]} (one per {COOLDOWN.days} days)"
    elif previous is None:
        d.action, d.held_by = "hold", "no earlier approved version to return to"
    else:
        d.action = "would_rollback" if mode == "dry_run" else "rollback"
    return d


def run_guardrail(
    dsn: str,
    registry: Any,
    monitoring: dict[str, Any] | None,
    mode: Mode,
    notify: Any = None,
    now: dt.datetime | None = None,
) -> list[Decision]:
    """Evaluate every monitored scorer; act (or report) according to `mode`. Returns the decisions."""
    now = now or dt.datetime.now(dt.UTC)
    memory: dict[str, Any] = kv_get(dsn, "guardrail", {}) or {}
    memory.setdefault("pins", {})
    memory.setdefault("last_rollback", {})
    memory.setdefault("notified", [])
    decisions = []
    for name, health in sorted(((monitoring or {}).get("models") or {}).items()):
        ev = paired_evidence(dsn, name, str(health["version"]), health)
        previous = _previous_version(dsn, name)
        d = decide(ev, mode, memory, previous, now)
        decisions.append(d)
        key = f"{d.action}:{name}:{d.version}:{d.held_by or ''}"
        if d.action == "rollback":
            why = "; ".join(d.reasons)
            res = registry.rollback(name, "guardrail", "system", reason=why)
            memory["last_rollback"][name] = now.isoformat(timespec="seconds")
            _say(
                notify,
                f"↩️ <b>Rolled back {name}</b> from <code>{d.version}</code> to <code>{res['version']}</code> "
                f"automatically.\nWhy: {why}\nUndo with /rollback, or /keep to stop automatic rollbacks.",
            )
        elif d.action in ("would_rollback", "hold") and key not in memory["notified"]:
            memory["notified"].append(key)
            verb = "would roll back" if d.action == "would_rollback" else f"wants to roll back, but holds ({d.held_by})"
            _say(notify, f"⚠️ Guardrail {verb}: <b>{name}</b> <code>{d.version}</code>.\nWhy: {'; '.join(d.reasons)}")
    memory["notified"] = memory["notified"][-200:]
    kv_set(dsn, "guardrail", memory)
    return decisions


def pin(dsn: str, name: str, days: int = PIN_DAYS, now: dt.datetime | None = None) -> str:
    """The owner keeps the current version: no automatic rollback of `name` for `days`."""
    now = now or dt.datetime.now(dt.UTC)
    memory: dict[str, Any] = kv_get(dsn, "guardrail", {}) or {}
    until = (now + dt.timedelta(days=days)).isoformat(timespec="seconds")
    memory.setdefault("pins", {})[name] = until
    kv_set(dsn, "guardrail", memory)
    return until


def _say(notify: Any, text: str) -> None:
    if notify is not None:
        try:
            notify(text)
        except Exception:  # a failed message must never undo or block the decision
            import logging

            logging.getLogger("intel.guardrail").exception("guardrail notification failed")
