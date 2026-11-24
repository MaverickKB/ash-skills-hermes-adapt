# Distill foundry

Turn multi-sample teacher outputs into execution-verified, zero-slop SFT and DPO training splits with SHA-256 leak audits for fast local student models.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You want to distill a large slow model into a smaller local student or LoRA using verified unit-test, JSON-schema and oracle checks instead of unverified synthetic slop.

## Try it

```text
Use distill-foundry. Initialize a run workspace, run execution verification on our raw teacher traces to build SFT and DPO pairs, strip slop and near-duplicates, and audit the sealed train/test manifest.
```

## Python tests

Start a trusted local Docker daemon and run `docker pull docker.io/library/python:3.12-slim` once. Verification uses the cached image in a non-root container with a read-only root, no host mounts, and no external network. JSON, exact-answer, and regex verification run without Docker.

## What you get

Use the [distillation verification foundry](scripts/foundry.py) with [example multi-sample traces](assets/trace.example.jsonl) and the [distillation pipeline guide](references/distillation-pipeline.md).

## Pair it with

[Quant autopsy](../quant-autopsy/README.md) · [Slopchop](../slopchop/README.md) · [VRAM alchemist](../vram-alchemist/README.md) · [Drafter forge](../drafter-forge/README.md)
