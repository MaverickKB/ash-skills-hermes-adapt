# Prefill and KV

Know how many cache pages fit before you find out by spilling to disk.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You're changing prompt chunking, prefix reuse, KV memory limits, or restore behaviour.

## Try it

```text
Use prefill-and-kv. Size the cache for this context length and concurrency, then compare cold and resumed cache state.
```

## What you get

The [prefill planner](scripts/prefill_plan.py) sizes whole pages, estimates spill restore bandwidth, and checks saved-state hashes.

## Pair it with

[Model bring-up](../model-bringup/README.md) · [Serving concurrency](../serving-concurrency/README.md)
