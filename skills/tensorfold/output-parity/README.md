# Output parity

Compares response text, finish reasons, token counts, and available token bytes across inference builds.

## When to use it

You've changed a kernel, cache path, or speculative decoder and expect identical output.

Compare baseline and candidate on the same prompts, seeds, and temperatures, and inspect the first differing token.

## What's included

`scripts/parity.py` supports live comparisons and replay of recorded responses from chat-completions endpoints.

Open [SKILL.md](SKILL.md) for the full instructions.
