# Spec check

Turn a feature pitch into something you can test.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're reviewing an RFC or writing a requirement with vague words such as fast or reliable.

## Try it

```text
Use spec-check on this retry spec. Give me a maximum retry count, a timeout, and an acceptance check for exhausting the retries.
```

## What you get

The [spec linter](scripts/spec_lint.py) flags vague language, missing failure paths, and requirement IDs with broken or missing references.

## Pair it with

[Mythos coding playbook](../mythos-mode/references/coding.md) · [Prove it](../prove-it/README.md)
