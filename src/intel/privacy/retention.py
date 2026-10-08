"""Retention job for intel-db (ADR 0024): deletes what the policy says may no longer be kept.

Rules come from `policy.yaml` (`intel_db.tables`): `retention: <days>` deletes whole rows older than that by `column`;
`delete_after_days: {where, days, column}` deletes only the matching rows (API draft predictions after 90 days,
while the shadow ledger is kept). Runs daily inside intel-api; all deletions and their audit entry commit in ONE
transaction, so the audit trail can never claim a purge that did not happen (or miss one that did).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from psycopg import sql

from intel.privacy import policy, tables


@dataclass(frozen=True)
class Rule:
    table: str
    column: str
    days: int
    where: str | None = None  # an extra SQL condition from the policy file (repository-controlled, never user input)

    def describe(self) -> str:
        return f"{self.table}{f' ({self.where})' if self.where else ''}: older than {self.days} days by {self.column}"


def rules() -> list[Rule]:
    out: list[Rule] = []
    for name, spec in tables("intel_db").items():
        if isinstance(spec.get("retention"), int):
            out.append(Rule(name, str(spec["column"]), int(spec["retention"])))
        if part := spec.get("delete_after_days"):
            out.append(Rule(name, str(part["column"]), int(part["days"]), str(part["where"])))
    return out


@dataclass(frozen=True)
class Purge:
    table: str
    rule: str
    cutoff: str
    deleted: int


def run_retention(dsn: str, now: dt.datetime | None = None, dry_run: bool = False) -> list[Purge]:
    """Apply every rule; returns what was (or, with `dry_run`, would be) deleted. Audited when anything is deleted."""
    from intel.serve.db import connect
    from intel.serve.lifecycle import audit

    now = now or dt.datetime.now(dt.UTC)
    done: list[Purge] = []
    with connect(dsn) as c:
        for r in rules():
            cutoff = now - dt.timedelta(days=r.days)
            cond = sql.SQL("{} < %s").format(sql.Identifier(r.column))
            if r.where:
                cond = sql.SQL("{} and ({})").format(cond, sql.SQL(r.where))
            if dry_run:
                q = sql.SQL("select count(*) as n from {} where {}").format(sql.Identifier(r.table), cond)
                row = c.execute(q, (cutoff,)).fetchone()
                n = int(row["n"]) if row else 0
            else:
                q = sql.SQL("delete from {} where {}").format(sql.Identifier(r.table), cond)
                n = c.execute(q, (cutoff,)).rowcount
            done.append(Purge(r.table, r.describe(), cutoff.isoformat(timespec="seconds"), n))
        deleted = {p.rule: p.deleted for p in done if p.deleted}
        if deleted and not dry_run:
            audit(c, "system", "system", "retention_purge", "intel-db", deleted=deleted, policy=policy()["version"])
    return done


def summary(purges: list[Purge]) -> dict[str, Any]:
    return {"deleted": sum(p.deleted for p in purges), "by_rule": {p.rule: p.deleted for p in purges}}
