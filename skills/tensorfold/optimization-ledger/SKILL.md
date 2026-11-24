---
name: optimization-ledger
description: Tracks TensorFold performance experiments in a structured `optimization-ledger.jsonl`, blocks re-running documented dead-end mechanisms without a named assumption change (`preflight`), enforces mirrored `B, C, C, B` A/B scoring with base-vs-base noise floors, clock/thermal throttling drops and 64-hex token-hash checks (`score-ab`), and manages cross-backend and cross-family `OWED_PORT` debt (`owed`, `mark-ported`) via `ledger.py`. Use before starting a TensorFold optimization experiment, when scoring mirrored A/B runs, or when planning cross-family ports of kept speedups.
metadata:
  version: "1.0.0"
---

# Optimization ledger

Fresh optimization sessions waste half their budget re-trying ideas that already lost last week, trusting biased run orders, or forgetting to port proven speedups to sibling backends and model families. Put every TensorFold experiment through a single executable ledger so gains compound instead of oscillating.

## Ground rules

1. **Preflight before coding.** Run `ledger.py preflight` on the candidate mechanism before editing code. Never retry a recorded loss unless you write down what hardware, architecture or runtime assumption changed since.
2. **Enforce mirrored order and noise floors.** Only accept gains from `order: ["B", "C", "C", "B"]` runs where every clean unthrottled round beats the base-vs-base noise floor, every reply matches full 64-hex `token_sha256`, and zero served cells regress.
3. **Log losses as carefully as wins.** Record every `REVERTED` and `FLAT` run in `ledger.jsonl` so future sessions skip dead ends immediately.
4. **Port every proven win.** Attach `--owed-ports` to every `KEPT` entry listing every sibling backend and model family where the mechanism fits.

## Step 1: Run preflight before writing code

```bash
python3 scripts/ledger.py preflight \
  --ledger notes/optimization-ledger.jsonl \
  --mechanism paired_fp4_qmm \
  --backend cuda --family qwen3_5
```

Exits `0` (`LEDGER: PREFLIGHT_OK`) when clear, or `1` (`LEDGER: BLOCKED_REPEAT`) if a prior loss or built-in known-loss archetype matches without `--changed-assumption`.

## Step 2: Score mirrored `B, C, C, B` benchmark receipts

```bash
python3 scripts/ledger.py score-ab notes/ab_receipt.json --max-temp-c 85 --json notes/ab_verdict.json
```

Verifies mirrored order, computes exact `base_vs_base` noise spread, drops throttled rounds, checks full 64-hex `token_sha256` equality, rejects any regressed served cell, and exits `0` only on `AB: CONFIRMED_GAIN`.

## Step 3: Record outcomes and work the `OWED_PORT` queue

```bash
python3 scripts/ledger.py record \
  --ledger notes/optimization-ledger.jsonl \
  --mechanism paired_fp4_qmm --backend cuda --family qwen3_5 --hardware rtx5090 \
  --predicted-gain-pct 12.0 --measured-gain-pct 14.3 --noise-pct 1.1 \
  --status KEPT --owed-ports metal:qwen3_5,cuda:gemma4,metal:gemma4 \
  --notes "register-unpack paired fp4 fused qmm"

python3 scripts/ledger.py owed --ledger notes/optimization-ledger.jsonl
```

When a sibling port lands and passes its own gates, clear it with `ledger.py mark-ported --ledger ... --id exp-0001 --target metal:qwen3_5`.

## Reference guide

Read `references/ledger-protocol.md` for built-in dead-end archetypes, the mirrored A/B receipt schema and cross-family porting discipline.
