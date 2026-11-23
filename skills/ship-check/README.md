# Ship check

Audits a Git diff for credential patterns, debug code, broken Python callers, and failed checks.

## When to use it

You want a pre-commit or pre-merge check of the code that will actually ship.

Run the audit on staged changes with your project's test command to check the commit candidate in a temporary checkout.

## What's included

`scripts/diff_audit.py` produces a `SHIP` or `HOLD` verdict with the findings that led to it.

Open [SKILL.md](SKILL.md) for the full instructions.
