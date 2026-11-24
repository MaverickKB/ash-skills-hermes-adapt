# Companion drafter craft for TensorFold

Why on-policy domain drafting outperforms generic draft models, how dense and MoE verification costs govern optimal depth, and how to gate exactness in TensorFold.

## Contents

1. Why tokenizer identity is the first gate
2. On-policy harvesting vs off-policy training data
3. Pricing draft depth on dense vs MoE targets
4. Choosing `--drafter-bits 0` vs `4`
5. Verification protocol in TensorFold

## 1. Why tokenizer identity is the first gate

Speculative verification checks the drafter's proposed token IDs directly against the target model's logits. If even a single special token, chat delimiter, tool-call tag, or BPE merge differs between target and drafter, acceptance collapses or the server rejects the pair outright.

Always run `drafter_forge.py check-vocab TARGET_DIR DRAFTER_DIR` before harvesting data or fine-tuning a drafter. Shared-prefix padding (where a model pads `vocab_size` to a multiple of 64 or 128 without reassigning token IDs) is safe; shifted IDs or mismatched `merges.txt` are hard stops.

## 2. On-policy harvesting vs off-policy training data

Training a small drafter on generic web text or another model's completions teaches the wrong next-token distribution. Speculative acceptance depends purely on matching the **target checkpoint's own token distribution** on your actual workload (your coding conventions, diffs, tool schemas, JSON keys and prose register):

- Collect 1,000 to 10,000 prompts representative of real daily serving traffic (mix code edits, tool calls, reasoning and plain prose so the drafter does not regress novel prose).
- Run `drafter_forge.py harvest` against `tensorfold serve TARGET --no-drafts` to record exact on-policy completions.
- Train the companion drafter (full SFT on a 0.5B-1.5B same-vocab checkpoint, LoRA merged cleanly, or reverse-KL logit distillation when top-K logprobs are available) with a short warm-up and small learning rate so it adapts cleanly without overfitting to one prompt template.

## 3. Pricing draft depth on dense vs MoE targets

Net speculative throughput equals `expected_landed(K) / (K * draft_step_ms + verify_ms(K + 1))`, where `expected_landed(K) = 1 + sum_{j=1..K} prod_{i=1..j} alpha_i`.

- **Dense target models.** Reading target weights dominates `verify_ms`, so verifying 5 to 9 rows costs nearly the same as 1 row. Deeper draft depths (`K = 5..8`) pay off whenever `alpha_k >= 0.45`.
- **Routed-expert MoE targets.** Each extra verify row can route to distinct experts, pulling fresh weight bytes from memory and raising `verify_ms(K + 1)` steeply. Always supply real measured `verify_ms_by_width` values to `analyze-acceptance` so `K*` stops before expert fan-out erases the gain.
- **Keep drafter latency tiny.** A drafter that takes more than ~8-12% of the target's single-step time per proposed token eats most of the theoretical speedup even at 80% acceptance.

## 4. Choosing `--drafter-bits 0` vs `4`

TensorFold exposes `--drafter-bits {0,4}` on `tensorfold serve`:

- **`--drafter-bits 4`:** Cuts drafter weight bandwidth and memory footprint sharply, lowering `draft_step_ms` on memory-bandwidth-bound devices. Often the net winner even if acceptance drops by 1-2 points.
- **`--drafter-bits 0`:** Keeps the drafter at its checkpoint precision. Use when the drafter is already tiny (< 1B params) and compute-bound, or when 4-bit quantization lowers acceptance on strict tool syntax.

Measure both settings on the same held-out workload and pick the higher served `tok/s` with identical token hashes.

## 5. Verification protocol in TensorFold

Never announce a drafter win from isolated acceptance numbers alone:

1. Run `output-parity` comparing `tensorfold serve MODEL --no-drafts` against `tensorfold serve MODEL --drafter DRAFTER` at both `temperature=0` and fixed-seed `temperature=0.8` across prose, code and tool calls.
2. Run `tensorfold benchmark MODEL --serial` vs `tensorfold benchmark MODEL --drafter DRAFTER --tokens 256 --reps 5 --temperatures 1.0,0`.
3. Pass both receipts through `drafter_forge.py gate-parity` (must have zero `token_sha256` mismatches, zero cell regressions, and geomean gain above the measured noise floor), then hand the win to `speedup-referee` in a fresh session.
