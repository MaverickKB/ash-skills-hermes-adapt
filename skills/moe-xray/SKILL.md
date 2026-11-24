---
name: moe-xray
description: Profiles Mixture-of-Experts routing traces from TensorFold (`--fsr-route-trace`) to expose per-layer activation histograms, normalized routing entropy, K90 hot sets, dead/cold experts and co-activation pairs. Computes GPU-vs-SSD hot-expert residency curves for `--ssd-experts`, builds safe cold-expert pruning manifests, and measures quantization or domain router drift via per-layer Jensen-Shannon divergence (`moe_xray.py`). Use when analyzing MoE expert utilization, planning `--ssd-experts` memory budgets, checking whether quantization broke MoE routing, or identifying dead experts on a local workload.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, moe, routing, expert-residency]
    related_skills: [quant-autopsy, vram-alchemist]
---

# MoE X-ray

Large Mixture-of-Experts checkpoints hide huge efficiency wins inside their routers: on a focused local workload such as coding or technical prose, a compact hot core of experts handles most tokens while a long tail stays nearly cold. `moe-xray` turns raw TensorFold routing traces (`--fsr-route-trace`) into exact residency plans, router-drift audits and pruning manifests.

## Ground rules

1. **Trace representative traffic.** Capture routing traces across real prompt mixes (code, tool calls, reasoning, prose) before ranking hot or dead experts. A single short prompt creates fake dead experts.
2. **Separate specialization from router collapse.** Low entropy can mean healthy domain specialization or a broken low-bit router. Always run `diff` against a `bf16` or 8-bit baseline trace when auditing a quant.
3. **Protect high-entropy layers and `top_k` floors.** Never apply blind uniform expert cuts across layers whose routing entropy is near `1.0`.
4. **Confirm on served silicon.** Treat static hit-rate curves as sizing plans; measure actual served throughput with `tensorfold benchmark` and `prefill-and-kv`.

## Step 1: Capture and profile routing traces

Run your workload against TensorFold with `--fsr-route-trace` enabled, then profile per-layer expert utilization, normalized entropy, `K90` hot sets, dead/cold experts and top co-activated pairs:

```bash
python3 scripts/moe_xray.py profile work/route_trace.jsonl \
  --hot-coverage 0.90 --dead-threshold 0.001 --json work/profile.json
```

Inspect `mean_entropy_norm`, `K90` hot-set fraction and `dead_slot_pct` across layers to see how skewed your workload really is.

## Step 2: Plan GPU vs `--ssd-experts` residency

Feed `work/profile.json` plus your routed-expert VRAM budget and checkpoint `config.json` (or explicit `--expert-size-mib`) into `residency`:

```bash
python3 scripts/moe_xray.py residency work/profile.json \
  --vram-budget-gib 24 --config /path/to/config.json --bpw 4.5 \
  --json work/residency_manifest.json
```

It prints the memory needed for `50%`, `75%`, `85%`, `90%`, `95%` and `99%` static route hit rates, computes exact GPU hit rate vs NVMe miss rate for your budget, and emits `residency_manifest.json` listing GPU-pinned vs SSD-streamed expert IDs per layer.

## Step 3: Check router drift across quants or domains

Compare two routing profiles (for example `bf16` vs `mlx-q4g64` on the exact same prompts, or `code` vs `general_chat`) using per-layer Jensen-Shannon divergence and hot-set Jaccard overlap:

```bash
python3 scripts/moe_xray.py diff work/profile_bf16.json work/profile_q4.json --max-jsd 0.05
```

Exits `0` (`ROUTER DIFF: STABLE`) when every layer stays within `--max-jsd`, or `1` (`ROUTER DIFF: DRIFT_DETECTED`) listing the exact layers where quantization or workload shift moved router decisions.

## Step 4: Generate a domain cold-expert prune plan (optional)

When planning a domain-specialized checkpoint shrink, compute exact per-layer `keep_experts` and `drop_experts` under a strict route-loss cap:

```bash
python3 scripts/moe_xray.py prune-plan work/profile.json \
  --max-route-loss-pct 1.5 --min-keep-per-layer 8 --json work/prune_manifest.json
```

It reports exact routed weight reduction %, mean and worst-layer route mass affected, and warns on any high-entropy layer (`entropy_norm >= 0.92`).

## Reference guide

Read `references/moe-routing-and-residency.md` for interpreting routing entropy, pairing residency curves with `tensorfold serve --ssd-experts`, diagnosing quantization router drift and enforcing safe pruning boundaries.
