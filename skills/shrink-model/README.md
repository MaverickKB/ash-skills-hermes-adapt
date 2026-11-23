# Shrink model

Plan a smaller descendant of an open-weight checkpoint and check that the candidate fits your parameter budget.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You need fewer total or active parameters through structural pruning and recovery training.

## Try it

```text
Use shrink-model. Count this checkpoint's parameters, propose a pruning plan within my budget, and verify it against a manifest.
```

## What you get

Start with the [parameter counter](scripts/count_params.py), explore the [budget calculator](scripts/budget.py), and verify the result with the [manifest checker](scripts/check_manifest.py).

## Pair it with

[Model bring-up](../../skills/tensorfold/model-bringup/README.md) · [Output parity](../../skills/tensorfold/output-parity/README.md)
