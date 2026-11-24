---
name: migrate-safe
description: Reviews and tests database schema and data migrations for destructive hazards, write-blocking locks, idempotency and clean rollbacks before they touch production. Ships a stdlib SQL linter and live SQLite round-trip harness (`migration_check.py`). Use when writing, reviewing or running SQL migrations, altering tables, backfilling rows, adding indexes on live databases, or planning a schema rollout and rollback.
metadata:
  version: "1.0.0"
  hermes:
    tags: [database, sqlite, migration, schema]
    related_skills: [ship-check, spec-check]
---

# Migrate safe

A buggy code deploy rolls back in thirty seconds. A destructive schema migration or an unguarded backfill can lock production traffic or erase customer rows permanently. Verify hazards statically and exercise rollback on a seeded sandbox before applying anything live.

## Two checks in one script

`scripts/migration_check.py` needs only Python 3.9+ (including built-in `sqlite3`).

### 1. Static hazard lint

```bash
python3 scripts/migration_check.py lint migrations/0042_up.sql \
  --dialect postgres --down migrations/0042_down.sql --require-down
```

Flags:

- `DROP TABLE`, `DROP DATABASE`, `DROP SCHEMA`, `TRUNCATE` (blocker unless explicitly acknowledged with `-- migrate-safe: allow-drop`)
- `ALTER TABLE ... DROP COLUMN` (high risk; follow expand-and-contract so running code stops reading the column in an earlier deploy)
- `ALTER TABLE ... ADD COLUMN ... NOT NULL` without a `DEFAULT` (blocker on non-empty tables)
- `CREATE INDEX` without `CONCURRENTLY` when `--dialect postgres` (blocks writes)
- `UPDATE` or `DELETE` without a `WHERE` on that write (blocker). Each write in a common table expression is checked separately. A predicate in a nested lookup filters only the lookup.
- missing or empty companion `--down` rollback file when `--require-down` is set

Exit codes: `0` on `MIGRATION LINT: CLEAN`, `1` on findings, `2` on usage error.

### 2. Live SQLite round-trip sandbox

```bash
python3 scripts/migration_check.py sqlite-test \
  --seed fixtures/seed.sql --up migrations/0042_up.sql \
  --down migrations/0042_down.sql --assert-sql fixtures/assert.sql
```

The sandbox enables foreign keys and checks for violations after each script, including scripts that temporarily disable enforcement. It denies named `ATTACH` targets, file exports through `VACUUM INTO`, and changes to SQLite storage directories. Plain `VACUUM` still runs on the temporary database. It records the seeded database and the result of the first forward migration. A repeat must preserve the first result, rollback must restore the seed, and reapplying must reproduce the first result. Each comparison checks the schema and each table's rows, including duplicate counts and SQLite value types, independent of scan order. Pass `--allow-non-idempotent` for DDL such as `ALTER TABLE ADD COLUMN` that raises on repeat. The repeat can raise an error only if it leaves the first result intact. Exits `0` on `MIGRATION: SAFE` and `1` on `MIGRATION: UNSAFE`.

## Safe rollout pattern (expand and contract)

1. **Expand:** add nullable columns or new tables, backfill in bounded batches, and verify row counts.
2. **Switch:** deploy code that reads and writes the new shape while tolerating the old shape.
3. **Contract:** in a later release, enforce `NOT NULL` / constraints and drop the obsolete column after confirming zero live readers.

## Rules

- Never run a migration against production or shared staging without explicit operator confirmation.
- Test on a seeded copy that contains realistic edge rows (nulls, duplicates, boundary dates), never only an empty schema.
- Note engine differences plainly: a passing SQLite sandbox proves SQL structure and rollback logic, not Postgres/MySQL lock timing.
