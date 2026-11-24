# Repo map

Find the files that matter before you open a dozen tabs.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're new to a repository or need to trace a change across several packages.

## Try it

```text
Use repo-map. Where does an upload request enter this codebase, which packages handle it, and what test command should I run?
```

## What you get

The [repo map tool](scripts/repo_map.py) extracts layout, heavily imported files, and commands from project manifests and CI.

## Pair it with

[Ash blast radius](../ash-blast-radius/README.md) · [Spec check](../spec-check/README.md)
