"""Model lifecycle and audit trail (ADR 0019).

Stages of a package (what people see), derived from its stored status and the activation history:

    received ─┬─ invalid | incompatible | test_only            (never offered for approval)
              └─ candidate ─┬─ rejected | expired
                            └─ approved ─ production ⇄ retired  (activations: approval / rollback)

`TRANSITIONS` lists the only status changes allowed; the database enforces the same table with a trigger
(migration 0006), so neither a buggy code path nor a manual SQL session can, say, approve a rejected model.
`audit()` writes a governance event into the hash-chained, append-only `audit_log` using the caller's connection,
so the record commits or rolls back together with the change it describes.
"""

from __future__ import annotations

import json
from typing import Any, Literal, cast

import psycopg
from psycopg.rows import DictRow

Status = Literal["invalid", "incompatible", "test_only", "pending_approval", "approved", "rejected", "expired"]
Stage = Literal["invalid", "incompatible", "test_only", "candidate", "rejected", "expired", "production", "retired"]

TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {("pending_approval", "approved"), ("pending_approval", "rejected"), ("pending_approval", "expired")}
)
STATUSES: tuple[str, ...] = (
    "invalid",
    "incompatible",
    "test_only",
    "pending_approval",
    "approved",
    "rejected",
    "expired",
)


def allowed(old: str, new: str) -> bool:
    return old == new or (old, new) in TRANSITIONS


def stage(status: str, is_active: bool) -> Stage:
    if status == "approved":
        return "production" if is_active else "retired"
    if status == "pending_approval":
        return "candidate"
    return cast(Stage, status)  # the remaining statuses are stages of the same name


def audit(c: psycopg.Connection[DictRow], actor: str, channel: str, action: str, subject: str, **detail: Any) -> None:
    c.execute(
        "insert into audit_log (actor, channel, action, subject, detail) values (%s,%s,%s,%s,%s)",
        (actor, channel, action, subject, json.dumps(detail, default=str)),
    )


def audit_verify(c: psycopg.Connection[DictRow]) -> dict[str, Any]:
    """{'ok': bool, 'entries': n, 'first_bad': id | None}: recomputes every hash and link of the chain."""
    row = c.execute("select audit_verify() as bad, (select count(*) from audit_log) as n").fetchone()
    assert row is not None
    return {"ok": row["bad"] is None, "entries": int(row["n"]), "first_bad": row["bad"]}
