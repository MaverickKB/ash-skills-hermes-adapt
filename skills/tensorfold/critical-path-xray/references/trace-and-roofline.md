# Tracing TensorFold requests and reading roofline ceilings

How to capture clean served traces on CUDA and Apple Silicon Metal without distorting timings, separate real kernel limits from launch/sync gaps, and screen candidates with Amdahl bounds.

## Contents

1. Capturing low-overhead served traces
2. Reading GPU, host and idle shares
3. Using hardware rooflines without declaring false ceilings
4. Screening candidates with non-overlapping Amdahl bounds

## 1. Capturing low-overhead served traces

- **Metal and Zig engine logs.** Start from TensorFold's built-in switches before attaching external profilers: `FZ_ROUND_LOG` for rows verified and landed, `FZ_ROUND_TIMES=1` for host, GPU and idle time per shared round, and `FZ_BUSY` for GPU busy share. Use a Metal GPU capture to confirm which compiled kernel actually dispatched.
- **CUDA with Nsight Systems.** Capture a real served request with `nsys profile` and export the kernel summary via `nsys stats --report cuda_gpu_kern_sum --format csv`. Keep heavyweight Nsight Compute replay off processes that hold a full checkpoint in VRAM; replay isolated shapes instead.
- **Never measure by knockout.** Skipping a kernel changes what the next kernel finds in L2 and VRAM caches, distorting both directions. Read each kernel's time from a trace of the intact served round.

## 2. Reading GPU, host and idle shares

A fast kernel cannot fix an idle chip. Always check the three-way wall split from `xray_trace.py audit`:

- **`host_share + idle_share >= 20%`.** Hand off to `graph-and-dispatch-surgeon` first. Time is leaking into CPU round trips, synchronous acceptance, command-buffer gaps or CUDA graph recaptures.
- **`gpu_busy_pct >= 80%` and top kernel is `LAUNCH_BOUND`.** Small kernels (`< 15 us/launch`) are spending most of their time in dispatch overhead; fuse adjacent elementwise, norm, RoPE or residual kernels under `kernel-contract`.
- **`gpu_busy_pct >= 80%` and top kernel is `UNDERUTILIZED`.** Achieved GB/s and TFLOP/s are both far below hardware peaks; check occupancy, register spills (`sass-and-air-diff`), memory coalescing, or narrow single-stream row counts (`one-stream-lanes`).
- **`gpu_busy_pct >= 80%` and top kernel is `BW_BOUND`.** On decode, stop trying to save ALU instructions and attack bytes moved or tokens landed per weight read (`one-stream-lanes` and `drafter-forge`).

## 3. Using hardware rooflines without declaring false ceilings

Use achieved GB/s and TFLOP/s to **diagnose why a kernel is slow**, never to declare an end-to-end speedup goal impossible. On single-stream decode, reading weights once for 16 drafted rows multiplies effective tokens per second without exceeding physical DRAM bandwidth.

## 4. Screening candidates with non-overlapping Amdahl bounds

Before editing a line of Zig, Metal or CUDA code, feed your ranked hypotheses into `xray_trace.py bound`:

- Count overlapping savings once (two optimizations removing the same 1.5 ms stall share one `overlap_group` and cannot both claim 1.5 ms).
- Apply a `3%` standalone screen on the target served cell, keeping smaller changes only when explicitly marked `"enabler": true` for a larger cross-layer fast path.
- Always pass `--expect-kernel` after building a new kernel so the trace proves your new path actually executed inside the live server.
