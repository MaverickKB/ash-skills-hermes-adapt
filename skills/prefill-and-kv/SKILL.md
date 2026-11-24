---
name: prefill-and-kv
description: Pushes cold prompt prefill, warm prefix reuse, paged KV pools, host/SSD spill, hybrid-recurrent chunking and direct-IO checkpoint loading in a native LLM inference engine while keeping cold, chunked and resumed states bit-identical. Ships an equal-work prefill analyzer, paged KV budget calculator and save-point state hash verifier (`prefill_plan.py`). Use when optimizing time to first token (TTFT), prompt chunking, radix prefix caching, KV paging/eviction/spill, SSD expert residency or direct-IO model loading.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, kv-cache, prefill, prefix-reuse]
    related_skills: [model-bringup, serving-concurrency]
---

# Prefill and KV

Prompt processing has no guessing problem: every prompt token is known before the first pass. When one long prompt runs much slower than N short prompts of the same total length, the gap splits cleanly into two pieces: extra quadratic attention work and idle silicon left behind by narrow scheduling. Measure both separately, then push cold prefill, warm prefix caching and KV residency without ever letting resumed state drift from a fresh run.

## 1. Cold prefill: saturate the chip cleanly

- **Skip unconsumed head work.** During prefill, compute the LM head and logits only for the final prompt row (and any speculative root), never for intermediate prompt rows whose logits are discarded.
- **Separate schedule limits from real dependencies.** Chunk size, chunk ordering, single-chunk-in-flight waits and routed-expert padding are scheduling choices; widen and pipeline them freely while keeping output bits identical across chunk widths. True serial dependencies (layer order, recurrent/Mamba/linear-attention state) require exact chunkwise parallel forms in the same arithmetic order.
- **Hold attention work equal in 1-vs-N comparisons.** One 32K prompt attends over 16x more key pairs than sixteen 2K prompts of equal token count. Run `scripts/prefill_plan.py equal-work` so you chase idle silicon rather than missing FLOPs.
- **Measure cold prefill cleanly.** Use unique prompt bodies across lengths (2K, 8K, 32K, 64K) with an empty cache store and normal cache settings. Set output tokens to 1 to isolate prefill and report uncached input tokens divided by prefill seconds (subtracting any restored tokens).

## 2. Warm prefill, radix prefix trees and KV residency

- **Radix prefix store.** Share immutable prefix pages across concurrent requests and multi-turn agent sessions so repeated system prompts, tool schemas and file contexts never re-prefill.
- **Paged KV pool and parked pages.** Allocate KV in fixed token pages (such as 16 or 64 tokens per page) so fragmentation never blocks long contexts, and park idle session pages to host RAM or NVMe SSD instead of recomputing cold prompts.
- **Direct I/O for checkpoint load and SSD residency.** Read checkpoints and spilled pages/experts through direct I/O with read-ahead prefetching so large model loads do not thrash the OS page cache or starve unified memory.
- **Exact save-point parity.** Whenever prefill chunking, prefix restoration, host spill or disk spill changes, export and hash the full logical state at true save points: attention KV, local sliding windows, recurrent state, draft state and final logits. Do not test fresh-vs-resumed with a global cache-off switch if that switch selects a different prefill grid; use an empty store with normal cache settings instead.

## 3. Script: `scripts/prefill_plan.py`

Stdlib Python 3.9+ tool covering equal-work attention math, paged KV capacity/spill sizing and offline state hash verification. Run with `--help` for all flags.

```bash
# Separate quadratic attention work in 1x32768 vs 16x2048 prefill
python3 scripts/prefill_plan.py equal-work --long-len 32768 --short-len 2048 --streams 16 --layers 32 --heads 32 --head-dim 128 --long-s 4.2 --short-s 1.6

# Size paged KV headroom and spill bandwidth for GQA or MLA
python3 scripts/prefill_plan.py kv-budget --layers 64 --kv-heads 8 --head-dim 128 --vram-gib 96 --weights-gib 42 --context 32768 --streams 16

# Verify cold vs chunked/resumed save-point state hashes (full 64-hex SHA-256)
python3 scripts/prefill_plan.py verify-state cold_state.json resumed_state.json
```

`verify-state` exits `0` on `MATCH`, `1` on `MISMATCH`, and `2` on missing or truncated hashes.

`kv-budget` rounds each stream's context up to whole pages and counts how many complete pages fit in VRAM. Reported spill size and restore bandwidth use the number of remaining pages. The command exits `0` when the requested contexts fit and `1` when spill is required or VRAM has no headroom.

## Rules

- Always report restored tokens beside prefill rate; counting cached prefix tokens as cold prefill inflates the number.
- Record physical memory, resident memory and GPU working-set allowance with units whenever changing KV pools, spill tiers or checkpoint I/O.
- Hand verified wins to `speedup-referee` in a fresh session and check concurrent queueing with `serving-concurrency`.
