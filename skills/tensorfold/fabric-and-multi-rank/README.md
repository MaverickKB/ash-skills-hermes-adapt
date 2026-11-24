# Fabric and multi-rank

Model MCDMA, NVLink, PCIe and RoCE collective overhead and gate multi-rank TensorFold binaries, deterministic reduction parity and scaling efficiency.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You are scaling TensorFold across multiple GPUs or peer Macs (`--parallel`, MCDMA, NCCL, RCCL) and want to know whether TP, EP, PP or `--ssd-experts` wins and prove multi-rank token-hash parity.

## Try it

```text
Use fabric-and-multi-rank. Model 2-rank and 4-rank TP collective overhead on our interconnect, compare against PP, and gate our multi-rank receipt for uniform binaries, single-rank token parity and scaling efficiency.
```

## What you get

Use the [multi-rank collective planner and rank gate](scripts/fabric_plan.py) with the [collective scaling guide](references/multi-rank-collectives.md).

## Pair it with

[Serving concurrency](../serving-concurrency/README.md) · [TensorFold speed hunt](../tensorfold-speed-hunt/README.md) · [Breakthrough architect](../breakthrough-architect/README.md) · [VRAM alchemist](../vram-alchemist/README.md)
