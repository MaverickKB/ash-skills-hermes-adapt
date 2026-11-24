# Drafter forge

Build, verify and gate on-policy companion drafters for `tensorfold serve --drafter` to multiply single-stream decode speed with exact token parity.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You want to adapt, evaluate or ship a companion speculative draft model for TensorFold on your own coding or prose workload without risking vocab mismatches, MoE row-cost slowdowns or output drift.

## Try it

```text
Use drafter-forge. Verify tokenizer alignment between my target and draft checkpoints, harvest on-policy workload completions, solve the optimal draft depth, and gate --drafter parity and speedup against --no-drafts.
```

## What you get

Use the [companion drafter toolkit](scripts/drafter_forge.py) for tokenizer checking, on-policy harvesting, acceptance/row-cost depth optimization and parity gating, backed by the [TensorFold drafter craft guide](references/drafter-craft.md).

## Pair it with

[One-stream lanes](../one-stream-lanes/README.md) · [Output parity](../output-parity/README.md) · [Fair bench](../fair-bench/README.md) · [Speedup referee](../speedup-referee/README.md)
