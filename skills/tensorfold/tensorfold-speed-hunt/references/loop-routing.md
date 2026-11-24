# Routing matrix and cross-layer combinations

How `tensorfold-speed-hunt` selects the right TensorFold specialist from served trace evidence, combines orthogonal wins across layers, and formats maintainer closing reports.

## Contents

1. Specialist routing from trace evidence
2. Cross-layer combinations that stack
3. Rules for keeping, reverting and reprofiling
4. Maintainer closing message format

## 1. Specialist routing from trace evidence

| Measured trace evidence | Load specialist | Primary mechanism |
| --- | --- | --- |
| `host_share + idle_share >= 20%` or graph recaptures > 1% | `graph-and-dispatch-surgeon` | Device-side acceptance, indirect graph metadata, overlapped `late_draft` / `grammar_parallel` |
| Top kernel `LAUNCH_BOUND` (`< 15 us/launch`) or `UNDERUTILIZED` | `kernel-contract` + `sass-and-air-diff` | Fusion, register/occupancy tuning, paired low-bit unpack, untouched-kernel identity |
| Single-stream `BW_BOUND` decode far below N-stream total | `one-stream-lanes` + `drafter-forge` | Calibrated row-cost width planning, on-policy `--drafter`, Recall/grammar copies |
| Slow TTFT, long-context cliff or KV pool spill | `prefill-and-kv` + `vram-alchemist` | Equal-work chunking, radix prefix reuse, paged KV and host/SSD spill |
| MoE router skew, `--ssd-experts` misses or quant router drift | `moe-xray` + `quant-autopsy` | Hot-expert residency, JSD router checks, Pareto quant selection |
| Concurrent p99 TTFT spike or batch scaling knee | `serving-concurrency` | Shared-round continuous batching, lane caps, cached-client queueing |
| New model family or checkpoint format | `model-bringup` | Safetensors contract and layer-by-layer FP64 parity ladder |

## 2. Cross-layer combinations that stack

Single-kernel wins cap out quickly under Amdahl's law. Look for two-layer combinations whose mechanisms multiply rather than overlap:

- **Single-stream velocity stack:** `drafter-forge` raises `landed/pass` while `graph-and-dispatch-surgeon` removes between-pass CPU syncs to raise `passes/s`. Since `tok/s = landed/pass * passes/s`, both gains multiply directly.
- **Wide-window MoE stack:** `moe-xray` pins hot experts and exposes co-activated pairs while `one-stream-lanes` prices exact per-row MoE expert fan-out so wider draft windows stop slowing passes per second.
- **Zero-recapture KV stack:** `prefill-and-kv` pages KV blocks cleanly while `graph-and-dispatch-surgeon` indirects block tables through device buffers so long prompts replay inside one captured CUDA graph or Metal ICB.

## 3. Rules for keeping, reverting and reprofiling

1. Never land a change that slows any served cell beyond the base-vs-base noise floor in every clean round, even when token hashes are exact.
2. Log every `REVERTED` and `FLAT` experiment into `optimization-ledger` before moving on so future sessions never repeat it.
3. After every verified win clears `check-land`, run `critical-path-xray` again on the new binary: removing the #1 bottleneck immediately shifts what the next #1 bottleneck is.

## 4. Maintainer closing message format

Keep closing messages short and scoped strictly to tested hardware and checkpoints:

- Give tokens per second before and after for each tested cell (with `landed/pass` and `passes/s` for drafted decode).
- State what was kept, what was reverted, the fresh-session `speedup-referee` verdict, and which `OWED_PORT` targets remain.
- Keep milliseconds, bytes and bandwidth numbers inside your notes-folder report, not in the closing summary.
