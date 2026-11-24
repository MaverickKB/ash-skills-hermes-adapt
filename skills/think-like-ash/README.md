# Think like Ash

Diagnose a fault the way a fast human does: jump across the stack, converge in the middle, and find the tiny detail.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

A fault has no obvious cause, a linear checklist keeps missing it, or the symptom is misleading.

## Try it

```text
Use think-like-ash on this internet failure. The router answers but nothing resolves. Fan out the causes, jump to the middle, and find the tiny detail.
```

## What you get

The [fault-web tracker](scripts/think_like_ash.py) enforces layer coverage, tracks convergence, and prints the detail and resolution checklists.

## Pair it with

[Diagnosing bugs](../diagnosing-bugs/README.md) · [Break it](../break-it/README.md)
