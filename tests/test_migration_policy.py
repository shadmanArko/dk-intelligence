"""ADR 0015: intel-db migrations are additive (expand / contract), so rolling the APP back is always safe.

The previous image must keep working on a database the new image has already migrated. A destructive statement is
allowed only in a migration marked `-- contract: <reason>`, shipped in a later release once no deployed version uses
the old shape.
"""

import re
from pathlib import Path

import pytest

from intel.serve.db import MIGRATIONS

DESTRUCTIVE = re.compile(
    # TRUNCATE the statement (`truncate [table] name`), not the trigger event (`before truncate on name`)
    r"\b(drop\s+(table|column|index|view|type|schema)|rename\s|alter\s+column\s+\w+\s+(type|set\s+not\s+null)"
    r"|truncate\s+(?:table\s+)?(?!on\b)\w+)\b",
    re.IGNORECASE,
)


def problems(sql: str) -> list[str]:
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())  # ignore comments
    if re.search(r"^--\s*contract:\s*\S", sql, re.MULTILINE):
        return []
    return [m.group(0) for m in DESTRUCTIVE.finditer(code)]


@pytest.mark.parametrize("path", sorted(MIGRATIONS.glob("*.sql")), ids=lambda p: Path(p).name)
def test_migrations_are_additive(path):
    assert not problems(path.read_text()), f"{path.name}: destructive statement without a `-- contract:` marker"


def test_the_policy_catches_destructive_sql():
    assert problems("alter table t drop column x;") == ["drop column"]
    assert problems("ALTER TABLE t ALTER COLUMN a TYPE bigint;") == ["ALTER COLUMN a TYPE"]
    assert problems("alter table t rename to u;") == ["rename "]
    assert problems("-- contract: nothing reads t.x since v42\nalter table t drop column x;") == []
    assert problems("alter table t add column y int; -- never drop column y") == []
    assert problems("truncate table t;") == ["truncate table t"] and problems("truncate t;") == ["truncate t"]
    assert problems("create trigger x before truncate on t for each statement execute function f();") == []
