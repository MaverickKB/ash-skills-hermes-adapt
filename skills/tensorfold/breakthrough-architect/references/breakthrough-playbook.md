# First-principles breakthrough playbook for local inference engines

How a lean native Zig/Metal/CUDA engine beats giant datacenter inference teams on local hardware by inverting hidden assumptions, running 10-minute kill-probes, autotuning on real silicon and multiplying orthogonal gains.

## Contents

1. Four blind spots of big datacenter engines
2. Five assumption inversions for local silicon
3. Writing a 10-minute kill-probe before refactoring
4. On-silicon autotuning without regressions
5. Graduating winning probes into tensorfold-speed-hunt

## 1. Four blind spots of big datacenter engines

Large inference teams optimize for `B = 64..512` datacenter clusters on H100/B200 pods. On local hardware (RTX 5090, Apple M5 Max) serving `B = 1..4`:

1. **98% idle FLOPs on serial decode.** One-token decode starves compute units while waiting on DRAM weight reads.
2. **Siloed cross-layer traffic.** Separate kernel, quant and Python-scheduler teams write intermediate activations back to DRAM 4-6 times per layer and pay 300-1,500 us of Python host overhead every token.
3. **Stale generic heuristics.** Fixed tile and warp heuristics tuned for datacenter GPUs leave 20-40% performance uncollected on consumer Blackwell and Apple Silicon Metal cores.
4. **Reactive memory hierarchies.** Big engines ignore local NVMe direct I/O, predictive MoE expert lookahead and cross-session prefix/Recall locality.

## 2. Five assumption inversions for local silicon

| Standard assumption | First-principles inversion | Target axis |
| --- | --- | --- |
| 1 forward pass produces 1 token | Fill every pass with `M` exact-verified tokens from unified multi-proposers (on-policy `--drafter`, Recall grafts, grammar-forced leaps, calibrated trees / block drafting) | `multi_token_lanes` |
| Every op reads and writes DRAM | Fuse norm + quant + projection pipelines in registers/SRAM and keep residual state on chip | `dram_traffic_fusion` |
| CPU inspects tokens after every step | Move greedy and seeded acceptance onto GPU and replay one indirect CUDA graph or Metal ICB across dynamic widths | `zero_host_dispatch` |
| Weights stay in checkpoint file order | Swizzle packed low-bit weights at load time into warp/simdgroup register order and co-pack co-activated MoE experts | `bandwidth_and_layout` |
| Hand-tuned tiles beat search | Autotune tile, warp/simdgroup, unroll and draft parameters directly on the target chip with exactness and zero-spill gates | `bandwidth_and_layout` |

## 3. Writing a 10-minute kill-probe before refactoring

Never spend three days rewriting an engine subsystem on faith. Isolate the single riskiest physical unknown into a probe that finishes in under 15-60 minutes with a pre-declared pass and kill threshold:

```json
{
  "name": "moe_2layer_lookahead_prefetch",
  "assumption_inverted": "SSD expert fetches must wait until the current layer router executes",
  "orthogonal_axis": "residency_and_prefix",
  "target_multiplier": 1.6,
  "max_probe_minutes": 20,
  "kill_probe_cmd": "python3 probes/router_lookahead_recall.py --trace notes/route_trace.jsonl",
  "higher_is_better": true,
  "pass_threshold": 0.80,
  "kill_threshold": 0.55
}
```

If the probe hits `<= 0.55` recall, kill the idea immediately and move to the next breakthrough. If it clears `>= 0.80`, graduate it directly into `tensorfold-speed-hunt`.

## 4. On-silicon autotuning without regressions

`breakthrough_lab.py autotune` turns your local GPU or Mac into a chip-specific schedule synthesizer:

- Always pass `--reference-sha256` and `--forbid-spills` so faster configs that drift a single token or spill registers are discarded automatically.
- Use `--strategy coord` for large 5-8 parameter spaces (tile shapes, simdgroups, unroll factors, draft widths) to converge in 20-50 evaluations instead of thousands.
- Confirm the winning config inside the live served build through `tensorfold-speed-hunt` (including mirrored `B, C, C, B` timings and fresh-session `speedup-referee`).

## 5. Graduating winning probes into tensorfold-speed-hunt

`breakthrough-architect` is **Stage 0 (Discovery)**. Once `physics-gap`, `probe-card` or `autotune` proves a >= 1.25x mechanism is physically real, hand it directly into `tensorfold-speed-hunt` so it passes `critical-path-xray`, `optimization-ledger`, `sass-and-air-diff`, `output-parity`, mirrored `score-ab` and fresh-session `speedup-referee` before landing.
