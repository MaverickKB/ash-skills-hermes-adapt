# Reasoning budgets, loop guards and dynamic effort routing

How overthinking loops happen in local reasoning models, why `max_tokens` truncation returns blank replies, and how to pair `--thinking-budget`, `--thinking-reserve` and `--loop-guard` in TensorFold.

## Contents

1. Anatomy of an overthinking loop
2. Preventing empty-answer thinking blowouts
3. Sampler settings that trigger or break loops
4. Designing a 3-tier dynamic effort policy

## 1. Anatomy of an overthinking loop

Reasoning-trained open-weight models often reach the right answer inside the first 200 to 600 thinking tokens, then trigger self-verification habits ("Wait, let me double check...", "Hold on, what if..."). On ambiguous prompts, low-bit quants, or greedy decoding (`temperature=0.0`), those checks cycle over the exact same deduction until the token limit expires.

`think_governor.py scan-trace` detects both failure modes:

- **Exact cycle loops:** An 8-word reasoning sequence repeating 3 or more non-overlapping times. The onset offset shows the exact point where additional tokens stopped helping.
- **Hesitation spirals:** High counts of self-doubt markers (`wait,`, `hold on`, `let me double check`, `on second thought`) across long traces with zero progress.

## 2. Preventing empty-answer thinking blowouts

When a client sends `max_tokens: 2048` and the server allows unbounded reasoning, a model that spends 2,048 tokens inside `<think>` terminates with `finish_reason: "length"` and an empty visible reply.

Fix it with two explicit guardrails in `tensorfold serve`:

- **`--thinking-budget N`:** Hard upper cap on reasoning tokens before closing the thinking phase cleanly.
- **`--thinking-reserve M`:** Guarantees at least `M` tokens remain inside `max_tokens` for the visible answer or tool call (use `512` to `1024` for coding edits so diffs never cut off mid-patch).
- **`--loop-guard` and `--loop-guard-stop`:** Monitors repeating token cycles during decode and halts runaway loops automatically instead of burning minutes of GPU time.

## 3. Sampler settings that trigger or break loops

- **Avoid `temperature=0.0` on heavy reasoning models** unless you specifically need bit-exact parity testing: greedy decoding locks a reasoning model onto a single cyclic trajectory once a hesitation phrase repeats.
- **Start at the model family's calibrated sampling band** (typically `temperature=0.6`, `top_p=0.95`, optional `min_p=0.02-0.05`) when profiling loop rates, then compare against greedy in `think_governor.py profile`.
- **Check the quant if loops spike.** Lower 3-bit and 4-bit quants lose calibration confidence and overthink far more than `bf16` or 8-bit weights. Pair `think-governor` with `quant-autopsy` to separate sampler issues from quant degradation.

## 4. Designing a 3-tier dynamic effort policy

Running one global thinking budget wastes latency on easy work or starves hard debugging. Use `think_governor.py tune` to pick three empirical operating points:

| Workload tier | Typical prompts | Recommended mode |
| --- | --- | --- |
| Fast / mechanical | Formatting, renaming, single-line lookup, JSON extraction | `--no-thinking --loop-guard` |
| Daily coding & edits | Targeted bug fix, writing unit tests, multi-file refactor | `--thinking --reasoning-effort medium --thinking-budget 1024..2048 --thinking-reserve 512 --loop-guard` |
| Deep debugging & design | Concurrency races, math/state proofs, architecture tradeoffs | `--thinking --reasoning-effort high --thinking-budget 4096 --thinking-reserve 1024 --loop-guard --loop-guard-stop` |
