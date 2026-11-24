# Breakthrough architect

Stage 0 R&D engine for TensorFold: quantify silicon physics gaps, invert big-engine assumptions with 10-minute kill-probes, autotune directly on target chips, and stack 3x-10x orthogonal multipliers.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You want step-function 2x-10x performance leaps in TensorFold on local Metal or CUDA silicon rather than incremental +3% kernel tweaks.

## Try it

```text
Use breakthrough-architect. Compute our four silicon physics gaps on this checkpoint, rank the biggest 2x-10x breakthrough classes, design a 10-minute kill-probe, and autotune the winning kernel/lane parameters directly on silicon.
```

## What you get

Use the [breakthrough physics and autotune lab](scripts/breakthrough_lab.py) with the [example autotune search space](assets/autotune.example.json) and [first-principles breakthrough playbook](references/breakthrough-playbook.md).

## Pair it with

[TensorFold speed hunt](../tensorfold-speed-hunt/README.md) · [Critical path X-ray](../critical-path-xray/README.md) · [One-stream lanes](../one-stream-lanes/README.md) · [Graph and dispatch surgeon](../graph-and-dispatch-surgeon/README.md)
