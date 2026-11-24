# Distillation pipeline, verifiers and student gating

How to turn a strong slow teacher into a verified, zero-slop SFT and DPO dataset for fast local student checkpoints without teaching teacher hallucinations or contaminating your eval split.

## Contents

1. Why execution verification beats raw synthetic generation
2. Designing ungameable verifiers per domain
3. Turning multi-sample failures into free DPO pairs
4. Anti-slop, deduplication and decontamination hygiene
5. Training and gating the distilled student

## 1. Why execution verification beats raw synthetic generation

Unverified synthetic datasets fail for one simple reason: even a strong teacher makes silent mistakes on 10-30% of hard coding, tool-calling and reasoning prompts. Fine-tuning a smaller student on unverified outputs bakes the teacher's worst edge-case bugs, subtle syntax errors and filler habits directly into the student's weights.

`foundry.py verify` keeps a trace only when an independent programmatic verifier passes, and proves every verifier can fail via built-in negative controls before judging real data.

## 2. Designing ungameable verifiers per domain

Pick the strongest deterministic check available for each task class:

- **Coding (`python_tests`):** Include boundary cases (empty lists, negative numbers, mutation guards, off-by-one indices) inside `verifier.tests`. Run inside the local Docker sandbox with a short timeout (`--exec-timeout 5`).
- **Structured output and tool schemas (`json_schema`):** Enforce `forbid_fences: true`, exact required keys, exact types and expected ground-truth values.
- **Math, state machines and diagnostics (`exact_answer`):** Compute the exact answer programmatically when generating prompts and require `FINAL_ANSWER: <value>`.
- **Formatting and policy constraints (`regex_rules`):** Combine required output markers with forbidden patterns (placeholder `TODO`s, raw tags, secret patterns).

Never trust a teacher to grade its own unverified free-form answer on tasks that have an executable or mathematical oracle.

## 3. Turning multi-sample failures into free DPO pairs

Sample `N = 4..8` completions per hard prompt from the teacher at moderate temperature (`0.6-0.8`). Prompts where 1-3 samples pass verification and 1-3 samples fail are gold:

- The shortest verified passing completion becomes `chosen`.
- The plausible-looking failing completions (wrong mutation, missed boundary case, leaked JSON fence) become `rejected`.
- `foundry.py verify --out-dpo` emits those preference pairs automatically, aligned on the exact same `prompt_id` so `foundry.py split --dpo` never leaks a preference pair into `val` or `sealed_test`.

## 4. Anti-slop, deduplication and decontamination hygiene

Before training a single optimizer step, run `foundry.py curate`:

1. **Drop truncated finishes:** Any trace ending with `finish_reason == "length"` teaches the student to ramble or cut off mid-patch.
2. **Strip AI slop:** Filter chatbot openers (`Great question!`, `Certainly!`), em-dash spam and stock filler vocabulary so the student inherits sharp engineering voice rather than teacher bloat.
3. **Deduplicate near-clones:** 5-gram Jaccard shingling (`--dedup-threshold 0.80`) removes repeated phrasing that makes small models memorize boilerplate and collapse in diversity.
4. **Decontaminate against sealed evals:** Pass `--eval-prompts` so any synthetic prompt sharing 8-grams with your held-out benchmark is stripped before training.

## 5. Training and gating the distilled student

1. Freeze `splits/manifest.json` and run `foundry.py audit-manifest` before training starts so train/val/sealed-test integrity is cryptographically proven.
2. Run a bounded pilot SFT (or SFT -> DPO) on your local student checkpoint (LoRA or full tune inside an explicit compute budget) and verify validation loss and routing/generation health before scaling up.
3. Evaluate the final distilled student **once** on `splits/sealed_test.jsonl` alongside `quant-autopsy` against three honest baselines:
   - the un-distilled base student (proves distillation actually improved it)
   - the strongest native open model of the same size
   - the teacher (measures exact retention ratio and speed gain)
4. Report honest retention numbers and speedups; if the distilled student loses to a stronger native model of the same size, say so plainly.
