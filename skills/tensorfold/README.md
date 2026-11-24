# TensorFold and inference

TensorFold groups sixteen inference skills. Install each one by its own name, such as `--skill tensorfold-speed-hunt` or `--skill kernel-contract`.

Start with the [performance playbook](../mythos-mode/references/inference-performance.md) when you need to find where a served request spends its time. It comes with Mythos Mode, so you can use it after installing that skill on its own.

```text
Use Mythos Mode's TensorFold performance playbook. Trace prefill and decode for
this served workload, then choose a specialist based on the measured bottleneck.
```

| What you need | Open |
| --- | --- |
| Run the combined 7-gate local TensorFold optimization loop | [TensorFold speed hunt](tensorfold-speed-hunt/README.md) |
| Trace GPU vs host vs idle shares, rooflines and Amdahl bounds | [Critical path X-ray](critical-path-xray/README.md) |
| Guard untouched kernels against SASS, register and spill regressions | [SASS and AIR diff](sass-and-air-diff/README.md) |
| Block repeat dead ends, score mirrored A/B runs and track owed ports | [Optimization ledger](optimization-ledger/README.md) |
| Eliminate between-pass host syncs and CUDA/Metal graph recaptures | [Graph and dispatch surgeon](graph-and-dispatch-surgeon/README.md) |
| Bring up a checkpoint and verify its tensor layout | [Model bring-up](model-bringup/README.md) |
| Compare kernel outputs against a numerical contract | [Kernel contract](kernel-contract/README.md) |
| Size the KV cache or check prefill state | [Prefill and KV](prefill-and-kv/README.md) |
| Improve single-stream decode throughput | [One-stream lanes](one-stream-lanes/README.md) |
| Measure the effect of concurrent requests | [Serving concurrency](serving-concurrency/README.md) |
| Compare model outputs across builds | [Output parity](output-parity/README.md) |
| Review a speedup claim independently | [Speedup referee](speedup-referee/README.md) |
| Solve TensorFold VRAM fit, KV spill and SSD expert flags | [VRAM alchemist](vram-alchemist/README.md) |
| Compare all TensorFold quants on tool calling, context cliffs and Pareto frontier | [Quant autopsy](quant-autopsy/README.md) |
| Verify, adapt and gate companion drafters for `--drafter` | [Drafter forge](drafter-forge/README.md) |
| Profile `--fsr-route-trace` MoE routing, `--ssd-experts` residency and drift | [MoE X-ray](moe-xray/README.md) |

[All skills](../../README.md#skill-directory)
