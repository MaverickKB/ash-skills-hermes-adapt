# Output parity

Catch the token that changed after a kernel, cache, or decoding update.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You need to prove that two inference builds preserve their output on the same cases.

## Try it

```text
Use output-parity. Compare both builds on these prompts, temperatures, and seeds, and show me the first token that differs.
```

## What you get

The [parity runner](scripts/parity.py) compares live chat endpoints or replays a baseline recording. It checks raw token bytes when both endpoints report them.

## Pair it with

[Kernel contract](../../../skills/tensorfold/kernel-contract/README.md) · [Speedup referee](../../../skills/tensorfold/speedup-referee/README.md)
