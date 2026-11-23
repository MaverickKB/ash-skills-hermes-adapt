# Break it

Find the edge case the happy-path test missed.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You want to stress-test a local CLI, a changed line, or an implementation plan before anyone relies on it.

## Try it

```text
Use break-it on this local CSV importer. Try an empty file, broken UTF-8, and a path with spaces. Report the smallest failing input.
```

## What you get

Use the [local CLI fuzzer](scripts/fuzz_cli.py) for input edges or the [diff mutator](scripts/mutate_diff.py) to find changes your tests fail to catch.

## Pair it with

[Prove it](../prove-it/README.md) · [Diagnosing bugs](../diagnosing-bugs/README.md)
