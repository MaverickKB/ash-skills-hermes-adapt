---
name: kernel-contract
description: Designs and verifies native GPU and CPU inference kernels (Metal, CUDA, Zig, C++, Rust) under an explicit bit-exact or ULP numerical contract, testing non-power-of-two tail shapes, KV ring boundaries, NaN/Inf guards and planted negative controls before shipping. Ships a stdlib IEEE-754 ULP and bit-exact tensor gate (`kernel_gate.py`). Use when writing, fusing, replacing or optimizing a quantized GEMV/GEMM, attention, RMSNorm, RoPE, MoE or sampling kernel in an inference engine.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, kernels, numerical-parity, metal]
    related_skills: [output-parity, model-bringup]
---

# Kernel contract

Fast GPU and CPU kernels fail in the dark corners: odd row counts (`M=3, 7, 13`), unaligned `K` tails, wrapped KV ring buffers, silent reduction-order drift that flips a near-tie token 400 tokens later, or a dispatch guard that keeps the new kernel from ever executing inside a real served request. Every kernel earns its place through an explicit contract and a gate that has been seen to fail.

## 1. Declare the contract before writing code

Every kernel change declares one of two contracts up front:

- **Bit-exact replacement (`--bit-exact`).** Produces identical output bytes across every shape and input, including the sign, payload and quiet/signaling bit of each NaN. Use raw binary tensors to verify those bits from the original output. JSON numbers are compared in the declared dtype's encoding. Required whenever a rewrite claims zero numerical change.
- **Bounded numerical change (`--max-ulp` / `--atol` / `--rtol`).** Allows altered floating-point reduction order or fusion math. Requires an independent FP64 reference, an explicit pre-declared tolerance, and maintainer approval whenever it can alter generated tokens once.

## 2. Cover the hazard shape matrix

Print the canonical hazard matrix with `scripts/kernel_gate.py shapes` and test every applicable shape:

- **Decode and draft windows (`M`):** `1, 2, 3, 4, 7, 8, 13, 16, 31, 32, 65`
- **Prefill (`M`):** `1, 17, 64, 127, 128, 257, 512, 1025`
- **Unaligned `K` and `N` tails:** `1, 3, 15, 31, 33, 63, 127, 129, 255, 513`
- **KV ring and masks:** empty (`0`), single (`1`), exact page boundary, wrapped ring (`head > tail`), tree/causal masks
- **Numeric hazards:** all zeros, `-0.0`, subnormals, large magnitudes, argmax ties, `NaN`/`Inf` propagation

## 3. Run the tensor gate with automatic planted-fault control

A JSON tensor supplies a flat `values` array. Its optional `shape` contains nonnegative integer dimensions whose product must equal the number of values. Omit `shape` for a vector or use `[]` for a single scalar. For `i32`, each value must be an integer in the signed 32-bit range. Inconsistent input exits 2 before comparison.

`scripts/kernel_gate.py` needs only Python 3.9+ (stdlib `struct` + `math`). It compares JSON or raw little-endian binary tensors (`f16`, `f32`, `f64`, `i32`), computes exact IEEE-754 ULP distance, checks `NaN`/`Inf` alignment, and automatically perturbs one element past the allowed budget to confirm the gate rejects it.

```bash
# Bit-exact replacement check on raw f32 outputs
python3 scripts/kernel_gate.py compare --ref-bin ref.f32 --cand-bin cand.f32 --dtype f32 --bit-exact

# FP64-referenced kernel check with explicit ULP cap
python3 scripts/kernel_gate.py compare --ref ref.json --cand cand.json --max-ulp 2 --atol 1e-4 --rtol 1e-4
```

Exit codes: `0` on `KERNEL: PASS` (candidate within contract and planted fault rejected), `1` on `KERNEL: FAIL` or `KERNEL: CONTROL FAILED` (budget too loose), `2` on usage/dtype mismatch.

## 4. Prove it runs in serving and avoid known losing traps

- **Confirm dispatch from a trace or capture, never from reading code.** A kernel that steps aside when a row limit or batch guard binds can win a microbenchmark and change nothing served.
- **Check untouched kernels.** Compare compiled assembly/PTX/AIR of kernels you did not intend to change so register pressure or shared helpers did not silently regress a neighbor.
- **Do not time a kernel by skipping it.** A knockout changes cache residency for the next kernel and skews both directions.
- **Watch out for documented serving traps:** cutting dispatch count alone with no critical-path reduction, naive secondary-queue prefetching that contends on bandwidth, and device-wide persistent barriers that stall concurrent waves.
- **Gate the served build next:** after `kernel_gate.py` passes, run full token-hash parity (`model-bringup` / `output-parity`) and mirrored A/B serving benchmarks (`fair-bench` / `speedup-referee`).

## Backend playbook

Read `references/metal-and-cuda.md` for Metal simdgroup/attention-range/working-set craft, CUDA FP4/FP8 paired decode, CUDA graphs, SASS identity verification on untouched kernels, and the serving anti-patterns ledger.
