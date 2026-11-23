---
name: repo-map
description: Builds a compact, token-budgeted architecture map of an unfamiliar codebase in one pass, extracting languages, build and test commands from manifests and CI, directory layout, most-imported hub files and top exported symbols with line numbers. Ships a stdlib cartographer (`repo_map.py`). Use when entering a new or large repository, onboarding to an unfamiliar codebase, planning edits across many directories, or orienting cheaply without burning context on blind searches.
metadata:
  version: "1.0.0"
---

# Repo map

Dropping into a large repository and running twenty blind searches fills the context window before real work begins. Generate one compact map first so you see the entry points, build commands, hub modules and public symbols up front.

## The one command

```bash
python3 scripts/repo_map.py --repo . --budget-tokens 1800 --max-lines 160
python3 scripts/repo_map.py --repo . --json map.json
```

`scripts/repo_map.py` uses only the Python 3.9+ standard library and completes in under a second on typical repositories. It extracts:

- language and line-count breakdown across Python, TypeScript, JavaScript, Go, Rust, Zig, Shell and SQL
- build, lint and test commands from `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `build.zig` and `.github/workflows/*.yml`
- top-level directory file counts
- hub files sorted by import in-degree and size, with exported functions, classes, structs and types tagged with `@L<line>`

## Orientation loop

1. **Run `repo_map.py` first** with a token budget sized to your task (`1200` for small tasks, `2500` for wide architecture work).
2. **Read repository rules next** (`README.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`) if present.
3. **Open only the hub files and target modules** the map surfaced, reading the exact line ranges shown.
4. **Stop orienting as soon as you can name the files to change and the check command to run.** For impact before editing a shared symbol, hand off to `blast-radius`.

## Limits

- In-degree ranking counts lexical/AST imports by module stem, which is a fast heuristic rather than a full linker graph.
- Generated or vendored folders outside standard ignore names (`node_modules`, `dist`, `build`, `target`, `.venv`) will appear unless excluded.

## Rules

- Do not dump entire large files into context when the map already tells you the exact symbol line range.
- Verify any command listed in the map before assuming it succeeds in the current environment.
