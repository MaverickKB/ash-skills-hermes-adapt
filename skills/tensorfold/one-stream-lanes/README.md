# One-stream lanes

Find out why one request leaves the accelerator idle.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You're tuning speculative decode width or trying to close the gap to concurrent throughput.

## Try it

```text
Use one-stream-lanes. Sweep these draft widths and show which width improves single-stream throughput with the same token hashes.
```

## What you get

The [lane calculator](scripts/lane_math.py) compares single and concurrent throughput, draft acceptance, and token receipts.

## Pair it with

[Serving concurrency](../../../skills/tensorfold/serving-concurrency/README.md) · [Speedup referee](../../../skills/tensorfold/speedup-referee/README.md)
