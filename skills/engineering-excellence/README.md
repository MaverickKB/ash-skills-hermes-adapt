# Engineering excellence

Plan the change around the interfaces and failure cases your callers already rely on.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're adding a feature, reviewing production code, or refactoring a shared module.

## Try it

```text
Use engineering-excellence. Split this storage module, keep its public behaviour intact, and verify how callers handle partial failures and retries.
```

## What you get

Read the [architecture playbook](references/architecture.md) for design trade-offs and the [verification playbook](references/verification.md) for checks to run before shipping.

## Pair it with

[Blast radius](../../skills/blast-radius/README.md) · [Prove it](../../skills/prove-it/README.md)
