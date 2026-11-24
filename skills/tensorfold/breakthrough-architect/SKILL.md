---
name: breakthrough-architect
description: Discovers 2x-10x first-principles architectural breakthroughs for TensorFold on local silicon (Apple Silicon Metal and desktop CUDA GPUs) before entering the 7-gate landing loop. Computes four fundamental silicon headroom ratios (`physics-gap`), enforces assumption-inversion and 10-minute falsifiable kill-probe cards (`probe-card`), runs exactness- and spill-gated on-silicon parameterized autotuning (`autotune`), and models multiplicative cross-axis speedup stacks (`stack-model`) via `breakthrough_lab.py`. Use when hunting step-function 2x-10x leaps, inverting engine design assumptions, or autotuning chip-specific kernel and lane schedules.
metadata:
  version: "1.0.0"
---

# Breakthrough architect

Incremental +3% kernel tweaks will never leapfrog 100-person datacenter engine teams. `breakthrough-architect` acts as **Stage 0 (R&D Discovery)** above `tensorfold-speed-hunt`: quantify the exact physics gap between current `tok/s` and silicon capability, invert the hidden assumptions big engines take for granted, test each leap with a 10-minute kill-probe, autotune directly on target silicon, and stack orthogonal multipliers cleanly into `tensorfold-speed-hunt`.

## Ground rules

1. **Hunt multipliers, not nibbles.** Require `target_multiplier >= 1.25x` in breakthrough mode; hand smaller local cleanups directly to `tensorfold-speed-hunt`.
2. **Falsify in 10 minutes first.** Never start a multi-day engine rewrite without a validated `probe-card` naming the inverted assumption, isolated kill-probe command, pass threshold and kill threshold.
3. **Multiply only orthogonal axes.** Combine gains strictly across independent axes (`bandwidth_and_layout`, `dram_traffic_fusion`, `zero_host_dispatch`, `multi_token_lanes`, `residency_and_prefix`) and count only the top win inside any single axis.
4. **Never compromise exactness during search.** Pass `--reference-sha256` and `--forbid-spills` to `autotune` so every surviving schedule stays token-for-token exact and spill-free.

## Step 1: Quantify the 4 fundamental silicon gaps

```bash
python3 scripts/breakthrough_lab.py physics-gap \
  --chip rtx5090 --active-weight-gib 14.5 --measured-bytes-gib 21.0 \
  --single-tps 85 --multi-tps 640 --gpu-busy-pct 76 \
  --json notes/physics_gap.json
```

Measures **Bandwidth Efficiency**, **One-Stream Share**, **DRAM Traffic Tax** and **Zero-Host Dispatch Headroom**, ranking which breakthrough class holds the biggest real multiplier on your chip.

## Step 2: Design and judge a 10-minute kill-probe

Write a probe card JSON declaring the assumption inverted, orthogonal axis, target multiplier (`>= 1.25x`), probe time cap (`<= 60` min) and numeric `pass_threshold` / `kill_threshold`, then validate and judge it:

```bash
python3 scripts/breakthrough_lab.py probe-card notes/probe_card.json
# After running the 10-minute probe:
python3 scripts/breakthrough_lab.py probe-card notes/probe_card.json --measured 0.86
```

Exits `0` (`PROBE VERDICT: GRADUATE_TO_SPEED_HUNT`) when the probe clears `pass_threshold`, or `1` (`PROBE VERDICT: KILL_IN_10_MIN`) so dead hypotheses die cheap.

## Step 3: Autotune chip-specific schedules directly on local silicon

Customize `assets/autotune.example.json` for your kernel tiles, simdgroups/warps, unrolls, KV page widths or draft trees, dry-run to inspect the space, then search:

```bash
python3 scripts/breakthrough_lab.py autotune --space notes/space.json --dry-run
python3 scripts/breakthrough_lab.py autotune \
  --space notes/space.json --strategy coord --budget 40 \
  --reference-sha256 <64-hex> --forbid-spills \
  --json notes/autotune_result.json
```

The tuner drops throttled measurements and rejects token-hash drift. With `--forbid-spills`, the evaluator must report an integer `spills` count of zero before a candidate can win.

## Step 4: Model the multiplicative 10x stack and graduate

```bash
python3 scripts/breakthrough_lab.py stack-model notes/roadmap.json --json notes/stack.json
```

Takes only the strongest gain per orthogonal axis and multiplies across independent axes to show the real `Nx` path. Hand every graduated breakthrough directly to `tensorfold-speed-hunt` to clear all 7 landing gates.

## Playbook

Read `references/breakthrough-playbook.md` for the four structural blind spots of big datacenter engines, five assumption inversions for local silicon, kill-probe templates and Stage 0 -> `tensorfold-speed-hunt` handoff rules.
