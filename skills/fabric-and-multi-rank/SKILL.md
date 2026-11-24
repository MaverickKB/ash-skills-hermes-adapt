---
name: fabric-and-multi-rank
description: Models multi-device collective latency and bandwidth across NVLink, PCIe, MCDMA peer fabric, Thunderbolt 5 and RoCE for TensorFold (`--parallel`, `--decode-share`), deciding whether Tensor Parallel, Expert Parallel, Pipeline Parallel or single-node `--ssd-experts` actually wins (`model-collectives`). Gates multi-rank serving receipts for uniform per-rank binary hashes, deterministic fixed-order reduction parity against single-rank 64-hex `token_sha256` digests, and scaling efficiency (`gate-ranks`) via `fabric_plan.py`. Use when scaling TensorFold across multiple GPUs or peer Macs, debugging slow multi-GPU TP, or gating multi-rank exactness.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, multi-gpu, collectives, nvlink]
    related_skills: [breakthrough-architect, serving-concurrency]
---

# Fabric and multi-rank

Splitting a model across two GPUs or two peer Macs often makes solo decode *slower* because `2 * L` per-layer all-reduces pay link-launch latency 100-160 times per token. Model the collective math on your exact link topology first, pick TP vs EP vs PP honestly, and prove multi-rank reduction matches single-rank token hashes before shipping.

## Ground rules

1. **Price latency before splitting.** Tiny `M = 1` activation payloads are latency-bound, not bandwidth-bound. Run `model-collectives` before investing in TP over PCIe or Thunderbolt.
2. **Verify every rank's binary.** A stale binary on rank 1 invalidates scaling numbers and parity receipts; `gate-ranks` rejects mismatched per-rank `binary_sha256` digests.
3. **Keep reduction order deterministic.** Fixed rank reduction order keeps multi-rank outputs reproducible across runs and comparable against single-rank execution. Never hide reduction-order token drift without explicit maintainer sign-off.

## Step 1: Model collective overhead on your link topology

```bash
python3 scripts/fabric_plan.py model-collectives \
  --layers 64 --hidden 5120 --batch-rows 1 --ranks 2 \
  --topology pcie5 --single-rank-step-ms 12.0 \
  --json notes/fabric_model.json
```

Computes exact all-reduce sync count, payload KiB, collective milliseconds, collective share %, projected TP speedup and scaling efficiency alongside Pipeline Parallel boundary cost. Exits `0` (`TP_VIABLE` or `EP_OR_PP_PREFERRED`) or `1` (`COLLECTIVE_BOUND_ABORT`) when TP is slower than 1 rank.

## Step 2: Gate multi-rank binaries, parity and scaling

```bash
python3 scripts/fabric_plan.py gate-ranks notes/multi_rank_receipt.json \
  --min-scaling-eff 0.55 --json notes/fabric_gate.json
```

Verifies:

1. At least two distinct nonnegative integer rank IDs and identical `binary_sha256` values across those ranks.
2. Exact 64-hex `token_sha256` equality between `single_rank` and `multi_rank` on every prompt cell (pass `--allow-reduction-drift` only after explicit maintainer arithmetic-order approval).
3. Real scaling efficiency (`multi_rank_tps / single_rank_tps / ranks`) clearing `--min-scaling-eff`.

Exits `0` (`FABRIC GATE: PASS`) or `1` (`FABRIC GATE: FAIL`).

## Reference guide

Read `references/multi-rank-collectives.md` for NVLink vs PCIe vs MCDMA latency math, TP/EP/PP/`--ssd-experts` selection rules and deterministic all-reduce design.
