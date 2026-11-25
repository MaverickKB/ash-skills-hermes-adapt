# TensorFold and inference

TensorFold groups eighteen inference skills. Install each one by its own name, such as `hermes skills install MaverickKB/ash-skills-hermes-adapt/skills/breakthrough-architect` or `hermes skills install MaverickKB/ash-skills-hermes-adapt/skills/tensorfold-speed-hunt`.

Start with the [performance playbook](../skills/mythos-mode/references/inference-performance.md) when you need to find where a served request spends its time. It comes with Mythos Mode, so you can use it after installing that skill on its own.

```text
Use Mythos Mode's TensorFold performance playbook. Trace prefill and decode for
this served workload, then choose a specialist based on the measured bottleneck.
```

| What you need | Open |
| --- | --- |
| Discover 2x-10x silicon breakthroughs, run 10-minute kill-probes and autotune on chip | [Breakthrough architect](../skills/breakthrough-architect/README.md) |
| Run the combined 7-gate local TensorFold optimization loop | [TensorFold speed hunt](../skills/tensorfold-speed-hunt/README.md) |
| Model MCDMA/NVLink/PCIe collectives and gate multi-rank reduction parity | [Fabric and multi-rank](../skills/fabric-and-multi-rank/README.md) |
| Trace GPU vs host vs idle shares, rooflines and Amdahl bounds | [Critical path X-ray](../skills/critical-path-xray/README.md) |
| Guard untouched kernels against SASS, register and spill regressions | [SASS and AIR diff](../skills/sass-and-air-diff/README.md) |
| Block repeat dead ends, score mirrored A/B runs and track owed ports | [Optimization ledger](../skills/optimization-ledger/README.md) |
| Eliminate between-pass host syncs and CUDA/Metal graph recaptures | [Graph and dispatch surgeon](../skills/graph-and-dispatch-surgeon/README.md) |
| Bring up a checkpoint and verify its tensor layout | [Model bring-up](../skills/model-bringup/README.md) |
| Compare kernel outputs against a numerical contract | [Kernel contract](../skills/kernel-contract/README.md) |
| Size the KV cache or check prefill state | [Prefill and KV](../skills/prefill-and-kv/README.md) |
| Improve single-stream decode throughput | [One-stream lanes](../skills/one-stream-lanes/README.md) |
| Measure the effect of concurrent requests | [Serving concurrency](../skills/serving-concurrency/README.md) |
| Compare model outputs across builds | [Output parity](../skills/output-parity/README.md) |
| Review a speedup claim independently | [Speedup referee](../skills/speedup-referee/README.md) |
| Solve TensorFold VRAM fit, KV spill and SSD expert flags | [VRAM alchemist](../skills/vram-alchemist/README.md) |
| Compare all TensorFold quants on tool calling, context cliffs and Pareto frontier | [Quant autopsy](../skills/quant-autopsy/README.md) |
| Verify, adapt and gate companion drafters for `--drafter` | [Drafter forge](../skills/drafter-forge/README.md) |
| Profile `--fsr-route-trace` MoE routing, `--ssd-experts` residency and drift | [MoE X-ray](../skills/moe-xray/README.md) |

[All skills](../README.md#skill-directory)
