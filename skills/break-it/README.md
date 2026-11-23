# Break it

Uses code mutations and edge-case inputs to find defects that a test suite or review missed.

## When to use it

You want to stress-test a code change, command-line tool, or implementation plan.

Ask the agent to find which changes on the diff survive the tests, or exercise your CLI with empty input, malformed files, and unusual paths.

## What's included

`scripts/mutate_diff.py` tests mutations on changed lines. `scripts/fuzz_cli.py` exercises a local command using temporary inputs.

Open [SKILL.md](SKILL.md) for the full instructions.
