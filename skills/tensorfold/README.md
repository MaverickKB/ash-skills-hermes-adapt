# TensorFold and inference

Start with the [performance playbook](../mythos-mode/references/inference-performance.md) when you need to find where a served request spends its time. It comes with Mythos Mode, so you can use it after installing that skill on its own.

```text
Use Mythos Mode's TensorFold performance playbook. Trace prefill and decode for
this served workload, then choose a specialist based on the measured bottleneck.
```

| What you need | Open |
| --- | --- |
| Bring up a checkpoint and verify its tensor layout | [Model bring-up](model-bringup/README.md) |
| Compare kernel outputs against a numerical contract | [Kernel contract](kernel-contract/README.md) |
| Size the KV cache or check prefill state | [Prefill and KV](prefill-and-kv/README.md) |
| Improve single-stream decode throughput | [One-stream lanes](one-stream-lanes/README.md) |
| Measure the effect of concurrent requests | [Serving concurrency](serving-concurrency/README.md) |
| Compare model outputs across builds | [Output parity](output-parity/README.md) |
| Review a speedup claim independently | [Speedup referee](speedup-referee/README.md) |

[All skills](../../README.md#skill-directory)
