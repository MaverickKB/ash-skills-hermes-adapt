# Mythos Mode

Get a repro you can run, a result you can verify, and a clear account of what still needs checking.

This may or may not have been created by Mythos to help smaller models think like Mythos.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

When you want to go beast mode on something.

## Try it

```text
Use mythos-mode. This test fails on CI twice a week. Reproduce it, narrow down the cause, and show me a check that fails without the fix.
```

## What you get

Use the playbooks for coding, research, writing, and judgement. The [coding playbook](references/coding.md) covers production changes and links to architecture, TypeScript, dependency research, security, and verification. Use the [TensorFold performance playbook](references/inference-performance.md) when you need to trace a served request and choose the next optimisation.

Run the [reply gate](scripts/gate.py) to catch unsupported claims and missing verification details.

## Pair it with

[Prove it](../prove-it/README.md) · [Diagnosing bugs](../diagnosing-bugs/README.md) · [Control vector forge](../control-vector-forge/README.md)
