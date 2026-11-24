---
name: drafter-forge
description: Builds, verifies and gates on-policy companion drafters for TensorFold (`tensorfold serve --drafter`) to accelerate single-stream decode with zero output drift. Checks exact tokenizer and special-token identity (`check-vocab`), harvests on-policy target completions (`harvest`), solves optimal draft depth K* across dense and MoE verification row costs (`analyze-acceptance`), and enforces 64-hex token-hash parity and net speedup gates (`gate-parity`) via `drafter_forge.py`. Use when training, adapting, selecting or validating a speculative draft model for TensorFold.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, speculative-decoding, drafter, vocab-parity]
    related_skills: [one-stream-lanes, output-parity, speedup-referee]
---

# Drafter forge

Generic draft models leave half their acceptance rate on the table because they do not match your target checkpoint's distribution on your codebase, tool schemas and writing style. Build an on-policy companion drafter tailored to your workload, size its draft depth against real dense or MoE verification costs, and prove exact token-for-token parity in TensorFold.

Use `one-stream-lanes` when modifying engine lane schedulers or kernels. Use `drafter-forge` when selecting, harvesting data for, adapting or gating a companion draft checkpoint plugged into `tensorfold serve --drafter`.

## Ground rules

1. **Tokenizer identity first.** Never harvest or train until `check-vocab` confirms the same token ID set and exact token mappings, special tokens (`bos`, `eos`, `pad`), added tokens and BPE merges between target and drafter.
2. **Train on-policy.** Harvest completions from the exact target checkpoint served by TensorFold (`--no-drafts`), never from a different model family.
3. **Price real verification width cost.** Measure `verify_ms` across row widths (`1..K+1`), especially on MoE targets where extra draft rows activate extra experts. Drop any drafter whose optimal depth fails to beat serial decode cleanly.
4. **Exactness is non-negotiable.** Every served reply with `--drafter` must match `--no-drafts` on full 64-hex `token_sha256` digests, greedy and fixed-seed sampled.

## Step 1: Verify target-to-drafter tokenizer compatibility

```bash
python3 scripts/drafter_forge.py check-vocab /path/to/target-checkpoint /path/to/drafter-checkpoint
```

Exits `0` (`VOCAB: COMPATIBLE`) when token ID sets, token mappings, added tokens, special IDs and ranked BPE merges match. Identical maps can still have different padded vocabulary sizes. Exits `1` (`VOCAB: INCOMPATIBLE`) on any silent ID shift or merge mismatch.

## Step 2: Harvest an on-policy workload corpus

Serve the target checkpoint with `tensorfold serve MODEL --no-drafts --host 127.0.0.1 --port 8080`, then harvest on-policy completions across representative coding, diff, tool-call and prose prompts:

```bash
python3 scripts/drafter_forge.py harvest \
  --url http://127.0.0.1:8080/v1 \
  --model my-target \
  --prompts work/prompts.jsonl \
  --out work/on_policy.jsonl \
  --logprobs
```

Train or adapt the companion drafter on `work/on_policy.jsonl` (merging any LoRA back into a clean standalone checkpoint before serving).

## Step 3: Solve optimal draft depth and verify net speedup

Record per-position acceptance (`steps` or `position_probs`), `draft_step_ms` and measured `verify_ms_by_width` on your hardware, then solve `K*`:

```bash
python3 scripts/drafter_forge.py analyze-acceptance work/acceptance.json --json work/depth_plan.json
```

It prints per-depth survival, expected landed tokens, pass duration, net `tok/s` and speedup over serial decode. Exits `0` (`DRAFTER: VIABLE`) when net speedup exceeds 1.03x, or `1` (`DRAFTER: SLOWER_THAN_SERIAL`) when draft latency or MoE row cost erases acceptance gains.

## Step 4: Gate exactness and served throughput

Compare receipts from `tensorfold benchmark MODEL --serial` (`--no-drafts`) and `tensorfold benchmark MODEL --drafter DRAFTER` across prose, code, edits and tool calls (and compare `--drafter-bits 4` vs `--drafter-bits 0`):

```bash
python3 scripts/drafter_forge.py gate-parity work/serial.json work/drafted.json \
  --noise-floor 0.03 --model ORG/TARGET --drafter checkpoints/my-drafter --drafter-bits 4
```

Exits `0` (`DRAFTER GATE: PASS`) only when every cell matches 64-hex `token_sha256`, zero cells regress beyond `--noise-floor`, and geometric-mean speedup clears the noise band. Hand verified wins to `output-parity` and `speedup-referee` in a fresh session.

## Reference guide

Read `references/drafter-craft.md` for on-policy distillation recipes, dense vs MoE verification row-cost curves, `--drafter-bits 0` vs `4` tradeoffs and TensorFold benchmark verification steps.
