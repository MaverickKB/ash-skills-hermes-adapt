# Quant failure modes and reading the all-quant matrix

How quantization degrades real TensorFold workloads across bit widths, group sizes and KV precisions, which capabilities collapse first, and how to read a Pareto frontier honestly.

## Contents

1. Why perplexity hides real failures
2. What breaks first as bits drop
3. Format-by-format behavior across TensorFold quants
4. KV-cache and prompt precision interaction
5. Reading the all-quant Pareto frontier

## 1. Why perplexity hides real failures

WikiText-2 perplexity averages log-likelihood across thousands of common English tokens. A 3-bit checkpoint can show only +0.15 perplexity while failing 40% of nested tool calls, dropping parallel tool invocations, or collapsing on multi-needle retrieval at 16K tokens. Perplexity stays smooth while strict discrete behaviors (JSON closing braces, MoE router selection, rare identifier copies, exact integer carries) fall off a cliff.

`quant-autopsy` tests those sharp failure cliffs directly with freshly seeded procedural inputs so no quant gets credit for memorized benchmark questions.

## 2. What breaks first as bits drop

Across most dense and MoE families, capabilities fail in a repeatable order:

1. **Multi-turn tool-call recovery and parallel tool calling.** Often the first thing to break below ~4.5 bpw or at coarse group sizes (`g128`). Models emit raw `<tool_call>` text in `content`, drop the second parallel call, or repeat a failed argument instead of reading the error payload.
2. **Effective Context Cliff.** Long-context attention weights and K/V projections accumulate dot-product errors. A model rated for 128K can stay clean through 64K at `bf16` and `mlx-q8g64`, slip to 32K at `mlx-q4g64`, and fail distractor rejection at 8K on `mlx-q3g64` or `--kv-cache fp8`.
3. **Exact multi-step state tracking.** Procedural register and Python execution traces expose carry errors and skipped branches quickly when FFN down-projections lose precision.
4. **Sycophancy resistance and loop discipline.** Heavily quantized weights lose calibration confidence: under user pushback they cave to wrong answers far more often than `bf16` or 8-bit arms.
5. **General prose fluency.** Usually the last thing to fail, which is why casual chat vibes almost always overestimate low quants.

## 3. Format-by-format behavior across TensorFold quants

Compare every supported quant in the same `quant-autopsy` report (every receipt binds to the same `suite_sha256`):

- **`bf16`:** Ground-truth reference arm. Always include it when memory allows; otherwise declare `modelopt-fp8`, `exlr8` or `mlx-q8g64` as the local reference and state that limit explicitly.
- **`modelopt-fp8`, `fp8-block128`, `exlr8`, `mlx-q8g32/64/128`:** Usually `LOSSLESS_TIER` (98-100% retention). `modelopt-fp8` and `fp8-block128` unlock native FP8 speed on CUDA; `mlx-q8g64` is the clean 8-bit ceiling on Apple Silicon.
- **`mlx-q6g64` / `mlx-q6g128`:** Almost always indistinguishable from 8-bit on tool calls and long context while cutting weight memory by ~23%.
- **`mlx-q5g64` / `mlx-q4g32`:** High-retention sweet spot when 6-bit misses device memory. Tighter group size (`g32`) frequently rescues strict JSON and coding accuracy over `g64` or `g128`.
- **`modelopt-nvfp4` / `mlx-q4g64` / `mlx-q4g128`:** The core 4-bit frontier. Behavior varies sharply by checkpoint architecture, group size and calibration; always test tool calls and effective context before declaring a winner.
- **`mlx-q3g32/64/128` and `mlx-q2g32/64/128`:** Dense models below ~14B degrade sharply here; large MoE checkpoints sometimes survive 3-bit routed experts only when attention, routers and norms stay high precision.

## 4. KV-cache and prompt precision interaction

Treat TensorFold's `--kv-cache fp8` and `--prompt-precision int8` as separate arms in the all-quant matrix (for example `mlx-q4g64` with `--kv-cache bf16` vs `mlx-q4g64+kv-fp8` with `--kv-cache fp8`). Compare Effective Context Cliff and tool-calling retention side by side before switching a daily serving setup away from `bf16`.

## 5. Reading the all-quant Pareto frontier

A quant sits on the **3D Pareto frontier** if no other tested quant simultaneously beats or ties it on Quality Retention, Decode `tok/s`, and Memory/Size GiB (with at least one strict win).

- **Strictly dominated quants** (`dom by ...`) are never the right checkpoint for your machine: another tested quant is both more accurate and faster (or smaller).
- **Check the capability cliff list, not just overall %.** A quant with 93% overall retention that drops from 100% to 50% on `tool_call` is unsuitable for coding agents even if it looks fine in the aggregate column.
- **Keep suite size honest.** The default 4 cases per category (`~20` total cases) is a fast screen across 8-12 quants. Before publishing an all-quant scorecard, raise `--per-category 10` and include your real target context lengths (`--contexts 4096,16384,32768,65536`).
