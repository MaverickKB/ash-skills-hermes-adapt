# Model bring-up

Inspects checkpoint shards, estimates KV-cache requirements, and checks output parity during model bring-up.

## When to use it

You're adding an open-weight model or checkpoint format to an inference runtime.

Verify tensor names, shard completeness, and memory requirements before implementing loaders or fused kernels.

## What's included

`scripts/inspect_checkpoint.py` reads checkpoint metadata. `scripts/verify_receipts.py` compares recorded output hashes.

Open [SKILL.md](SKILL.md) for the full instructions.
