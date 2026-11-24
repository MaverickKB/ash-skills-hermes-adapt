# MoE routing entropy, SSD residency and router drift

How to read Mixture-of-Experts routing traces captured from TensorFold (`--fsr-route-trace`), plan hot-expert residency for `--ssd-experts`, spot quantization-induced router collapse, and decide when domain expert pruning is safe.

## Contents

1. Reading normalized routing entropy and K90 hot sets
2. Sizing hot-expert residency for `--ssd-experts`
3. Catching quantization router drift with Jensen-Shannon divergence
4. Safe boundaries for domain expert pruning

## 1. Reading normalized routing entropy and K90 hot sets

Normalized Shannon entropy lies in `[0.0, 1.0]`:

- **`0.92 - 1.00` (near-uniform routing).** Every expert carries meaningful traffic. Good for load balance during pretraining, poor for aggressive static pruning; SSD caching still wins if temporal locality across one prompt is high.
- **`0.65 - 0.90` (skewed specialist routing).** Common on domain-specific local workloads (coding-only, English technical prose, structured tool calling). Typically 35% to 65% of experts cover 90% of token routes (`K90`).
- **`< 0.65` (heavy concentration or router collapse).** Either strong domain specialization (tiny hot core) or a broken/quantized router collapsing onto a handful of experts. Always compare against the `bf16` profile using `moe_xray.py diff` before celebrating low entropy.

## 2. Sizing hot-expert residency for `--ssd-experts`

When a large MoE checkpoint exceeds GPU VRAM or the Apple Silicon Metal working-set cap, TensorFold's `--ssd-experts` keeps attention, norms, routers, shared experts and KV cache resident while streaming cold routed experts from NVMe SSD.

`moe_xray.py residency` turns raw routing counts into a concrete memory-versus-hit-rate curve:

- Pinning the hottest `(layer, expert)` slots up to your exact VRAM budget gives the projected static GPU hit rate (`gpu_hit_rate_pct`) and remaining NVMe miss rate (`ssd_miss_rate_pct`).
- Inspect `top_coactivated_pairs` per layer: experts that fire together on the same tokens benefit from contiguous packing and paired prefetching.
- Always pair static residency numbers with measured served decode latency (`tensorfold benchmark`), because real traffic exhibits strong bursty locality within a single file or coding turn that often beats the corpus-wide static hit rate.

## 3. Catching quantization router drift with Jensen-Shannon divergence

One of the sneakiest ways a low-bit quant (`mlx-q3g64`, `mlx-q2g64`) damages an MoE model is **router drift**: tiny perturbations in router gate weights or layer-norm outputs flip top-k expert selections, routing coding tokens to the wrong specialists even while perplexity looks calm.

Run the exact same prompt set through TensorFold at `bf16` (or `mlx-q8g64`) and at your candidate quant with `--fsr-route-trace`, profile both, and run `moe_xray.py diff`:

- **`max_jsd <= 0.02` and `hot_jaccard >= 0.85`:** Router selections are tightly preserved.
- **`max_jsd > 0.05`:** Quantization is materially altering expert routing on the flagged layers. Check those layers' router precision and verify capability retention with `quant-autopsy`.

## 4. Safe boundaries for domain expert pruning

Cold experts (under 0.1% activation share on a 50K+ token domain trace) can be pruned physically to shrink checkpoint footprint for a single-domain local deployment, subject to four strict rules:

1. **Trace enough real tokens.** Never prune from a 500-token toy prompt; capture at least 20K to 100K tokens spanning every task you still care about (code, tests, debugging, tool calls, docs).
2. **Respect high-entropy layers.** Early and late MoE layers often route more uniformly than middle specialist layers. Keep pruning lighter (or zero) on layers with `entropy_norm >= 0.92`.
3. **Never drop below `top_k`.** `prune-plan` enforces a hard floor of at least `max(--min-keep-per-layer, top_k)` experts per layer so router selection stays valid.
4. **Verify after physical removal.** Dropping weights physically requires re-indexing router output logits and gating the pruned child on a sealed eval (`quant-autopsy`) against both the parent and smaller native models.
