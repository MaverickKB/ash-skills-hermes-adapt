# Model bring-up

Know what a checkpoint contains before you wire it into your inference engine.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You're adding a model family or a checkpoint format and need to verify weights, memory requirements, and outputs.

## Try it

```text
Use model-bringup. Verify the shard index, confirm the tensor layouts, and estimate KV-cache memory before we touch the loader.
```

## What you get

Use the [checkpoint inspector](scripts/inspect_checkpoint.py) for metadata and the [receipt verifier](scripts/verify_receipts.py) for output hashes.

## Pair it with

[Kernel contract](../kernel-contract/README.md) · [Output parity](../output-parity/README.md) · [TensorFold performance playbook](../README.md)
