# Blast radius

Traces callers, aliased imports, dependent modules, tests, and documentation for a proposed change.

## When to use it

You're renaming a symbol, changing a signature, or removing a module.

Before renaming an exported function, list its direct callers, re-exports, and dependent tests.

## What's included

`scripts/impact.py` accepts a symbol, a file, or a Git diff as its starting point.

Open [SKILL.md](SKILL.md) for the full instructions.
