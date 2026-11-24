# Serving concurrency

Find where extra clients stop buying throughput and start adding latency.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're tuning batching, queueing, or concurrency limits for a serving workload.

## Try it

```text
Use serving-concurrency. Audit this burst sweep for real overlap, p99 time to first token, and token parity against solo requests.
```

## What you get

The [burst audit](scripts/concurrency_audit.py) reads request traces and reports timing, throughput, and parity findings.

## Pair it with

[One-stream lanes](../one-stream-lanes/README.md) · [Speedup referee](../speedup-referee/README.md)
