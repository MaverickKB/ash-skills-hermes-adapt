# MoE X-ray

Profile TensorFold MoE routing traces (`--fsr-route-trace`), plan hot-expert residency for `--ssd-experts`, catch quantization router drift and map dead experts.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You are serving or auditing a Mixture-of-Experts checkpoint in TensorFold and want to see which experts actually fire on your workload, how much GPU memory buys a 90% hot-expert hit rate, or whether a lower quant shifted router decisions.

## Try it

```text
Use moe-xray. Profile this TensorFold --fsr-route-trace log, compute the hot-expert residency curve for a 24 GiB VRAM budget, and diff bf16 vs mlx-q4g64 router drift.
```

## What you get

Use the [MoE routing analyzer](scripts/moe_xray.py) for activation histograms, entropy, K90 hot sets, `--ssd-experts` residency curves, pruning manifests and JSD router-drift checks, backed by the [MoE routing guide](references/moe-routing-and-residency.md).

## Pair it with

[VRAM alchemist](../vram-alchemist/README.md) · [Quant autopsy](../quant-autopsy/README.md) · [Prefill and KV](../prefill-and-kv/README.md) · [Model bring-up](../model-bringup/README.md)
