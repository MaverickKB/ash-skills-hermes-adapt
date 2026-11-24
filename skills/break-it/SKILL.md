---
name: break-it
description: Attacks a piece of work to find where it fails before anyone relies on it. Ships diff-scoped AST mutation testing, an adversarial CLI/directory fuzzer, and a bias-free fresh-session brief builder. Works on a code change, pull request, script, plan, design, document, instructions, or number someone is about to quote. Use when the user says "break it", "red-team this", "poke holes in this", "what could go wrong", "stress test this" or "tear this apart", when they ask for an adversarial review, and before shipping anything hard to undo.
metadata:
  version: "2.0.0"
  hermes:
    tags: [verification, testing, mutation, adversarial]
    related_skills: [prove-it, diagnosing-bugs]
---

# Break it

You are here to find the failure. You did not build this, and you gain nothing if it survives. A problem found now is a win. The same problem found after it ships is a cost.

Two rules hold throughout. A check counts only if it could have failed. A finding counts only if you reproduced it.

## 1. Say what it claims

Write the claims the work makes, each in a form that can fail. "The export works" cannot fail. "Running it for March writes one row per invoice and the amounts sum to the ledger total" can.

Add the claims nobody stated. It works somewhere other than the author's machine. The changed code is the code that ran. Nothing next to it got worse. It survives input the author did not try.

## 2. Choose the attacks

Pick the row that fits, then order the attacks by how likely the failure is, how bad it would be, and how cheap the check is. Run the cheap, likely, costly ones first.

| Target | Attacks |
|---|---|
| A code change | Empty, single, huge and malformed input. The boundary and one step either side. Every caller of what changed. The error path. Remove the change and see whether its tests fail |
| A script that deletes, moves or rewrites | An unset variable. A path with a space. A symbolic link that leads outside. A second run. A run stopped halfway. Two runs at once. A dry run compared with a real run on a copy |
| A plan or design | What must be true for it to work, and which of those is least certain. The three likeliest reasons it has failed a month from now. How things like this usually go. The step that cannot be undone |
| A document or instructions | Every path, command, flag and number checked against the real system. Rules with no way to carry them out. Two sections that disagree. References to things that are not there. Following it to the letter |
| A number or benchmark | The same thing measured against itself, to learn the noise. Run order and warm-up. How many runs. What was compared with what. The arithmetic, redone. What was left out |

## 3. Run them

- Work on a copy, a branch or a scratch directory. Never attack production or shared data. Anything destructive needs a clear yes first.
- Reproduce each failure before you write it down. Keep the command and the output.
- Check your own checks. If a test you rely on has never been seen to fail, make it fail once.
- If you cannot run anything, reason from the material and label every finding as suspected.

## 4. Report

Lead with the verdict: **Broke it** or **Could not break it**.

For each finding give five things, most serious first.

1. Where it is.
2. How to trigger it.
3. What happens.
4. Why it matters.
5. A fix.

Wrong results, lost data and security problems come before misleading output. Misleading output comes before inconvenience. Taste comes last and is labelled as taste.

Then say what held up, so the reader knows which parts to trust. Then say what you did not test.

## Rules

- Do not fix what you find unless asked. Your job is the report.
- Do not pad. Two real findings are a better report than two real ones and five guesses.
- Mark a finding you did not reproduce as suspected.
- "Could not break it" is a valid result. Say what you tried.
- Stop when the next attack is unlikely to change what anyone does, and say what remains untested.

## Scripts

All three scripts need Python 3.9 or later and use only the standard library. They work in isolated temporary copies and never mutate the repository. Run each with `--help` for full options.

### `scripts/mutate_diff.py`

Mutates only changed lines (from `git diff` or explicit `--files`) in Python (AST spans) plus TypeScript, JavaScript, Go, Rust and Zig (comment/string-aware operator mutations) inside a temp copy, reporting every mutant that survives the test suite.

```bash
python3 scripts/mutate_diff.py --cmd "python3 -m unittest discover -s tests"
```

Exits 0 on `MUTATE: CLEAN`, 1 on `MUTATE: SURVIVORS`, and 2 if the baseline tests fail or usage is invalid.

### `scripts/fuzz_cli.py`

Hammers any CLI or script (`{file}` or `{dir}`) in fresh sandboxes with empty, single-byte, large, invalid UTF-8, unicode, spaces/quotes in paths, outside symlink escape (with a canary file), read-only directories and double-run idempotency.

```bash
python3 scripts/fuzz_cli.py --cmd "python3 cleanup.py {dir} --days 30"
```

Clean nonzero error exits count as handled. Unhandled tracebacks, signals, timeouts and outside symlink modifications exit 1 with `FUZZ: BROKE IT`.

### `scripts/adversarial_brief.py`

Packages only the original request, the git diff (with SHA-256), untracked files and the check command into a fresh-context review brief, stripping out the builder's explanations so a separate session or subagent can attack the change without confirmation bias.

```bash
python3 scripts/adversarial_brief.py --request "Fix retry on 503" --cmd "pytest" --out brief.md
```
