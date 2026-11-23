# Diagnosing bugs

Find where a failure starts and get a small test that reproduces it.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

A test flakes, a first fix failed, or a regression appeared somewhere between two versions.

## Try it

```text
Use diagnosing-bugs. This parser crashes only on certain inputs. Reduce one failing file, test competing explanations, and find the first bad commit if you can.
```

## What you get

Measure with the [repro rate tool](scripts/repro_rate.py), reduce with the [input shrinker](scripts/shrink_input.py), or narrow history with [auto bisect](scripts/auto_bisect.py).

## Pair it with

[Prove it](../prove-it/README.md) · [Handover](../handover/README.md)
