---
name: quant-autopsy
description: Runs an uncontaminated, procedurally generated stress suite across all TensorFold quants of a checkpoint (`bf16`, `modelopt-fp8`, `fp8-block128`, `exlr8`, `modelopt-nvfp4`, `mlx-q8` down through `mlx-q2` group sizes, plus `--kv-cache bf16|fp8` and `--prompt-precision` arms) to measure exactly how each quant performs on tool calling, strict JSON, multi-step state reasoning, code traces, effective context cliffs and sycophancy/slop. Ships a stdlib generator, all-quant `tensorfold serve` sweep runner and 3D Pareto frontier Markdown/HTML reporter (`quant_autopsy.py`). Use when comparing all quants of a model, checking whether a quant broke tool calling or long context, or picking the best quant on the quality-speed-memory frontier.
metadata:
  version: "1.0.0"
---

# Quant autopsy

Published benchmarks are contaminated, and perplexity stays deceptively smooth right up to the point where a quant silently breaks nested tool calls, collapses at 16K context, or caves to user pushback. `quant-autopsy` freezes one freshly seeded procedural suite and sweeps every TensorFold quant of a checkpoint against it so you see the exact quality, speed and memory curve across all quants.

Use `output-parity` when a change must keep output token-for-token identical. Use `quant-autopsy` when outputs are allowed to differ (comparing quants, group sizes, `--kv-cache bf16|fp8`, or `--prompt-precision`) and you need a hard capability matrix.

## Ground rules

1. **Freeze the suite first.** Generate `suite.json` once with a fresh `--seed` and record its `suite_sha256`. `report` refuses to mix receipts from different suites.
2. **Prove the graders can fail.** Every `run` and `generate` executes built-in negative controls (leaked `<tool_call>` text, fenced JSON, wrong state-machine integer, sycophantic flip, slop spam) before scoring any quant. If a control passes, the run aborts (`AUTOPSY: CONTROL_FAILED`).
3. **Hold non-quant variables fixed.** Keep temperature (`0.0`), seed (`0`), max tokens, chat template, `--no-drafts` (or the same draft setting) and `--context` identical across every arm, except when explicitly testing a `--kv-cache fp8` or `--prompt-precision int8` arm.
4. **Report cliffs beside averages.** Never hide a broken capability inside an overall score. Always report the per-capability collapse breakpoints and Effective Context Cliff.

## Step 1: Generate a frozen procedural suite

```bash
python3 scripts/quant_autopsy.py generate \
  --seed 20261123 \
  --contexts 4096,16384,32768 \
  --per-category 6 \
  --out runs/suite.json
```

This creates freshly seeded, programmatically graded cases across six stress tracks:

- **`tool_call`:** Strict nested single call, parallel multi-tool call in one turn, and multi-turn error recovery (`role: "tool"` failure -> retry with fixed parameter). Detects raw XML/tag leaks into `content`.
- **`json_schema`:** Raw JSON object with filtering, computed subtotals, deterministic sorting, enum banding and zero markdown-fence leakage.
- **`reasoning`:** 10-to-13-step procedural register state machine with modular arithmetic, branches, swaps and XORs.
- **`code_logic`:** Freshly parameterized Python execution trace with modulus/sign branches and index arithmetic.
- **`context_cliff`:** Multi-needle synthesis across each length in `--contexts`, with `REVOKED_IGNORE` distractors at 47% and 91% depth to pinpoint the exact Effective Context Cliff.
- **`slop_sycophancy`:** False-pushback sycophancy traps plus cliché density and benign-prompt false-refusal checks.

## Step 2: Run all quants (automated TensorFold sweep or one-by-one)

### Option A: Automated all-quant sweep (sequential `tensorfold serve` start/stop)

Copy and edit `assets/sweep.example.json` to list every quant you want to compare (`bf16`, `modelopt-fp8`, `mlx-q8g64`, `mlx-q6g64`, `mlx-q5g64`, `modelopt-nvfp4`, `mlx-q4g32`, `mlx-q4g64`, `mlx-q4g64+kv-fp8`, `mlx-q4g128`, `mlx-q3g64`, `mlx-q2g64`). Dry-run first to confirm launch commands, then run the full sweep:

```bash
python3 scripts/quant_autopsy.py sweep --suite runs/suite.json --manifest sweep.json --out-dir runs/ --dry-run
python3 scripts/quant_autopsy.py sweep --suite runs/suite.json --manifest sweep.json --out-dir runs/ --logprobs
```

`sweep` launches `tensorfold serve` for each quant, waits for `/v1/models`, sends one unscored warm-up request, runs the frozen suite, writes `runs/<quant>.json`, shuts the server down cleanly, and generates the combined all-quant Markdown, JSON and HTML Pareto reports.

### Option B: Run quants individually (separate hosts or manual swaps)

```bash
python3 scripts/quant_autopsy.py run \
  --suite runs/suite.json \
  --url http://127.0.0.1:8080/v1 \
  --model my-model \
  --quant mlx-q4g64 \
  --quant-family mlx \
  --bpw 4.50 --size-gib 16.9 --vram-gib 20.6 --kv-quant bf16 --engine tensorfold \
  --logprobs \
  --out runs/mlx-q4g64.json
```

Repeat for every quant you want in the matrix, pointing at the same `runs/suite.json`.

## Step 3: Build the all-quant matrix and Pareto frontier

```bash
python3 scripts/quant_autopsy.py report \
  --dir runs/ \
  --baseline bf16 \
  --md runs/all-quants-report.md \
  --html runs/all-quants-report.html \
  --json runs/all-quants-summary.json
```

The report prints:

- Side-by-side table of every quant: Memory GiB, median decode `tok/s`, overall score, Retention % vs baseline, Tool %, JSON %, Reasoning %, Code %, Effective Context Cliff, Sycophancy Flip %, Verdict (`BASELINE`, `LOSSLESS_TIER`, `SWEET_SPOT`, `DEGRADED`, `COOKED`) and Pareto status (`PARETO` vs `dom by <quant>`).
- Per-capability collapse breakpoints showing the exact quant where each skill drops >= 10 points below baseline.
- Standalone HTML dashboard (`--html`) with an embedded SVG Pareto scatter plot (Memory GiB vs Quality Retention, labeled with `tok/s`).

Exit codes: `0` clean run/report, `1` negative-control failure, `2` usage, schema mismatch (`suite_sha256` mismatch) or endpoint error.

## Quant anatomy and interpretation

Read `references/quant-failure-modes.md` for why tool calls and long context break before prose, how TensorFold bit widths and group sizes (`g32` vs `g64` vs `g128`, `fp8`, `nvfp4`, `exlr8`) degrade, and how to test `--kv-cache fp8` and `--prompt-precision int8` arms alongside weight quants.
