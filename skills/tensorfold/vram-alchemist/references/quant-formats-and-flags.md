# TensorFold quant formats, KV budgets and serving flags

Reference tables for `vram-alchemist` when selecting native TensorFold checkpoint quants, KV-cache precision, host spill, SSD expert residency and multi-GPU flags.

## Contents

1. Effective bits per weight across TensorFold quant formats
2. KV-cache precision and prompt precision tradeoffs
3. Spill and SSD-resident experts for oversized checkpoints
4. Multi-GPU and unified-memory sizing rules
5. Serving and benchmarking with TensorFold

## 1. Effective bits per weight across TensorFold quant formats

Run `tensorfold capabilities --json` to confirm the exact quant formats your installed TensorFold build supports for each model family. Effective whole-checkpoint bits per weight are slightly higher than the nominal bit width because norms, routers, embeddings, `lm_head` and group scales stay at higher precision.

| TensorFold quant | Effective bpw | Precision tier | Notes |
| --- | --- | --- | --- |
| `bf16` | 16.00 | Reference | Unquantized baseline for parity and all-quant audits |
| `mlx-q8g32` / `mlx-q8g64` / `mlx-q8g128` | 8.25 - 9.00 | Lossless tier | Group-quantized 8-bit; tightest group (`g32`) holds highest accuracy |
| `exlr8` | 8.25 | Lossless tier | Native 8-bit format for supported MoE/DSA checkpoints |
| `modelopt-fp8` / `fp8-block128` | 8.20 | Lossless tier | Native FP8 fast path on Ada, Hopper and Blackwell CUDA GPUs |
| `mlx-q6g64` / `mlx-q6g128` | 6.25 - 6.50 | Near-lossless | Preserves tool calling and long context at ~40% of BF16 weight size |
| `mlx-q5g64` / `mlx-q5g128` | 5.25 - 5.50 | Sweet spot | Strong quality floor when 6-bit misses VRAM or working set |
| `modelopt-nvfp4` | 4.55 | Sweet spot | Native Blackwell FP4 microscaling fast path on CUDA |
| `mlx-q4g32` / `mlx-q4g64` / `mlx-q4g128` | 4.25 - 5.00 | Sweet spot / compact | Core 4-bit family; audit `g32` vs `g64` vs `g128` in `quant-autopsy` |
| `mlx-q3g64` / `mlx-q3g128` | 3.25 - 3.50 | Tight | Noticeable risk on strict tool schemas and multi-needle context |
| `mlx-q2g64` / `mlx-q2g128` | 2.25 - 2.50 | Last resort | High risk of loops, schema corruption and context collapse |

## 2. KV-cache precision and prompt precision tradeoffs

- **`--kv-cache bf16` (2.0 B/elem):** Exact default. Keep `bf16` whenever device headroom fits your target `--context` and `--max-inflight`.
- **`--kv-cache fp8` (1.0 B/elem):** Halves KV-cache memory so you can double context or active streams on tight GPUs. Verify long-context retrieval with `quant-autopsy` before making it permanent.
- **`--prompt-precision bf16|int8`:** Controls prefill compute precision on supported families. Keep `bf16` for exactness baselines and check output behavior before switching to `int8`.
- **MLA and hybrid sliding-window layers:** DeepSeek MLA compresses KV to `kv_lora_rank + qk_rope_head_dim` per layer, and sliding-window layers cap KV growth at `sliding_window` tokens. Only full-attention layers scale linearly with the full context length.

## 3. Spill and SSD-resident experts for oversized checkpoints

When a checkpoint or multi-stream KV cache exceeds GPU VRAM or the Metal working-set cap, TensorFold provides two explicit residency paths instead of blind OS swapping:

- **Host RAM KV spill and prompt cache (`--spill-gib`, `--prompt-cache-gib`, `--prompt-cache-disk`):** Keep active weights and running streams in VRAM while parking idle session KV pages in host RAM or on NVMe SSD so multi-turn coding sessions avoid cold re-prefills.
- **SSD expert and PLE residency (`--ssd-experts`, `--ple-on-ssd`):** For large MoE checkpoints whose routed experts exceed VRAM or unified working set, keep attention, norms, routers, shared experts and KV cache resident on GPU while streaming routed experts directly from NVMe SSD. Measure actual latency on your workload rather than assuming disk throughput matches RAM.

## 4. Multi-GPU and unified-memory sizing rules

- **Desktop display carveout.** On CUDA machines driving a desktop display, reserve `--display-carveout-gib 1.0` (or inspect real free VRAM via `nvidia-smi`) before sizing `--context` and `--prompt-cache-gib`.
- **Apple Silicon unified memory.** Metal enforces a GPU working-set cap near 75% of physical RAM (`vram_solve.py probe` reports both total RAM and the ~75% working-set budget). Never fill to 95% of physical RAM, or macOS swap and compositor pressure collapse decode throughput.
- **Multi-GPU scaling (`--parallel N`):** Split weights across GPUs with `--parallel` and verify solo-vs-concurrent exactness with `serving-concurrency`.

## 5. Serving and benchmarking with TensorFold

Always validate analytical memory fit on silicon before settling on a quant and context budget:

```bash
tensorfold pull ORG/REPO
tensorfold serve ORG/REPO --context 32768 --kv-cache bf16 --max-inflight 1 --prompt-cache-gib 8 --host 127.0.0.1 --port 8080
tensorfold benchmark ORG/REPO --context 32768 --kv-cache bf16 --tokens 256 --reps 5 --temperatures 1.0,0
```

Verify output parity across cache or draft settings with `output-parity`, measure multi-quant quality cliffs with `quant-autopsy`, and verify claimed speedups with `fair-bench` and `speedup-referee`.
