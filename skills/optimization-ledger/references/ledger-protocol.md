# TensorFold experiment ledger and mirrored A/B rules

How to keep multi-session local optimization runs cumulative instead of circular, score mirrored benchmarks honestly, and compound verified wins across Metal, CUDA and every model family.

## Contents

1. Why an anti-repeat ledger multiplies agent velocity
2. Built-in known-loss archetypes
3. Mirrored `B, C, C, B` receipt format
4. Compounding wins through `OWED_PORT` debt

## 1. Why an anti-repeat ledger multiplies agent velocity

Every time a fresh coding session starts optimizing TensorFold without checking past losses, it rediscovers the same tempting dead ends, runs biased `B, B, C, C` timings where cold-chip boost flatters the first arm, or forgets to port a proven win from CUDA to Metal. `ledger.py` makes experiment memory executable.

## 2. Built-in known-loss archetypes

`ledger.py preflight` automatically blocks four documented TensorFold losing patterns unless you record a concrete `--changed-assumption`:

- `dispatch_count_only`: merging launches without removing critical-path time.
- `secondary_queue_prefetch`: prefetching next-step weights from a second queue or host CPU, which contends on memory bandwidth.
- `persistent_device_barrier`: persistent kernels spinning on device-wide barriers that stall concurrent waves.
- `single_gpu_tp`: tensor parallelism inside one GPU.

## 3. Mirrored `B, C, C, B` receipt format

`ledger.py score-ab` expects one JSON file with `target_cell` and a `cells` mapping:

```json
{
  "target_cell": "decode_solo_code",
  "cells": {
    "decode_solo_code": {
      "order": ["B", "C", "C", "B"],
      "base_vs_base": [82.1, 83.0, 82.6],
      "rounds": [
        {
          "base_tps": 82.4,
          "cand_tps": 91.2,
          "base_token_sha256": "<64-hex>",
          "cand_token_sha256": "<64-hex>",
          "temp_c": 68,
          "throttled": false
        },
        {
          "base_tps": 82.7,
          "cand_tps": 91.6,
          "base_token_sha256": "<64-hex>",
          "cand_token_sha256": "<64-hex>",
          "temp_c": 70,
          "throttled": false
        }
      ]
    }
  }
}
```

Any round marked `throttled: true` or exceeding `--max-temp-c` is dropped automatically. Any cell where candidate falls below base by more than `noise_floor_pct` in every clean round fails the whole run (`AB: REJECTED_CELL_REGRESSION`).

## 4. Compounding wins through `OWED_PORT` debt

A mechanism proven on one checkpoint (for example +14% on Qwen on CUDA) is rarely family-specific. Record `--owed-ports metal:qwen,cuda:gemma,metal:gemma` whenever you mark an experiment `KEPT`, and run `ledger.py owed` at the start of every new session so high-ROI ports jump straight to the top of the backlog.
