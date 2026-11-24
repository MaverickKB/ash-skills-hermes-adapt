# Multi-rank collectives, MCDMA peer fabric and deterministic reduction

How interconnect latency governs whether Tensor Parallel (`--parallel`), Expert Parallel, Pipeline Parallel or single-node `--ssd-experts` wins in TensorFold, and how to preserve single-rank token hashes across ranks.

## Contents

1. Why Tensor Parallel loses on slow links at `M = 1`
2. Choosing TP vs EP vs PP vs `--ssd-experts`
3. Deterministic fixed-order all-reduce
4. Multi-rank receipt checklist

## 1. Why Tensor Parallel loses on slow links at `M = 1`

Standard Tensor Parallel executes 2 all-reduces per transformer layer (after attention output projection and after MLP down projection). On an 80-layer model, that is **160 collective synchronizations per decode step**.

At `M = 1` solo decode, each payload is tiny (`1 * 4096 * 2 = 8 KiB`), so link latency (`alpha`), not link bandwidth (`beta`), dominates:

- **NVLink 4/5 (`~3.5-4.5 us/sync`):** 160 syncs cost `~0.6-0.8 ms/step`, letting TP scale cleanly across 2-8 GPUs.
- **PCIe Gen4/5 without P2P or MCDMA over Thunderbolt 5 (`~14-22 us/sync`):** 160 syncs cost `~2.5-4.5 ms/step`, which can exceed the weight-read savings on a fast model and make 2-rank TP *slower* than 1 rank.

Always run `fabric_plan.py model-collectives` before wiring a multi-rank deployment.

## 2. Choosing TP vs EP vs PP vs `--ssd-experts`

| Topology & bottleneck | Best strategy | Why |
| --- | --- | --- |
| High-speed NVLink / direct P2P (`<= 6 us` sync) | Tensor Parallel (`--parallel N`) | Low sync latency unlocks both memory pooling and near-linear solo decode speedup |
| Dual PCIe desktop GPUs or MCDMA peer Macs (`14-22 us` sync) | Pipeline Parallel (PP) for memory fit, or Expert Parallel (EP) for MoE | PP pays only `R - 1` boundary hops per step instead of `2 * L` syncs |
| Large MoE on one fast NVMe machine | Single-node `--ssd-experts` + `moe-xray` hot pinning | Avoids cross-host network hops completely when hot experts fit locally |
| High concurrent traffic (`C >= 8`) on PCIe / MCDMA | Data-parallel replicas or batched TP | Larger `M` amortizes fixed per-sync latency across many requests |

## 3. Deterministic fixed-order all-reduce

Floating-point addition is non-associative. Dynamic tree reductions whose arrival order depends on network jitter produce nondeterministic token flips across runs and diverge from single-rank execution.

- Enforce a fixed deterministic reduction order across ranks (`rank 0 + rank 1 + ...`) inside TensorFold's collective interface (`NCCL`, `RCCL`, or `MCDMA`).
- Whenever multi-rank reduction alters even one token relative to single-rank execution, treat it as an explicit arithmetic-order change requiring maintainer sign-off before shipping.

## 4. Multi-rank receipt checklist

Record every field before running `fabric_plan.py gate-ranks`:

- Topology, measured link bandwidth and latency
- Exact `binary_sha256` on **every** rank (a stale binary on rank 1 invalidates every measurement)
- Single-rank baseline `decode_tps` and per-cell 64-hex `token_sha256` digests on the exact same prompt fixtures
- Multi-rank `decode_tps` and per-cell 64-hex `token_sha256` digests
