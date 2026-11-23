# Skill check

Check whether a skill loads, packages cleanly, and improves agent behaviour on real tasks.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're writing a skill or getting one ready for others to install.

## Try it

```text
Use skill-check. Validate the paths and metadata, package the skill, and compare agent results on the same held-out cases with and without it.
```

## What you get

Use the [validator](scripts/validate.py), [packager](scripts/build.py), and [evaluation runner](scripts/run_evals.py).

## Pair it with

[Spec check](../../skills/spec-check/README.md) · [Slopchop](../../skills/slopchop/README.md)
