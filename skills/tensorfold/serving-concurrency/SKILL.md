---
name: serving-concurrency
description: Designs and audits concurrent LLM serving schedulers, shared-round continuous batching, active lane caps, cached-client queueing, saturation knees and multi-device tensor-parallel scaling while keeping every concurrent reply token-for-token identical to solo execution. Ships a burst-sweep auditor (`concurrency_audit.py`). Use when scaling concurrent streams, diagnosing TTFT tail spikes or queueing behind active decodes, verifying solo-vs-burst parity, or scaling an inference engine across multiple GPUs or hosts.
metadata:
  version: "1.0.0"
---

# Serving concurrency

Concurrent serving fails in two opposite ways: leaving hardware idle behind rigid serial queues, or packing streams together until p99 time to first token collapses or batching arithmetic subtly changes output tokens. Build one shared-round scheduler that mixes prefill chunks, decode rows and drafted windows cleanly, and prove every concurrent stream matches its solo reference hash for hash.

## 1. Shared-round continuous batching and lane caps

- **One scheduler, no serial fallback.** Run prefill chunks, plain decode and drafted decode inside shared rounds governed by a single active lane cap and KV page pool.
- **Offered concurrency vs active lane cap.** Always report both the offered client count (`C=1, 4, 8, 16, 32`) and the active lane cap. When offered concurrency exceeds the cap, measure queue seconds separately from prefill seconds so saturation is visible.
- **Test cached-client queueing explicitly.** Seed a prefix, start a competing request, then submit the cached turn while the competitor is generating. Capture queue seconds, TTFT and total response time; a cache hit that sits behind an unchunked prefill or a maxed lane cap still hurts interactive latency.
- **Separate reasoning TTFT from visible answer TTFT.** For thinking models, report first streamed reasoning token and first visible answer token apart, and label the reasoning mode on every comparison.

## 2. Exact rate and memory definitions

- **Aggregate burst decode rate.** Compute `sum(output_tokens - 1)` divided by `(latest_last_token - earliest_first_token)`. Report per-stream rate separately from aggregate rate.
- **Distinct prompt per stream.** Never benchmark `C >= 2` with identical prompts: identical streams fall into lockstep on L2 and KV caches, producing artificially high rates that swing wildly across repeats.
- **Name every memory metric with units and source.** Disk footprint, process RSS, physical footprint, Metal working-set allowance and CUDA VRAM guard limit are different numbers. Report measured peak beside the active lane cap.
- **Solo-vs-concurrent invariance.** Every concurrent stream must match its solo reference full 64-hex `token_sha256` regardless of batch width, stream ordering or timing.

## 3. Multi-device scaling (when one device is full)

When a workload exceeds one chip's memory or throughput, keep the same exactness contract across ranks:

- **Explicit collective paths.** Isolate tensor-parallel groups, rank scatter/gather and transport backends (NCCL, RoCE verbs, P2P, or host-to-host links) behind one collective interface.
- **Deterministic reduction order.** Fixed all-reduce order keeps multi-rank token hashes reproducible across runs; if floating-point reduction changes once relative to single-rank, treat it as an explicit arithmetic-order decision requiring maintainer approval.
- **Multi-rank receipts.** Record topology, link bandwidth, rank count, binary hash on every rank, and full token hashes for every stream.

## 4. Script: `scripts/concurrency_audit.py`

Stdlib Python 3.9+ auditor for multi-concurrency burst sweeps. Run with `--help` for options.

```bash
python3 scripts/concurrency_audit.py audit bursts.json --max-ttft-p99-s 1.5 --json audit.json
```

Computes exact aggregate and per-stream decode rates, TTFT p50/p95/p99 (plus separate reasoning/answer TTFT when present), locates the saturation knee, rejects any `level >= 2` burst with duplicate prompts (`LOCKSTEP CONTAMINATION`), and fails on any solo-vs-burst `token_sha256` mismatch. Exit codes: `0` on `CONCURRENCY: VALID`, `1` on `CONCURRENCY: FAILED`, `2` on invalid receipt.

Record submission and token times on the same clock. At the claimed level, every request must have been submitted before the earliest last token, so all requests were outstanding together for a positive interval. Queueing counts toward offered concurrency. A burst that falls short fails verification and is excluded from throughput and saturation estimates.
