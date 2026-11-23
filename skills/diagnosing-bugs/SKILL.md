---
name: diagnosing-bugs
description: Finds the cause of a bug or performance regression that survived a first look. Follows a fixed method that rules out the dull causes, reproduces the failure on demand, shrinks the case, halves the search space, tests rival explanations, then fixes the cause and proves the fix. Use when a failure has no obvious cause, a test is flaky, a first fix did not hold, behaviour differs between machines or versions, or something became slower. Not needed for an obvious one-line fix. Includes scripts that measure a failure rate and shrink a failing input file.
compatibility: The bundled scripts need Python 3.9 or later and use only the standard library. Bisecting by commit needs git.
---

# Diagnosing bugs

Guessing is the slow way to find a bug. This method replaces it with two moves. First make the failure cheap to trigger. Then halve the space where the cause can hide until one thing is left.

## When to use it

Use it for a bug or performance regression that survived a first look. Typical signs are an error that does not point at its cause, a fix that did not hold, a failure that comes and goes, or a failure on one machine only.

For an obvious one-line fix, just fix it.

## The steps

1. Triage. Spend two minutes on the dull causes.
2. Reproduce. Get one command that fails on demand.
3. Reduce. Find the smallest input, config and steps that still fail.
4. Localise. Halve the search until you have the faulty place.
5. Explain. Test rival hypotheses against each other.
6. Fix the cause.
7. Prove it.
8. Clean up and report.

Work in order. Skip a step only when its result already exists, for example when the person hands you a failing test.

Keep a short log of each thing you tried and what it showed. The report comes from that log, and it stops you from testing the same idea twice.

## 1. Triage

Most failures have a dull cause. Check these before forming any theory, because each check takes seconds and a theory built on a stale build can cost an hour.

| Dull cause | Fast check |
| --- | --- |
| Wrong directory, branch or environment | Print the working directory, the branch, and the path of the interpreter or binary that actually runs. |
| Stale build, cache or long-running process | Clean and rebuild. Restart the server or worker. Confirm the running process started after your last edit. |
| File not saved | Diff the file on disk against the change you believe you made. |
| Installed version differs from the docs | Print the version the program loads at run time, then read the docs for that version. |
| Missing environment variable or config value | Print the value inside the process that fails. An empty string counts as missing. |
| Typo in a name, path or key | Copy the name from where it is defined and search for it. Do not compare by eye. |
| Missing permission | Read the whole error for "denied" or "forbidden". Check file modes and the scope of the credential. |
| Time zone, encoding or line endings | Compare time zone and locale with a machine where it works. Look at the raw bytes of the input. |
| The test or the expectation is wrong | Work out the expected value by hand and compare it with the assertion. |

If triage finds the cause, fix it, confirm, and stop.

## 2. Reproduce

Get one command that fails on demand and shows the person's exact symptom.

- Make it one command with no manual steps, and make it exit nonzero when the bug shows. Every later step runs it many times.
- Match the person's symptom exactly. A different error message is a different bug, and fixing it leaves theirs in place.
- Capture the exact error in a file: the full message, the stack trace and the exit code. Do not paraphrase it, because the exact words are what you will search for.
- Write down what the command needs: the commit, the input, the environment.

### Flaky failures

Measure the failure rate before you chase a clean reproduction. A 1 in 100 failure needs a loop before it needs a theory.

```sh
python3 scripts/repro_rate.py -n 200 -t 60 -- <command that sometimes fails>
```

The rate is the baseline for everything after it.

- Anything that changes the rate is a clue. More load, more parallel workers or a shorter timeout often raise it, and a bug that fails half the time is far easier to work on.
- The rate sets how many clean runs a fix needs. Use about 3 divided by the rate, so a 1 in 100 failure needs about 300 clean runs. Twenty clean runs prove nothing there, because twenty in a row pass 82% of the time with the bug still present.
- Compare a failing run with a passing run. Look at ordering, timing and data.

## 3. Reduce

Shrink the input, the config and the steps while it still fails, one cut at a time.

- Make one cut, then rerun. If it still fails, keep the cut. If it passes, undo the cut and note it, because that piece is part of the cause.
- Cut one thing at a time. After two cuts at once you cannot tell which one mattered.
- After each cut, check that it is still the same failure. Removing a line can cause a new error, such as a syntax error, that only looks like progress.
- Candidates to cut are input records, config options, plugins, dependencies, setup steps, concurrency, and code that is not on the failing path.
- Stop when every piece that is left is needed for the failure.

For an input file made of lines, a script does the cutting:

```sh
python3 scripts/shrink_input.py orders.csv --match "exact error text" -- <command> {}
```

## 4. Localise by halving

A search that rules out half the space each time ends fast. A thousand commits need about ten tests. There are four ways to halve.

### By commit

Use this when it used to work. Find one good commit and one bad commit, then let `git bisect` drive a check script.

```sh
git bisect start <bad-commit> <good-commit>
git bisect run ./check.sh
git bisect reset
```

The check script builds, runs the repro command, and exits 0 for good and 1 for bad. Make it exit 125 when the commit cannot be built, so that commit is skipped and not blamed.

Run the check by hand on both end commits first. A bisect with a wrong end point returns a confident wrong answer. For a flaky bug the check must loop often enough to catch it, or one lucky pass sends the search into the wrong half.

### By input

Feed half the input. If it still fails, the trigger is in that half. `scripts/shrink_input.py` does this for line-based files.

### By code path

Switch half the code path off. Turn off a feature flag, replace a step with a fixed value, return early, or drop a plugin. If the failure goes away, the cause is in the half you switched off.

### By layer

Data crosses boundaries on its way through the system, for example request, parser, validator, store, response. Print the real value at each boundary and find the first place where it is wrong. The fault lies between the last right value and the first wrong one.

Print what is there, not what you expect. Show the type, the length and a quoted form of the value, so that stray whitespace and encoding problems are visible.

## 5. Explain

Localising says where. Now say why.

- Write down at least two hypotheses. With only one you tend to collect evidence that agrees with it.
- Give each one a prediction that could turn out false. "If the cache is stale, the first request after clearing it is correct." "If it is a race, the failure rate rises with more workers and drops to zero with one."
- Pick the observation that separates them, meaning one where they predict different results. Make that observation.
- Change one thing at a time, and revert each change that did not help. Otherwise the code fills up with edits whose effect you do not know.
- A fix you cannot explain may have moved the bug. If the failure stopped and you do not know why, you are not done. Find out why that change mattered.

When stuck, reread the exact error text. Then list the facts you have not checked yourself in this session and check the one you are most sure of.

## 6. Fix the cause

- Fix the cause, not the symptom. The symptom is where the failure shows. The cause is where the wrong value or state first appears. A null check at the crash site hides the question of why the value was null.
- Write the failing test first when the code has a place to put one. Watch it fail with the same symptom, then apply the fix and watch it pass. A test that has never failed may not test anything.
- If the code has no place for a test, say so in the report and keep the repro command as the check.
- Keep the change small. Leave unrelated cleanup out, because it makes the fix harder to review and harder to revert.

## 7. Prove it

Do all four.

1. Run the original repro command from step 2, unchanged. It passes. For a flaky bug, run it about 3 divided by the failure rate times.
2. Put the bug back. Revert the fix, keep the new test, and confirm the test fails. Then restore the fix. This shows the test detects this bug.
3. Run the wider test suite, because the fix may have broken a neighbour.
4. Look for the same mistake elsewhere. Search for the same call, pattern or assumption, and report every site you find, including the ones you did not fix.

## Performance regressions

The method is the same. The failure is a number.

- Measure same against same first. Run the benchmark several times on one unchanged build. The spread of those results is the noise.
- Never trust a difference smaller than the noise. If two runs of one build differ by 4%, a 3% slowdown is not yet a finding. Lower the noise with more runs, a fixed input, a warm cache and a quiet machine, or state that the difference cannot be resolved.
- Then bisect on the metric. The check script runs the benchmark and exits 1 when the number is worse than a threshold set between the good value and the bad value. This only works when the gap is several times the noise.
- Keep everything else equal: machine, input, build flags and warm-up.
- After the bisect, profile the good commit and the bad commit to see where the time went. The explanation should account for the size of the change.

## Clean up

Tag every temporary debug line with one unique marker, so a single search removes them all. Choose a marker that cannot occur by accident, such as `TMPDBG-4821`, before you add the first line.

```sh
grep -rn "TMPDBG-4821" .
```

That search must come back empty before you finish. Also remove temporary files and saved logs, run `git bisect reset`, re-enable anything you switched off, and restore changed settings.

## Secrets

Logs, environment dumps and request traces often carry credentials. Before you show one to anyone or put it in a report, issue or commit, redact tokens, keys, passwords, cookies and auth headers. Keep the shape so the line stays readable, as in `Authorization: Bearer <redacted>`.

Print one variable, not the whole environment. Treat files saved by the scripts here as sensitive, since they hold raw command output.

## When it cannot be reproduced here

Do not guess at a fix and call it done. Say plainly that you could not reproduce the failure, then:

- List what you tried: the commands, versions, inputs and number of runs.
- List the known differences between your setup and theirs. The cause is often on that list.
- Ask for one of three things: the environment details, a captured artifact, or permission to add temporary instrumentation where the failure does happen. Environment details are the operating system, versions, config and exact command. An artifact is a full log, the failing input, a core dump or a trace.
- If you name a likely cause, label it as an unverified hypothesis and say which observation would confirm it.

## Report

If the session or the person has its own report format, use it and make sure these five facts are in it.

1. Cause. What was wrong and where, in one or two sentences.
2. Evidence. The observation that singles out this cause, such as the bisect result, the value at the boundary, or the test that fails without the fix.
3. Fix. What changed.
4. Verification. The exact commands and their results.
5. Not verified. Checks that did not run, platforms not tested, run counts below the target, and similar sites found but not fixed.

## Symptom to first check

| Symptom | Check first |
| --- | --- |
| Works on one machine, fails on another | Diff versions, environment variables and config between the two. Then operating system, locale and time zone. |
| Worked yesterday, no code change | A dependency that updated, an expired certificate or token, date-dependent logic, a full disk, a changed external service. |
| Broke after a pull or an upgrade | Bisect by commit between the last good state and now. |
| Passes alone, fails in the full suite | State shared between tests. Run it after half of the other tests, then halve again. |
| Fails only sometimes | Measure the rate. Then look at timing, ordering, shared state, unseeded randomness and the network. |
| Fails only in CI | What CI lacks or adds: a clean checkout, different variables, parallel jobs, resource limits, tool versions. |
| An edit has no effect | You are not running the code you are editing. Add a deliberate error to prove the file is loaded. |
| Fails only on large or particular input | Halve the input. Look for size limits, special characters, empty values and duplicates. |
| Correct at first, wrong after a while | State that builds up: memory, file handles, connections, an unbounded cache. |
| Slower than before | Measure the noise first, then bisect on the metric. |
| Off by some hours or by one day | Time zone handling, daylight saving, UTC against local time, date boundaries. |
| Garbled or unexpected characters | Bytes decoded with the wrong encoding, or a byte order mark at the start of the file. |
| "Not found" for a thing that exists | A relative path resolved from another directory, letter case, a typo, or a permission that hides it. |
| Hangs | Dump the stacks of the hung process. Look for a lock, a network call with no timeout, or a wait for input. |

## Worked example

Symptom: `python3 import_orders.py orders.csv` exits with `sqlite3.IntegrityError: UNIQUE constraint failed: orders.id` on a 20,001-line export. The person says the export holds no duplicate ids.

1. Triage. Branch, build, versions and config all match. Nothing found.
2. Reproduce. The command fails 5 runs out of 5 with the same message, so no loop is needed.
3. Reduce. `python3 scripts/shrink_input.py orders.csv --match "UNIQUE constraint failed" -- python3 import_orders.py {}` leaves three lines: the header, a row with id `420` and a row with id `0420`. Without `--match` the same run ends at two unrelated rows that fail with a different error, because the header was cut.
4. Localise by layer. A marked debug line prints each id as read from the file and as passed to the insert: `'420' -> 420` and `'0420' -> 420`. The file is right and the value is wrong before it reaches the database, so the fault is in the importer.
5. Explain. Hypothesis A says the export holds a true duplicate, which predicts the same id text on both lines. Hypothesis B says the importer turns two different ids into one, which predicts different text in the file and equal values after parsing. The output from step 4 separates them and fits B. The importer calls `int()` on the id, which drops the leading zero.
6. Fix the cause. A new test imports a fixture with ids `420` and `0420`, expects two orders, and fails with the same error. The fix keeps the id as text, which is what the column type already says. Skipping rows that collide would have silenced the error and lost an order.
7. Prove it. The full export imports. With the fix reverted, the new test fails. The full suite passes. A search for other `int()` calls on id columns finds one in `import_customers.py`, which goes in the report.
8. Clean up. The marker search comes back empty and `orders.min.csv` is deleted.

## Scripts

All three scripts need Python 3.9 or later and nothing outside the standard library. Each prints its options with `--help`. Put the command to run after `--` where used.

### `scripts/auto_bisect.py`

Uses Git's bisection ranking to search the commit graph in a temporary worktree. Verifies that `--good` passes and `--bad` fails before bisecting, supports `--build-cmd` to skip unbuildable commits, and supports `--repeats N` for flaky regressions.

A failed build or a check that exits 125 leaves that revision unclassified. If the verified bounds still contain several possible first bad commits, the tool exits 1 with `INCONCLUSIVE` and lists the candidates in the console and JSON report.

```sh
python3 scripts/auto_bisect.py --good v1.2.0 --bad HEAD --repeats 5 --cmd "pytest tests/test_sync.py"
```

Exits 0 with `BISECT: FOUND <commit>`, 1 if inconclusive, and 2 if either endpoint fails validation or usage is wrong.

### `scripts/repro_rate.py`

Runs a command N times and reports how often it fails.

```sh
python3 scripts/repro_rate.py -n 100 -j 4 -t 30 --save failure.log -- <command>
```

- A run fails when it exits nonzero or passes the `-t` time limit. A run over the limit is killed.
- It prints failures out of N, the distinct exit codes with counts, and the minimum, median and maximum time per run.
- It saves the output of the first failing run to the `--save` file, by default `repro_rate_failure.log`.
- It exits 0 if no run failed, 1 if any run failed and 2 on bad usage.
- `-j` runs several at once. Overlapping runs can themselves cause or hide failures when tests share files or ports, so compare against `-j 1`.

### `scripts/shrink_input.py`

Removes chunks of lines from an input file while a command still fails, then writes the smallest failing input it found.

```sh
python3 scripts/shrink_input.py input.txt --match "error text" -o small.txt -- <command> {}
```

- Write `{}` where the command takes the file path.
- It first runs the command on the original input twice. If that does not fail both times, it refuses and exits 1.
- `--match TEXT` counts a run as failing only when its output contains TEXT. Use it, so the result keeps the original error and not a new one.
- Choose match text that appears only in the error itself. A stack trace quotes the source line that failed, so a message that sits on that line in your code will also match a different error raised from the same line.
- It never changes the original. The result goes to `-o`, by default the input name with `.min` before the suffix.
- It exits 0 after writing a failing input, 1 if it refused or could not confirm the result, and 2 on bad usage.
- `-t` sets the time limit per run, 60 seconds by default. `--max-runs` caps the number of runs, 1000 by default.

Limits: it works on whole lines, it needs a failure that repeats every time, and it runs each trial on a copy in a temporary directory. An input that refers to neighbouring files by relative path will not fail there, and the script says so and stops.
