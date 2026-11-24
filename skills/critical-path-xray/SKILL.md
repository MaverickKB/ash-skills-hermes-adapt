---
name: critical-path-xray
description: Parses served TensorFold traces (`nsys` CSVs, Metal/Zig `FZ_ROUND_TIMES` logs and JSON step dumps) to account for GPU compute/bandwidth, host dispatch, idle gaps and per-kernel Amdahl speedup ceilings. Classifies kernels against chip memory and compute rooflines (`rtx5090`, `m5-max`, `m4-max`, `h100`), screens non-overlapping optimization candidates above the 3% bar, and catches dispatch lies when an expected kernel never executed in the trace (`xray_trace.py`). Use at the start of a TensorFold performance investigation when tracing where wall time goes, computing rooflines, or confirming a kernel ran in a served request.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, inference, profiling, traces]
    related_skills: [optimization-ledger, tensorfold-speed-hunt]
---

# Critical path X-ray

Guessing bottlenecks from source code wastes days optimizing kernels that take 2% of a served step or never even dispatch. Parse the real trace first, measure GPU vs host vs idle wall shares, bound every candidate by Amdahl's law, and verify your optimized kernel actually appeared in the trace.

## Ground rules

1. **Trace the served path.** Measure an intact served request via `FZ_ROUND_TIMES`, Metal capture or `nsys`, never by knocking kernels out.
2. **Prove dispatch from the trace.** Pass `--expect-kernel` to confirm the kernel you modified actually executed on the served workload; reading `if` branches in host code is not evidence.
3. **Bound the change, never the goal.** Compute each candidate's Amdahl ceiling and count overlapping savings once before coding, skipping standalone ideas below 3% unless they unlock a combined fast path.
4. **Separate measured numbers from estimates.** Never mix measured trace milliseconds with back-of-the-envelope bandwidth numbers in the same column.

## Step 1: Audit a served trace

Feed an `nsys` kernel CSV, a TensorFold JSON/JSONL step trace, or an `FZ_ROUND_TIMES` log into `scripts/xray_trace.py audit`:

```bash
python3 scripts/xray_trace.py audit notes/decode_trace.json \
  --chip m5-max --expect-kernel qmm_group --json notes/xray.json
```

It reports:

- Wall time split into GPU execution, host overhead and idle gaps, plus the Amdahl ceiling for eliminating between-pass gaps.
- Per-kernel GPU time, wall share %, Amdahl max end-to-end gain %, achieved GB/s, microseconds per launch and regime (`BW_BOUND`, `COMPUTE_BOUND`, `LAUNCH_BOUND`, `UNDERUTILIZED`).
- Exits `1` (`XRAY: EXPECTED_KERNEL_MISSING`) if any `--expect-kernel` substring never ran in the trace.

## Step 2: Check single-kernel rooflines

```bash
python3 scripts/xray_trace.py roofline \
  --bytes-gib 14.2 --gflops 420 --time-ms 9.4 --launches 48 --chip rtx5090
```

Computes arithmetic intensity (`FLOP/byte`), hardware ridge point, achieved GB/s (% of peak), achieved TFLOP/s (% of peak) and bound classification.

## Step 3: Screen candidates with non-overlapping Amdahl bounds

```bash
python3 scripts/xray_trace.py bound notes/candidates.json --min-standalone-pct 3.0 --json notes/bounds.json
```

Screens each candidate against `--min-standalone-pct`, deduplicates savings within each `overlap_group`, and computes the combined non-overlapping speedup ceiling. Exits `1` if zero candidates clear the screen.

## Step 4: Route to the right specialist

- `host_share + idle_share >= 20%`: switch to `graph-and-dispatch-surgeon`.
- Top kernel `UNDERUTILIZED` or `LAUNCH_BOUND`: optimize under `kernel-contract` and verify zero neighbor regressions with `sass-and-air-diff`.
- Top kernel `BW_BOUND` on single-stream decode: push tokens landed per pass with `one-stream-lanes` and `drafter-forge`.
- Record the chosen hypothesis in `optimization-ledger` before editing code.

## Reference guide

Read `references/trace-and-roofline.md` for low-overhead Metal/CUDA capture switches, interpreting roofline regimes and counting overlapping savings honestly.
