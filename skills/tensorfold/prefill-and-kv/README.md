# Prefill and KV

Calculates prefill work, sizes cache residency in whole pages, and checks saved cache state.

## When to use it

You're tuning prompt chunking, prefix reuse, or KV-cache memory and spill behaviour.

Estimate how many KV pages fit in memory, then compare saved state after cold and resumed prefill.

## What's included

`scripts/prefill_plan.py` compares prompt workloads, sizes cache pages, and verifies saved-state hashes.

Open [SKILL.md](SKILL.md) for the full instructions.
