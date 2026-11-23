# Migrate safe

Lints SQL migrations and checks schema and row data across apply, repeat, and rollback in SQLite.

## When to use it

You're changing a schema, backfilling rows, or testing whether a migration can be reversed.

Run the migration against representative seeded data and check that a rollback restores the original state.

## What's included

`scripts/migration_check.py` includes a SQL lint command and a SQLite round-trip check.

Open [SKILL.md](SKILL.md) for the full instructions.
