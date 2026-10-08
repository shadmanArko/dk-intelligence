# 0015. intel-db migrations are additive (expand / contract)

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
Automatic rollback (ADR 0014) starts the previous image against a database the new image may already have migrated
(migrations run at start-up). If a migration dropped, renamed or retyped something, the previous version would break
exactly when it is needed.

## Decision
A release may only **expand** the schema (new tables, new nullable or defaulted columns, new indexes). Removing or
changing something (**contract**) happens in a later release, once no version that could be rolled back to still uses
it, in a migration marked `-- contract: <reason>`. `tests/test_migration_policy.py` fails on `DROP`, `RENAME`,
`ALTER COLUMN … TYPE / SET NOT NULL` or `TRUNCATE` in any unmarked migration.

## Consequences
- Good: app rollback is always schema-safe; the rule is enforced, not remembered.
- Cost: a cleanup takes two releases; old columns live a little longer.
