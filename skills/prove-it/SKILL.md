---
name: prove-it
description: Makes an agent prove that work it called done actually works, using evidence gathered now and a check that could have failed. Ships stdlib scripts that verify git diffs against base in an isolated worktree (for fixes and refactors) and run differential output checks with automatic input shrinking. Use when the user says "prove it", "are you sure", "show me it works", "did you actually test that", or doubts a claim of done, fixed, tested or passing. Also use before reporting work as finished when other people will build on it.
metadata:
  version: "2.0.0"
---

# Prove it

Someone has said a piece of work is done. Your job now is to show that it is, or to find out that it isn't. Both results are useful. Nothing you remember counts. Only what you run and read from this point on is evidence.

## 1. List what "done" claims

Start from the behavior the user asked for. Write each claim so a check could disprove it. When a refactor keeps the same contract, the claim is that callers observe the same behavior on both revisions. When someone reports that a suite passed, check which tests actually ran after the last change.

## 2. Gather evidence for each claim, now

Use the strongest evidence you can get, and say which kind it is.

1. You ran the real thing and saw the right behavior.
2. You ran the tests and they passed.
3. It builds, type-checks and lints clean.
4. You read the code and it looks right.

For a bug fix, start with the original failing case. For each claim keep the command, its exit code, and the few output lines that carry the result, copied exactly.

## 3. Show that the main check can fail

Choose the control for the specific claim. Keep the inputs, assertions and environment fixed across runs.

- **Same contract.** Expect the original and the refactor to pass the same check. Compare observable results on representative and boundary inputs, including errors and side effects where relevant. Pick one of the user's inputs and give its expected result. In a temporary copy, change the implementation so that input gives a specific wrong result. Run the unchanged check to catch that difference, then rerun it on the clean refactor.
- **Changed behavior.** Use the original implementation as the failing control only when you can name an input whose expected behavior changed. Run the same regression test on both versions. Confirm it fails for the predicted reason on the original and passes with the fix or feature. Keep the test available in both runs.

For example, suppose both versions of a parser report line 2 for a malformed record. Make the scratch copy report line 3 for that record. The original assertion should show expected 2, got 3. Passing on both clean versions supports the line number for that record.

If the control still passes or fails for an unrelated reason, report the gap and strengthen the check. When asked what would settle a claim, give the input, expected result and faulty result for the user's case. Limit the conclusion to the inputs exercised.

## 4. Report a proof table

Lead with **Proven**, **Partly proven**, or **Not done**, followed by the evidence for the user's claim. For several claims, use a table with columns for the claim, evidence, control result and verdict. Label supplied results as reported and proposed checks as unrun. Report a command, exit code or control outcome only if you observed it or can attribute it to the person who ran it.

Then list what you did not check, one line each, and give the exact command the person can run for each.

## Rules

- "Should work", "looks right" and "I ran it earlier" are not evidence.
- Never mark a claim proven from memory or from reading code alone. Reading earns "looks right from the code, not run".
- If you cannot run anything here, say that first. Then give the claims, what would prove each one, and the commands to run. Do not fill the table with guesses.
- If the work turns out to be broken, say so before anything else. Fix it only if finishing the task is still your job, and then prove the fix the same way.
- Never weaken a check to get a pass.
- Quote output exactly. Do not tidy it.

## Scripts

Both scripts need Python 3.9 or later and use only the standard library. Neither modifies your working tree. Run each with `--help` for full options.

### `scripts/prove_diff.py`

Runs your check on the current working tree, then runs the negative control inside an isolated temporary `git worktree` (no `git stash`, no risk to dirty files).

```bash
# Bug fix or new behavior: keeps changed test files on HEAD and confirms the check fails there
python3 scripts/prove_diff.py --cmd "pytest tests/test_sync.py" --expect-failure "AssertionError: expected two synced chunks, got zero"

# Behavior-preserving refactor: confirms both HEAD and current pass, then injects a fault in a copy
python3 scripts/prove_diff.py --mode refactor --cmd "pytest tests/test_pages.py"
```

In fix mode, choose a distinctive diagnostic from the assertion or application error that represents the reported bug. A proof requires a passing current check and a completed baseline failure containing that diagnostic. A missing prediction or a message also printed on success leaves the claim unproven. Supply the same fixtures to both runs.

In refactor mode the check also runs in the clean scratch copy before the fault is introduced. Exits 0 on `PROOF: PROVEN`, 1 on `PROOF: NOT PROVEN`, and 2 on usage or git errors. Pass `--json receipt.json` to keep a hashed receipt.

### `scripts/diff_oracle.py`

Compares two commands (or `--git-base HEAD` against the current working tree) across a corpus or built-in adversarial edge inputs (`--gen-edge`). Checks that base is deterministic first, then flags any exit-code or output divergence and delta-shrinks multi-line counterexamples with `--shrink`.

```bash
python3 scripts/diff_oracle.py --git-base HEAD --cmd "python3 cli.py {}" --gen-edge --shrink
```

Exits 0 on `ORACLE: IDENTICAL`, 1 on `ORACLE: DIVERGED`, 3 on `ORACLE: INCONCLUSIVE` (base nondeterministic), and 2 on usage or command-launch errors. Shell statuses 126 and 127 are reserved for unavailable commands. The error report identifies the side and input case. Other application exit codes remain part of the comparison.

## Independent check for high-stakes work

When a lot rides on the change, do not rely on proving it inside the same session that wrote it. Hand only the original request, the git diff and the check command to a fresh session (see `break-it`) so confirmation bias cannot carry over.
