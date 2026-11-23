# Fair bench

Measures baseline noise and compares two commands in alternating order.

## When to use it

You need to tell a real speedup from run-to-run variation.

Benchmark the same workload on both versions and record how much the baseline varies before quoting a percentage.

## What's included

`scripts/ab_bench.py` reports the measured difference, baseline noise, and a verdict.

Open [SKILL.md](SKILL.md) for the full instructions.
