# Truffle Hunter

Audit an entire repository line by line for hidden telemetry, phone-home IPs and domains, obfuscated URLs, default-on analytics and sneaky credential or auth-file snooping.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You want to know whether a repo phones home, reads local auth files or sends hashed identifiers to third-party servers before trusting it with your API keys or codebase.

## Try it

```text
Use truffle-hunter. Scan this entire repo, inspect every flagged file and entrypoint line by line, and tell me straight: Zero telemetry is transmitted as far as I can tell, or This part looks sus.
```

## What you get

Use the [forensic telemetry scanner and review gate](scripts/truffle_hunter.py) backed by the [covert telemetry tactics guide](references/telemetry-tactics.md).

## Pair it with

[Red team](../red-team/README.md) · [Repo map](../repo-map/README.md) · [Ship check](../ship-check/README.md) · [Deepfield](../deepfield/README.md)
