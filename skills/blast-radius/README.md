# Blast radius

Find the downstream caller that can turn a one-line change into a broken release.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're renaming a symbol, changing an interface, or removing a module.

## Try it

```text
Use blast-radius before I rename this exported function. Find aliased callers, dependent modules, tests, and documentation that need the new name.
```

## What you get

The [impact mapper](scripts/impact.py) starts from a file, symbol, or Git diff and follows dependencies in the repository.

## Pair it with

[Repo map](../../skills/repo-map/README.md) · [Ship check](../../skills/ship-check/README.md)
