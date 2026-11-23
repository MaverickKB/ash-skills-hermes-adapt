# Shrink model

Plans structural pruning and recovery training from an open-weight parent checkpoint.

## When to use it

You need a smaller descendant model within a total or active parameter budget.

Count the parent's parameters, choose a candidate that meets the budget, and verify the child checkpoint against the pruning manifest.

## What's included

The scripts count parameters, search pruning budgets, and validate checkpoint manifests.

Open [SKILL.md](SKILL.md) for the full instructions.
