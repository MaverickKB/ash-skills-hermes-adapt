# Ship check

Review the staged changes and get a `SHIP` or `HOLD` verdict with the findings behind it.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're about to commit or merge and want to catch leftover debug code, credential patterns, or broken callers.

## Try it

```text
Use ship-check on my staged changes. Run the test command against that snapshot and show me what would block the commit.
```

## What you get

Run the [diff audit](scripts/diff_audit.py) on the staged snapshot or working tree. Add your project's test command when you want the verdict to include its result.

## Pair it with

[Ash blast radius](../ash-blast-radius/README.md) · [Prove it](../prove-it/README.md)
