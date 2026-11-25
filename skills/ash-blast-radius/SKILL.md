---
name: ash-blast-radius
description: Maps every caller, aliased import, transitive dependent, covering test, documentation mention and circular import affected by a symbol, file or git diff before you edit. Ships a zero-dependency AST and multi-language static impact analyzer covering Python, TypeScript, JavaScript, Go, Rust and Zig. Use before changing a function signature, renaming a module, deleting code, refactoring a shared helper, or when asking what depends on a symbol or file.
metadata:
  version: "1.0.1"
  hermes:
    tags: [refactoring, callers, impact, tests]
    related_skills: [repo-map, ship-check]
---

# Blast radius

Changing a function without knowing its callers breaks the one caller nobody remembers: the file that imported it under an alias, the re-export two modules away, or the README showing the old signature. Map the full blast radius before the first edit.

## The one command

```bash
python3 scripts/impact.py --repo . --symbol discount
python3 scripts/impact.py --repo . --files pricing.py
python3 scripts/impact.py --repo . --git-diff HEAD --fail-on-cycles
```

`scripts/impact.py` uses only Python 3.9+ standard library modules. It reads Python ASTs to resolve aliased imports (`from pricing import discount as apply_pct`), builds a reverse import graph to find transitive dependents and circular imports, scans TypeScript/JavaScript/Go/Rust/Zig files for named imports and references, and flags config files and Markdown that mention the target, counting a Markdown mention only when it sits inside backticks.

## Workflow

1. **Run `impact.py` before editing.** Pass the symbol or file you intend to change.
2. **Inspect every aliased caller first.** Aliased imports miss plain text searches for the function name.
3. **Note every covering test file.** If `covering tests` is empty, write a test before changing the code so you have a signal.
4. **Update every site in scope, or keep backward compatibility.** If a caller lives outside your authority or repository, keep the old interface working or flag the breaking change explicitly.
5. **Re-run with `--git-diff HEAD --fail-on-cycles` after editing.** Confirm no unintended circular import entered the graph and no doc mention was left stale.

## Limits

- A Python symbol query starts the dependent search from every module defining that top-level function or class. Use `--files` to narrow the search when several modules define the same name.
- The cycle check follows Python imports at any depth and reports each multi-module cycle touching a target once.
- Static analysis cannot see dynamic dispatch such as `getattr(mod, name)`, reflection, or callers outside the repository. Report that gap when dynamic call sites exist.
- For TypeScript, JavaScript, Go, Rust and Zig, matching is lexical plus import-statement parsing rather than a full compiler type graph, so common short names can include false positives.

## Rules

- Never rely on plain `grep` alone when changing a function's parameters or return type; check aliased imports too.
- Do not skip doc and config mentions when a public call or CLI flag changes. The scanner only counts backticked Markdown mentions, so search prose docs yourself for a public rename.
- Keep unrelated callers untouched unless the contract change actually requires updating them.
