# Spec check

Finds vague requirements, missing failure cases, and broken links between a spec and its tests.

## When to use it

You want a design or feature proposal that someone can implement and verify.

Turn `make retries more reliable` into a retry limit, a timeout, and a test for what happens when retries run out.

## What's included

`scripts/spec_lint.py` flags ambiguous requirements and checks paths and requirement IDs.

Open [SKILL.md](SKILL.md) for the full instructions.
