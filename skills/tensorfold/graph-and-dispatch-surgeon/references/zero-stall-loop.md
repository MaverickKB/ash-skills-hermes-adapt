# Eliminating CPU round trips, graph recaptures and decode bubbles

Architecture patterns for keeping CUDA and Apple Metal silicon continuously busy across dynamic decode widths, speculative drafting and grammar masking in TensorFold.

## Contents

1. Removing the host from speculative acceptance
2. Indirect device metadata for zero-recapture graphs
3. Overlapping late drafting and grammar masking
4. Proving dynamic-width exactness

## 1. Removing the host from speculative acceptance

When a decode loop copies logits or accepted-token counts back to the host on every forward pass, GPU execution halts while the CPU decides the next window and submits the next command buffer.

- Move both **greedy** and **seeded-sampled** speculative acceptance onto device kernels using deterministic counter-based RNG seeded from the request seed and logical token position.
- Keep the live token cursor, KV sequence length and accepted count in device memory so the next step starts immediately without a host barrier.
- Flush generated token IDs to the host asynchronously on a separate stream or ring buffer while the GPU keeps advancing.

## 2. Indirect device metadata for zero-recapture graphs

Single-stream speculative decode shifts row count `M` every step (`1 -> 4 -> 8 -> 3 -> 16`) as acceptance and proposers fluctuate. Re-capturing a CUDA graph or rebuilding a Metal pipeline whenever `M`, KV page count or tree mask changes destroys the benefit of graph replay.

- Indirect dynamic parameters (`active_rows`, `tree_mask`, `kv_block_table`, `seq_lens`) through device-resident metadata buffers updated by a tiny memcpy or device kernel prior to graph launch.
- Cap tile grids to the maximum bucket width and let kernels exit cleanly on `row >= active_rows` read from device memory (never slowing `M=1..4` narrow prose).
- On Apple Metal, encode repeated layer passes via indirect command buffers (ICBs) or persistent batched command buffers to keep host encoding latency off the critical path.

## 3. Overlapping late drafting and grammar masking

- **`late_draft`:** Draft the next speculative window in parallel while the GPU executes the verification pass instead of waiting for verification to return.
- **`grammar_parallel`:** Compute legal token masks concurrently with model forward layers so strict JSON/tool grammar enforcement adds zero wall time before sampling.

## 4. Proving dynamic-width exactness

Indirect graphs and device-side acceptance introduce subtle index bugs at non-power-of-two widths (`3, 7, 13, 31`) and KV page boundaries. Always build a forced-width matrix (`rows = 1, 2, 3, 4, 7, 8, 13, 16, 32` with `graph=off` and `graph=on`) and verify every run against the plain scalar `reference_token_sha256` with `dispatch_audit.py verify-invariance` before benchmarking.
