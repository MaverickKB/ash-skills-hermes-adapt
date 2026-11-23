# Prove it

Make a test earn your trust by showing that it fails for the problem you claim to have fixed.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You have a green test and need to know whether it would catch the original bug.

## Try it

```text
Use prove-it. Run the same retry test on the baseline and on my patch. Show why the baseline failed and which cases the patch covers.
```

## What you get

Use [prove_diff.py](scripts/prove_diff.py) for baseline and fault checks, or [diff_oracle.py](scripts/diff_oracle.py) to compare two commands on the same inputs.

## Pair it with

[Break it](../break-it/README.md) · [Ship check](../ship-check/README.md)
