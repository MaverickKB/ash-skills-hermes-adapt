---
name: graph-and-dispatch-surgeon
description: Eliminates between-pass CPU stalls, CUDA graph recaptures, Metal command-buffer waits and serialized drafting or grammar overhead in TensorFold's decode loop. Classifies step-boundary dispatch pathologies (`GRAPH_RECAPTURE_STORM`, `HOST_SYNC_STALL`, `SERIAL_PROPOSER_OR_GRAMMAR`, `ENQUEUE_GAP_STARVATION`), computes zero-stall speedup ceilings, and verifies exact 64-hex `token_sha256` invariance with zero post-warmup recaptures across forced dynamic row widths (`dispatch_audit.py`). Use when GPU busy share drops below 90%, CUDA graphs recapture on width changes, or host syncs stall decode between passes.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, cuda-graphs, dispatch, host-sync]
    related_skills: [critical-path-xray, one-stream-lanes]
---

# Graph and dispatch surgeon

When `critical-path-xray` shows kernels running fast while GPU busy share sits at 60-80%, optimizing GEMM math will not save you. The missing speed is trapped between passes: synchronous CPU acceptance waits, CUDA graph recaptures whenever draft width `M` shifts, Metal command-buffer round trips, or serialized grammar and proposer work. Cut the CPU out of the decode loop without breaking token-for-token exactness.

## Ground rules

1. **Measure the bubble before touching the scheduler.** Run `dispatch_audit.py audit-gaps` on a live step trace to identify the dominant dispatch pathology and its exact zero-stall ceiling.
2. **One graph across dynamic widths.** Never recapture CUDA graphs or rebuild Metal pipelines on normal width (`M`) or KV page transitions; indirect dynamic metadata through device buffers.
3. **Never trade narrow-prose speed for graph overhead.** Verify `M=1..4` prose latency beside `M=8..32` drafted windows.
4. **Prove forced-width invariance.** Every graph-on and forced-width cell (`1, 2, 3, 4, 7, 8, 13, 16, 32`) must match the plain `graph=off, rows=1` 64-hex `token_sha256` with zero post-warmup recaptures.

## Step 1: Audit between-pass gaps and recaptures

```bash
python3 scripts/dispatch_audit.py audit-gaps notes/decode_steps.jsonl \
  --min-gpu-busy-pct 90 --json notes/dispatch_audit.json
```

Classifies the primary stall (`GRAPH_RECAPTURE_STORM`, `HOST_SYNC_STALL`, `SERIAL_PROPOSER_OR_GRAMMAR`, `ENQUEUE_GAP_STARVATION`, or `DISPATCH_HEALTHY`), computes the zero-stall speedup ceiling, and exits `1` whenever dispatch is stalled.

## Step 2: Apply the matching loop fix

- `GRAPH_RECAPTURE_STORM`: move live row count `M`, draft tree mask and paged KV block table into device-resident indirect buffers.
- `HOST_SYNC_STALL`: move greedy and seeded-sampled acceptance onto device kernels and stream token IDs back asynchronously.
- `SERIAL_PROPOSER_OR_GRAMMAR`: overlap `late_draft` and `grammar_parallel` beside the active verify pass.
- `ENQUEUE_GAP_STARVATION`: capture the decode step into a CUDA graph or Metal indirect command buffer.

## Step 3: Gate forced-width exactness and zero recaptures

```bash
python3 scripts/dispatch_audit.py verify-invariance notes/width_matrix.json \
  --max-post-warmup-recaptures 0 --json notes/invariance.json
```

Exits `0` (`INVARIANCE: PASS`) only when every forced-width and graph-on run matches `reference_token_sha256` bit for bit and logs zero post-warmup recaptures. Then run `sass-and-air-diff`, `output-parity` and `optimization-ledger score-ab`.

## Reference guide

Read `references/zero-stall-loop.md` for device-side acceptance patterns, indirect graph metadata layout, overlapped drafting/grammar pipelines and forced-width testing.
