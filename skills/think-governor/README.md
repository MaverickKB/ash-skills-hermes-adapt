# Think governor

Kill overthinking loops, stop blank `max_tokens` truncations, and calibrate per-tier reasoning budgets and `--loop-guard` policies for local thinking models.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

Your local reasoning model burns thousands of `<think>` tokens on trivial questions, loops on hesitation phrases, runs out of tokens before answering, or needs calibrated `--thinking-budget` and `--reasoning-effort` tiers.

## Try it

```text
Use think-governor. Generate a 4-tier calibration ladder, profile our thinking model from no-thinking through budget-4096, scan any looping traces, and output the per-tier TensorFold policy.
```

## What you get

Use the [reasoning budget governor](scripts/think_governor.py) for seeded 4-tier calibration, offline loop autopsies and per-tier policy tuning, backed by the [reasoning budget guide](references/reasoning-budgets.md).

## Pair it with

[Quant autopsy](../tensorfold/quant-autopsy/README.md) · [Local coder overclock](../local-coder-overclock/README.md) · [Serving concurrency](../tensorfold/serving-concurrency/README.md)
