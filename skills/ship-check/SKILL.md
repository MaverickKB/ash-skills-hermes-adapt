---
name: ship-check
description: Runs a pre-merge pull-request audit over a git diff to decide SHIP or HOLD before a human reviewer sees it. Ships a stdlib diff auditor (`diff_audit.py`) that catches leaked secrets, hardcoded home paths, conflict markers, debug leftovers, oversized file growth, and Python signature changes with untouched external callers. Use right before committing, opening a pull request, merging a branch, or whenever asked if a branch or diff is ready to ship.
metadata:
  version: "1.0.0"
  hermes:
    tags: [verification, git, staged-changes, release]
    related_skills: [ash-blast-radius, prove-it]
---

# Ship check

Catch the embarrassing misses before a teammate opens your pull request: a leaked token, a forgotten `breakpoint()`, a merge marker, a changed function signature whose second caller was missed, or a test suite that was not rerun after the last edit.

## The one command

```bash
python3 scripts/diff_audit.py --repo . --base main --cmd "pytest -q"
python3 scripts/diff_audit.py --repo . --staged --cmd "pytest -q"
python3 scripts/diff_audit.py --repo . --base HEAD --strict --json audit.json
```

`scripts/diff_audit.py` audits the Git index whenever changes are staged. It captures the index as a tree and runs the diff, caller checks and `--cmd` in a temporary checkout of that exact tree. The report identifies the tree by its Git hash. The check command needs to work in a clean checkout with dependencies available on the system or installed by the command.

With an empty staging area the default is the working tree, including non-ignored untracked files. Use `--staged` for a pre-commit check or `--worktree` to audit local edits alongside staged changes. Both modes compare against `--base`, which defaults to `HEAD`.

### What it flags

For top-level public Python functions, the auditor checks the argument names and counts at unchanged call sites against both signatures, including required keyword-only parameters. Calls with unpacked arguments can require manual review because their values are known only at runtime.

| Severity | Checks |
|---|---|
| Blocker (`HOLD`) | AWS/GitHub tokens, private keys, hardcoded non-placeholder secrets, absolute home paths, merge conflict markers, `breakpoint()`, `pdb.set_trace`, `debugger;`, `dbg!()`, `TMPDBG` markers, changed Python function signatures with untouched external callers, or a failing `--cmd` |
| Warning (`HOLD` with `--strict`) | New `TODO`/`FIXME`/`HACK` comments, `console.log(` in non-test JS/TS, or non-test code files past 350 lines |

Exit codes: `0` on `VERDICT: SHIP`, `1` on `VERDICT: HOLD`, `2` on usage or git error.

## Full pre-PR sequence

1. Run `scripts/diff_audit.py --base <target-branch> --cmd "<test-cmd>"`. Fix every blocker.
2. Read the entire `git diff <target-branch>` line by line. Revert any line that does not trace to the task.
3. For behavior changes or refactors that matter, run `prove-it` (`prove_diff.py`) to confirm the main check can fail and `break-it` (`mutate_diff.py`) to expose surviving mutants on changed lines.
4. Report **SHIP** or **HOLD** in the first sentence, listing blockers, warnings, the exact check command run, and anything not verified.

## Rules

- Never silence a blocker by editing the auditor or weakening `--cmd`; fix the diff or explain why a warning is deliberate.
- Never report **SHIP** if tests were not run after the last edit.
- Keep `diff_audit.py` read-only on the repository: do not auto-stage, commit, or push unless explicitly asked.
