---
name: think-governor
description: Profiles local reasoning models on a seeded 4-tier calibration ladder (`trivial`, `medium`, `hard`, `loop_trap`) to kill overthinking loops, eliminate empty-answer thinking truncations, and solve Pareto-optimal `--reasoning-effort`, `--thinking-budget`, `--thinking-reserve` and `--loop-guard` policies. Ships a stdlib ladder generator, live arm profiler, offline trace loop scanner and policy tuner (`think_governor.py`). Use when a local thinking model burns thousands of tokens on easy tasks, loops in `<think>`, returns blank replies on `max_tokens`, or needs calibrated reasoning budgets.
metadata:
  version: "1.0.0"
  hermes:
    tags: [local-models, reasoning, budgets, loops]
    related_skills: [local-coder-overclock, quant-autopsy, serving-concurrency]
---

# Think governor

Local reasoning models love to spend 6,000 thinking tokens second-guessing `14 + 29`, spiral into `"Wait, let me double-check..."` loops, or exhaust `max_tokens` inside `<think>` and return a blank visible reply. Measure the real thinking-tokens-versus-accuracy curve on your model and quant, then lock in calibrated budgets and loop guards instead of guessing.

## Ground rules

1. **Freeze one calibration ladder.** Generate `ladder.json` once with a fresh `--seed`; `tune` refuses to combine receipts with mismatched `ladder_sha256` hashes.
2. **Count blank blowouts as hard failures.** Any reply that runs out of tokens inside reasoning (`truncated_empty`) or triggers a repetition loop disqualifies that arm for the tier.
3. **Separate reasoning tokens from visible answers.** Always measure both `reasoning_content` (or `<think>` blocks) and final answer tokens, and enforce `--thinking-reserve` so diffs and tool calls never truncate.
4. **Match budget to task difficulty.** Do not run one bloated cap for trivial formatting and hard concurrency debugging alike.

## Step 1: Generate the 4-tier calibration ladder

```bash
python3 scripts/think_governor.py generate --seed 20261123 --per-tier 4 --out runs/ladder.json
```

Builds freshly seeded cases with exact ground truth across four tiers:

- **`trivial`:** One-step arithmetic that should pass cleanly with `--no-thinking` or `<= 64` thinking tokens.
- **`medium`:** Bounded multi-step numeric transforms (`~256-1024` thinking tokens).
- **`hard`:** 9-step stateful register machines requiring structured deduction.
- **`loop_trap`:** Simple ground-truth puzzles wrapped in phrasing that triggers endless hesitation spirals.

## Step 2: Profile effort and budget arms

Run the frozen ladder against each serving configuration you want to test (for example `no-thinking`, `budget-512`, `budget-1536`, `budget-4096` and `unbounded`):

```bash
python3 scripts/think_governor.py profile \
  --ladder runs/ladder.json \
  --url http://127.0.0.1:8080/v1 \
  --model my-thinker \
  --arm budget-1536 \
  --thinking-budget 1536 \
  --serve-flags "--thinking --thinking-budget 1536 --thinking-reserve 512 --loop-guard" \
  --out runs/budget-1536.json
```

Repeat across all candidate arms using the same `runs/ladder.json`.

## Step 3: Autopsy any runaway trace offline

When a user pastes a slow or looping reasoning trace (raw text or saved JSON reply), run `scan-trace` to locate the exact n-gram loop cycle, hesitation count and pre-loop `--thinking-budget` cut point:

```bash
python3 scripts/think_governor.py scan-trace work/stuck_trace.txt
```

Exits `0` (`TRACE: HEALTHY`) or `1` (`TRACE: LOOP_DETECTED`) with a concrete `--thinking-budget` + `--loop-guard` recommendation.

## Step 4: Solve the per-tier effort policy

```bash
python3 scripts/think_governor.py tune --dir runs/ \
  --min-trivial-acc 100 --min-medium-acc 90 --min-hard-acc 85 --min-trap-acc 90 \
  --policy-out runs/policy.json
```

Selects the lowest-token, zero-loop, zero-truncation arm meeting each tier's accuracy gate, reports exact thinking tokens saved versus unbounded thinking, and emits ready-to-run `tensorfold serve` commands and `policy.json`. Exits `0` (`GOVERNOR: POLICY_READY`) only when all four tiers have a qualifying arm. A tier with no receipt evidence is marked `NO_TIER_EVIDENCE` and exits `1` (`GOVERNOR: INCOMPLETE`).

## Reference guide

Read `references/reasoning-budgets.md` for loop anatomy, `--thinking-budget` vs `--thinking-reserve` vs `--loop-guard` rules, sampler loop traps and 3-tier effort routing templates.
