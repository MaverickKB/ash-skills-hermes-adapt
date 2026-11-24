# Graph and dispatch surgeon

Eliminate between-pass host syncs, CUDA graph recaptures, Metal command-buffer stalls and serialized drafting or grammar bubbles in TensorFold's decode loop.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

Traces show low GPU busy share, host round trips between decode passes, CUDA graph recaptures on row-width shifts, or serialized grammar/proposer latency.

## Try it

```text
Use graph-and-dispatch-surgeon. Audit the between-pass stalls in this decode trace, move dynamic metadata into device buffers, and verify zero recaptures and exact token hashes across forced widths.
```

## What you get

Use the [decode dispatch auditor](scripts/dispatch_audit.py) for bubble classification and forced-width invariance gating, backed by the [zero-stall loop guide](references/zero-stall-loop.md).

## Pair it with

[Critical path X-ray](../critical-path-xray/README.md) · [One-stream lanes](../one-stream-lanes/README.md) · [Kernel contract](../kernel-contract/README.md) · [TensorFold speed hunt](../tensorfold-speed-hunt/README.md)
