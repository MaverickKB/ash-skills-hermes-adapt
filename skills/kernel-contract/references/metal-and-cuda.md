# Metal and CUDA backend kernel craft

Backend-specific rules and patterns for native inference kernels built under `kernel-contract`.

## Contents

1. Live row-count dispatch and narrow-prose protection
2. Metal patterns and traps
3. CUDA patterns and traps
4. Kernel registry and untouched-kernel identity
5. Documented serving anti-patterns

## 1. Live row-count dispatch and narrow-prose protection

A single-stream engine shifts dynamically between `M=1` serial decode, `M=2..8` prose drafts, `M=8..32` copy/grammar/tree windows and `M=64..1024` prefill chunks.

- **Read live row counts on device.** Wide kernels that rely on host-side specialization per row count stall the command buffer or trigger redundant pipeline rebuilds. Pass the active row count in device memory so the GPU kernel adapts without a host sync.
- **Never slow narrow windows to help wide ones.** Benchmark `M=1` and `M=2..4` prose cells beside `M=8..32` wide cells. If a tiled wide kernel loses at `M=1..3`, keep the tight narrow kernel for small `M` and verify identical token hashes across the dispatch threshold (depth/width sweep).
- **Confirm dispatch from capture or trace.** Never assume a new kernel ran because the host `if` branch looks right. Row limits, alignment guards, cache paths and batch-cap checks silently route back to fallback kernels.

## 2. Metal patterns and traps

- **Use simdgroup matrix tiles for grouped projections.** Structure quantized GEMM/GEMV around simdgroup-cooperative loads and explicit dequant-in-register loops rather than scalar thread loops.
- **Enforce attention range and precision contracts.** Hardware matrix units and FP16 accumulators overflow or lose precision on extreme attention logit ranges. Register an explicit attention-range contract covering long contexts, large logit scales and soft-capping, verified against an FP64 reference.
- **Log GPU duration and host wall time apart.** On Apple Silicon unified memory, host submission overhead, command-buffer encoding and GPU execution time can diverge sharply. A kernel that cuts GPU duration by 15% while adding host sync overhead can lose end-to-end.
- **Respect the Metal working-set allowance.** Unified memory shares RAM with the OS and compositor. Record both physical footprint and the GPU working-set allowance; do not attach replay profilers that duplicate full-model buffers inside tight memory.
- **Warm the GPU before timing.** Run roughly two seconds of warm-up work before scored Metal measurements so clocks settle out of idle power states, and verify mains power and zero thermal throttling.

## 3. CUDA patterns and traps

- **Paired low-bit decode and grouped QMM.** For FP4, FP8 and sub-byte quantized weights, unpack pairs directly into register fragments (`fp_pairs`) fused with grouped matrix multiplication (`qmm_group`) rather than materializing dequantized weight buffers to global memory.
- **CUDA graph capture and replay.** Keep dynamic lane counts, draft tree masks and paged KV block tables indirected through device-resident metadata buffers so one captured graph serves varying window widths without recapture stalls.
- **Nsight Systems first, Nsight Compute carefully.** Use Nsight Systems for critical-path served timelines (host gaps, stream waits, launches, copies). Keep Nsight Compute kernel replay off any process that holds a full model in tight VRAM; replay isolated fixtures instead.
- **Account for display carveout.** On desktop GPUs attached to a display, measure usable VRAM after the compositor carveout before sizing KV pools or prefill scratch buffers.
- **Check SASS identity on untouched kernels.** Header or shared-template edits frequently change register allocation and spill behavior in neighbor kernels. Disassemble (`cuobjdump --dump-sass`) and diff the SASS of kernels you did not intend to modify before landing.

## 4. Kernel registry and untouched-kernel identity

Keep one central kernel contract registry per backend (`metal`, `cuda`, `hip`, `xpu`, `cpu`) recording for every kernel:

- supported shapes, alignment constraints and launch limits
- exact contract mode (`bit-exact` or `FP64-bounded` with ULP/atol/rtol)
- positive tests across the hazard shape matrix (`M=1, 2, 3, 7, 8, 13, 16, 31, 32, 65`, odd `K`/`N`, wrapped KV rings, `NaN`/`Inf`)
- a planted negative control that has been seen to fail

## 5. Documented serving anti-patterns

Check the ledger before investing time in changes that repeatedly lose in real served inference:

- **Cutting dispatch count alone** without removing critical-path bytes or waits.
- **Prefetching next-layer weights from a second queue or CPU thread** when the primary queue already saturates memory bandwidth.
- **Persistent kernels waiting on device-wide barriers** that block concurrent waves or starve other work.
- **Single-GPU tensor parallelism** or splitting small matrices where sync cost exceeds compute gain.
- **Timing a kernel by knocking it out.** Skipping a kernel alters L2 residency for downstream kernels and invalidates the delta in both directions.
