# VRAM alchemist

Solve exact TensorFold VRAM and unified-memory fit, size KV caches and SSD expert residency, and emit `tensorfold serve` and `tensorfold benchmark` commands before pulling a checkpoint.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You want the highest-quality TensorFold quant (`bf16`, `modelopt-fp8`, `modelopt-nvfp4`, `exlr8`, or `mlx-q8` through `mlx-q2`) and exact `--context`, `--kv-cache`, `--prompt-cache-gib`, `--spill-gib` or `--ssd-experts` flags for your hardware before running `tensorfold pull`.

## Try it

```text
Use vram-alchemist. Probe my hardware and TensorFold capabilities, solve every quant tier for this Hugging Face model at 64K context, and give me the exact tensorfold serve and benchmark commands.
```

## What you get

Use the [TensorFold VRAM solver](scripts/vram_solve.py) for hardware and `tensorfold capabilities --json` probing, zero-download HF checkpoint inspection and multi-quant memory solving, backed by [TensorFold quant and flag tables](references/quant-formats-and-flags.md).

## Pair it with

[Quant autopsy](../quant-autopsy/README.md) · [Prefill and KV](../prefill-and-kv/README.md) · [Model bring-up](../model-bringup/README.md) · [Fair bench](../../fair-bench/README.md)
