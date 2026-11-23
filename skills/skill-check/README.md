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

## Build local exports

Run the build from the repository root to generate `.skill` and single-file `.md` exports in `dist/`. In a fresh checkout, `--check` reports missing exports until you build them.

```bash
python3 skills/skill-check/scripts/build.py
python3 skills/skill-check/scripts/build.py --check
```

The validator's overlap notes compare description wording. Read the triggers together to catch skills that claim the same task in different words.

## Pair it with

[Spec check](../spec-check/README.md) · [Slopchop](../slopchop/README.md)
