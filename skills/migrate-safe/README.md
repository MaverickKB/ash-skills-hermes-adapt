# Migrate safe

Run the migration, repeat it, and check exactly what rollback restores.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're adding a column, backfilling data, or preparing a schema change you may need to reverse.

## Try it

```text
Use migrate-safe. Seed a SQLite database with representative rows, run the migration twice, then check that rollback restores the schema and every row.
```

## What you get

The [migration checker](scripts/migration_check.py) lints SQL for hazards and tests a SQLite round trip against seeded data.

## Pair it with

[Spec check](../../skills/spec-check/README.md) · [Ship check](../../skills/ship-check/README.md)
