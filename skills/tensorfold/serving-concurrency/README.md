# Serving concurrency

Audits concurrent request traces for actual overlap, token parity, throughput, and tail latency.

## When to use it

You're tuning batching, queueing, or concurrency limits.

Compare solo and concurrent token hashes and review p99 time to first token across an overlapping request sweep.

## What's included

`scripts/concurrency_audit.py` reads burst traces and reports throughput, latency, and parity findings.

Open [SKILL.md](SKILL.md) for the full instructions.
