---
name: vram-alchemist
description: Calculates exact VRAM, unified memory and host RAM fit for TensorFold checkpoints before pulling weights, probing local hardware and `tensorfold capabilities --json`, inspecting remote Hugging Face safetensors trees and `config.json`, sizing GQA/MLA/sliding-window KV caches (`--kv-cache bf16|fp8`), planning `--spill-gib`, `--prompt-cache-gib`, `--ssd-experts` and `--parallel`, and emitting ready-to-run `tensorfold serve` and `tensorfold benchmark` commands. Use when picking the best TensorFold quant for a GPU or Apple Silicon Mac, sizing context and KV precision, or planning SSD expert residency before pulling a model.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, vram, quantization, serving-flags]
    related_skills: [model-bringup, prefill-and-kv, quant-autopsy]
---

# VRAM alchemist

Picking a checkpoint quant by guesswork wastes hours on OOM failures, truncated contexts and sluggish unified-memory swapping. Solve the exact TensorFold memory budget from hardware probes, `tensorfold capabilities --json`, and remote Hugging Face metadata before pulling a single gigabyte, then verify throughput with `tensorfold benchmark`.

## Ground rules

1. **Zero-download inspection first.** Read remote `config.json` and safetensors shard trees before running `tensorfold pull`.
2. **Name every memory pool.** Separate physical VRAM, desktop display carveout, Apple Metal GPU working-set cap (~75% of unified RAM), checkpoint weights, KV cache (`bf16` vs `fp8`), prompt cache, and graph overhead. Never size to 100% of physical RAM or nominal VRAM.
3. **Use TensorFold residency paths explicitly.** When KV exceeds headroom, budget `--spill-gib` and `--prompt-cache-disk`. When MoE routed experts exceed VRAM or unified working set, plan `--ssd-experts` while keeping attention, norms, router and KV cache resident on GPU.
4. **Verify fit on silicon.** Analytical fit prevents OOMs; it does not prove throughput. Run the emitted `tensorfold benchmark` command and hand speedup claims to `fair-bench` and `speedup-referee`.

## Step 1: Probe hardware and TensorFold capabilities

Run `scripts/vram_solve.py probe` to capture local GPUs, free VRAM, PCIe links, Apple unified memory and ~75% working set, host RAM, and the installed `tensorfold` binary's version, backends, supported model families and supported quant formats from `tensorfold capabilities --json`:

```bash
python3 scripts/vram_solve.py probe
```

When planning for a remote machine (for example, a 32 GiB CUDA GPU, a `32,32` dual-GPU rig, or a 192 GiB Apple Silicon Mac), pass `--vram-gib` or `--gpus` plus `--ram-gib` and `--backend` explicitly.

## Step 2: Inspect a remote Hugging Face checkpoint before pulling

Use `inspect-repo` on any Hugging Face model ID or URL to fetch `config.json` and the `.safetensors` shard tree without downloading weights, detect its quant format (`bf16`, `modelopt-fp8`, `fp8-block128`, `modelopt-nvfp4`, `mlx-q*g*`), check fit at your target `--context` and `--kv-cache`, and emit `tensorfold pull`, `tensorfold serve` and `tensorfold benchmark` commands:

```bash
python3 scripts/vram_solve.py inspect-repo ORG/MODEL-REPO \
  --context 32768 --streams 1 --kv-cache bf16
```

## Step 3: Solve across all TensorFold quant tiers

Use `solve` from a Hugging Face repo (`--hf-repo`), a local `config.json` (`--config`), a local checkpoint folder (`--checkpoint`), or manual shape flags (`--params-b`). It prices every TensorFold quant tier (`bf16`, `mlx-q8g32/64/128`, `exlr8`, `modelopt-fp8`, `fp8-block128`, `mlx-q6g*`, `mlx-q5g*`, `modelopt-nvfp4`, `mlx-q4g32/64/128`, `mlx-q3g*`, `mlx-q2g*`) alongside KV cache (GQA, MHA, DeepSeek MLA and hybrid sliding-window layers), `--spill-gib`, `--prompt-cache-gib`, `--ssd-experts` and `--parallel`:

```bash
# Plan across all TensorFold quant tiers from a remote config.json
python3 scripts/vram_solve.py solve --hf-repo ORG/MODEL-REPO \
  --vram-gib 32 --ram-gib 64 --backend cuda --context 32768 --kv-cache fp8

# Plan a local checkpoint directory on Apple Silicon unified memory
python3 scripts/vram_solve.py solve --checkpoint /path/to/checkpoint \
  --backend metal --context 65536 --streams 2 --kv-cache bf16
```

Exit codes: `0` at least one quant fits (`FITS_ALL_GPU`, `FITS_WITH_KV_SPILL` or `FITS_SSD_EXPERTS`), `1` `NO_FIT`, `2` usage, fetch or parse error.

## Step 4: Verify speed and quant quality

1. Run the emitted `tensorfold benchmark` command to measure real prefill and decode `tok/s` at your target `--context` and `--kv-cache`, and confirm memory stays inside the GPU or Metal working-set budget while context is full.
2. Do not choose between lower quants (`mlx-q4g32` vs `mlx-q4g64` vs `mlx-q4g128` vs `modelopt-nvfp4` vs `mlx-q3g64`, or `--kv-cache bf16` vs `fp8`) on file size alone: run `quant-autopsy` across the top candidate quants that fit your rig to find the true Pareto winner on tool calling, long context and reasoning.

## Reference tables

Read `references/quant-formats-and-flags.md` for whole-checkpoint effective bpw across TensorFold quant formats, `--kv-cache` and `--prompt-precision` tradeoffs, `--spill-gib` and `--ssd-experts` residency rules, and multi-GPU sizing guidance.
