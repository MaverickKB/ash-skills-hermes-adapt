# Fair bench

Measure how much the baseline varies before you call the candidate faster.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

A timing improved once and you need to know whether the result holds across reruns.

## Try it

```text
Use fair-bench to compare these two parser commands on the same input. Measure the baseline noise first and give me the raw runs behind your verdict.
```

## What you get

The [benchmark runner](scripts/ab_bench.py) alternates the commands and reports the measured gap and noise floor.

## Pair it with

[Speedup referee](../tensorfold/speedup-referee/README.md) · [TensorFold performance playbook](../tensorfold/README.md)
