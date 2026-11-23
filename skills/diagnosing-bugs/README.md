# Diagnosing bugs

Reproduces failures, reduces failing inputs, and narrows regressions with Git bisection.

## When to use it

A failure is intermittent, a first fix didn't work, or behaviour differs between versions.

Measure how often the test fails, find the smallest reproducer, then compare plausible causes against that evidence.

## What's included

The bundled scripts measure failure rates, shrink input files, and bisect commits.

Open [SKILL.md](SKILL.md) for the full instructions.
