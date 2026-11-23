# Inference speed hunt

Follow a served request and find which stage has room to get faster.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

Inference is slow and you're deciding where to spend optimisation time.

## Try it

```text
Use inference-speed-hunt. Break down time to first token and decode time on this workload, then benchmark one change while checking output parity.
```

## What you get

Separate prefill, decode, and concurrent throughput so a gain in one stage stays visible alongside regressions in another.

## Pair it with

[Fair bench](../../../skills/fair-bench/README.md) · [Output parity](../../../skills/tensorfold/output-parity/README.md)
