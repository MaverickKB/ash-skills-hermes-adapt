# Prove it

Tests fixes against a baseline and checks whether tests detect a deliberately introduced fault.

## When to use it

You need evidence that a retry fix works or that a refactor preserves behaviour.

Ask the agent to run the same regression test on the old and new code, then report why the old run failed.

## What's included

`scripts/prove_diff.py` runs proof checks in an isolated Git worktree. `scripts/diff_oracle.py` compares two commands over a set of inputs.

Open [SKILL.md](SKILL.md) for the full instructions.
