# Kernel contract

Catch a changed bit in a new kernel even when ordinary inputs still look fine.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You're replacing or fusing an inference kernel and need an exact or tolerance-bounded comparison.

## Try it

```text
Use kernel-contract. Compare the old and new attention outputs on odd dimensions, signed zero, and NaNs. Include one deliberate mismatch.
```

## What you get

The [tensor gate](scripts/kernel_gate.py) compares JSON or raw binary tensors and checks that a deliberately changed value fails.

## Pair it with

[Model bring-up](../../../skills/tensorfold/model-bringup/README.md) · [Output parity](../../../skills/tensorfold/output-parity/README.md)
