# Quant autopsy

Compare every TensorFold quant of a checkpoint side by side on uncontaminated tool-calling, JSON, reasoning, code-trace, effective-context and sycophancy tests.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You want to see how all TensorFold quants of a checkpoint perform (`bf16`, `modelopt-fp8`, `exlr8`, `modelopt-nvfp4`, `mlx-q8` down through `mlx-q2`, plus `--kv-cache fp8`) and find the exact quant on the quality-speed-memory Pareto frontier.

## Try it

```text
Use quant-autopsy. Generate a fresh seeded suite up to 32K context, sweep all our TensorFold quants from bf16 down to mlx-q2g64, and build the all-quant Markdown and HTML Pareto report.
```

## What you get

Use the [all-quant benchmark and Pareto reporter](scripts/quant_autopsy.py) with the [example TensorFold sweep manifest](assets/sweep.example.json) and [quant failure-mode guide](references/quant-failure-modes.md).

## Pair it with

[VRAM alchemist](../vram-alchemist/README.md) · [Output parity](../output-parity/README.md) · [Local coder overclock](../../local-coder-overclock/README.md) · [Fair bench](../../fair-bench/README.md)
