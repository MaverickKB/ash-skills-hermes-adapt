---
name: distill-foundry
description: Builds execution-verified, zero-slop synthetic SFT and DPO distillation datasets to train fast small local checkpoints from stronger teacher models. Executes isolated Python unit tests, strict JSON schema checks, exact-answer oracles and regex contracts with built-in negative controls, converts multi-sample pass/fail traces into DPO preference pairs, filters slop and near-duplicates, strips eval contamination via 8-gram matching, and seals prompt-grouped train/val/test splits with SHA-256 provenance (`foundry.py`). Use when distilling a large model into a smaller local student, generating verified fine-tuning or LoRA datasets, building DPO preference pairs, or auditing training splits for leakage.
compatibility: Python 3.9+ for the CLI. Python tests require a local Docker daemon running Linux containers and the official Python 3.12 slim image.
metadata:
  version: "1.0.0"
---

# Distill foundry

Most local fine-tunes and distillations fail at the dataset stage, not the optimizer stage: teacher hallucinations, broken code snippets, near-duplicate boilerplate, chatbot slop and accidental train/test leakage quietly ruin the student checkpoint. `distill-foundry` puts a hard execution-verification, anti-slop and split-integrity pipeline between your teacher model and your local student training run.

## Ground rules

1. **Execute before keeping.** Every synthetic trace must pass a deterministic verifier (`python_tests`, `json_schema`, `exact_answer` or `regex_rules`). Unverified synthetic prose does not enter `verified/sft.jsonl`.
2. **Prove verifiers fail first.** `foundry.py verify` runs relevant negative controls before grading traces. For Python traces it also checks that the sandbox can run a passing case. If a negative control passes, the run aborts (`FOUNDRY: CONTROL_FAILED`).
3. **Turn failures into DPO signal.** Sample multiple teacher trajectories per hard prompt; when one passes verification and another fails, export `(chosen, rejected)` preference pairs automatically.
4. **Zero split leakage.** Split strictly by `prompt_id`, decontaminate against held-out eval prompts via 8-gram overlap, and verify `manifest.json` hashes before training starts.
5. **Run generated Python in a local Docker sandbox.** The runner uses a non-root container with a read-only root, no host mounts, no host environment variables, and no external network access. Python verification stops if the sandbox is unavailable.

## Step 1: Initialize a foundry run workspace

```bash
python3 scripts/foundry.py init \
  --out-dir runs/coder-distill \
  --teacher teacher-70b \
  --student student-8b \
  --domain coding
```

Creates `prompts/`, `raw/`, `verified/`, `splits/`, `reports/` and `foundry.json` outside your repository tree.

## Step 2: Verify raw teacher traces and extract DPO pairs

Prepare raw traces using `assets/trace.example.jsonl`. For `python_tests`, start a trusted local Docker daemon and pull the official image once. Verification uses the cached image.

```bash
docker pull docker.io/library/python:3.12-slim
```

Run the verification gate.

```bash
python3 scripts/foundry.py verify \
  --in runs/coder-distill/raw/traces.jsonl \
  --out-sft runs/coder-distill/verified/sft.jsonl \
  --out-dpo runs/coder-distill/verified/dpo.jsonl \
  --exec-timeout 5.0 \
  --report runs/coder-distill/reports/verify.json
```

Only traces passing execution/oracle verification enter `sft.jsonl`. Prompts with both passing and failing samples produce verified preference pairs in `dpo.jsonl`.

## Step 3: Curate (anti-slop, dedup, decontamination)

Filter truncated finishes (`finish_reason != "stop"`), chatbot openers and stock AI slop, 5-gram Jaccard near-duplicates, and any prompt overlapping a held-out evaluation benchmark (`--eval-prompts`):

```bash
python3 scripts/foundry.py curate \
  --in runs/coder-distill/verified/sft.jsonl \
  --out runs/coder-distill/verified/curated.jsonl \
  --eval-prompts evals/heldout.jsonl \
  --dedup-threshold 0.80 \
  --max-slop-hits 1 \
  --report runs/coder-distill/reports/curate.json
```

## Step 4: Seal prompt-grouped splits and audit provenance

Group strictly by `prompt_id` so multi-sample completions or DPO pairs never span train and test, then audit the resulting manifest:

```bash
python3 scripts/foundry.py split \
  --in runs/coder-distill/verified/curated.jsonl \
  --dpo runs/coder-distill/verified/dpo.jsonl \
  --out-dir runs/coder-distill/splits \
  --val-ratio 0.1 --test-ratio 0.1 --seed 42

python3 scripts/foundry.py audit-manifest runs/coder-distill/splits/manifest.json
```

`audit-manifest` requires a manifest with train, validation and sealed test splits and checks every listed file against its recorded SHA-256. Train and sealed test must contain rows. The audit compares both SFT and DPO training prompts against sealed test prompt IDs and 8-grams.

## Step 5: Train and gate the distilled student

Train the student (LoRA or full SFT, followed optionally by DPO on `dpo_train.jsonl`), then evaluate the frozen student once on `splits/sealed_test.jsonl` and run `quant-autopsy` comparing the distilled student against both its un-distilled base and the teacher.

## Pipeline craft

Read `references/distillation-pipeline.md` for multi-sample harvesting tactics, designing ungameable domain verifiers, SFT-to-DPO staging and honest student baseline gating.
