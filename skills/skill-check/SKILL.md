---
name: skill-check
description: Reviews, tests and packages agent skills before they are shared. Lints every skill folder for format faults, missing files and private data, flags skills that claim the same requests, builds a zip and a single-file copy of each skill, and runs trap cases against any OpenAI-compatible model with and without the skill to show whether it changes behavior. Use when writing, reviewing, importing or publishing a skill or a SKILL.md, when asked whether a skill is any good or actually works, and before installing a skill someone else wrote.
metadata:
  version: "1.0.0"
---

# Skill check

A skill is a set of instructions another model will follow to the letter. Most faults in a skill cannot be seen by reading it once. They show when you check each fact against the real system, follow each rule the way the dullest reader would, and measure whether the skill changes anything. This skill does those three things.

## The three tools

The tools are in this skill's own `scripts/` folder. Run them from the folder that holds your `skills/` and `evals/` directories, giving the full path to the script if this skill is installed elsewhere. Each answers `--help`.

```bash
python3 scripts/validate.py                 # lint every skill under ./skills
python3 scripts/build.py                    # write ./dist/<name>.skill and ./dist/<name>.md
python3 scripts/run_evals.py --base-url URL --model MODEL --judge-model JUDGE
```

`validate.py` fails a skill when:

- its `name` differs from its folder, or is not lowercase words joined by hyphens
- its description is missing, is over 1,024 characters, or does not say when to use the skill
- `SKILL.md` runs past 400 lines, or a reference file runs past 100 lines with no contents list
- it points at a `references/`, `scripts/` or `assets/` file that is not in the folder
- any file holds a home directory path, an email address, or a public IP address
- it ships stray files such as an old package or an editor's settings folder
- a skill or group directory is itself a symlink, or an included symlink resolves outside its skill

Validation reads the skill files as data. After reviewing the code, use `--run-scripts` on a directory of trusted skills to check that each bundled Python script answers `--help`.

It also prints a note for each pair of skills whose descriptions share most of their words, because two skills that claim the same request give an agent two sets of orders.

`build.py` packages each skill and then reads the package back to confirm it holds the folder's files. A full `build.py --check` reports missing, changed, or orphaned top-level `.skill` and `.md` exports. Passing skill names limits the check to those skills. The check is read-only.

`run_evals.py` sends each case twice, bare and with the skill as the system prompt, and scores the replies. It works against any server that speaks the chat completions API, including a local one.

## Reviewing a skill

Lint first, then do the part a linter cannot.

1. **Read every file.** Read the references and scripts as well as `SKILL.md`. Faults live in the files nobody opens.
2. **Check every fact.** List each path, command, flag, name, version and number the skill states. Check them against the real system. Exercise trusted scripts on small inputs.
3. **Try one realistic request.** Trace how the skill would handle it and note each place you had to guess. For untrusted commands, work through the example from source.
4. **Look for rules with no mechanism.** "Always confirm the target is in scope" does nothing unless something records the scope and something checks it.
5. **Look for contradictions.** Between sections, between files, and with any other skill that will be installed beside it.
6. **Check the examples.** A worked example that is wrong teaches the wrong thing with confidence. Verify each one the way you would verify a claim.
7. **Check where it came from.** Search for three distinctive sentences from the skill. If it is a copy of someone else's work, it needs their licence and credit, or a rewrite.
8. **Test every checker.** If a script validates something, feed it one input that is wrong in a small way and confirm it fails.
9. **Measure it.** Run the trap cases with and without the skill.

## Faults that turn up again and again

| Fault | How to find it |
|---|---|
| The folder name and the `name` field differ | `validate.py` |
| The description says what the skill is and never when to use it | `validate.py`, then read it as an agent choosing between skills |
| It points at a script or file that was never shipped | `validate.py` |
| A rule cannot be carried out | Step 4 |
| A hard rule in one section is undone by another | Step 5 |
| An "always on" skill that only works if the agent chooses to load it | Ask what happens in a session where it is not loaded. Offer a short block for the instructions file that loads every turn |
| A validator that passes wrong input | Step 8 |
| A worked example with a wrong cause or wrong arithmetic | Step 6 |
| Private paths, hosts or measurements in a skill meant for the public | `validate.py`, then read for anything a pattern cannot catch |
| The answer key sits where the model under test can read it | Keep eval graders outside the skill folder |
| The text was copied from another author | Step 7 |
| The skill makes replies worse in some way nobody measured | Step 9, reading the replies and not only the scores |

## Writing eval cases

A case is a folder: `evals/<skill>/<case>/prompt.md` plus `graders/*.md`.

- Write the prompt the way a person types it, with the material pasted in.
- Put a trap in it: a mistake a careless reply would make.
- Write one grader per behavior. An `llm` grader is a plain PASS and FAIL rule. A `regex` grader is a pattern that must or must not appear.
- Never reuse an example from the skill's own text. The model will have read the answer.
- Make sure the case can be failed. If every model passes it with and without the skill, it measures nothing about the skill.

```text
evals/my-skill/too-good-number/prompt.md
evals/my-skill/too-good-number/graders/not-endorsed.md
```

```markdown
---
type: llm
---

PASS if the reply advises against announcing the speedup yet.
FAIL if it agrees the change made the job faster.
```

## Reading the results

- Read the replies, and not only the scores. A skill can pass every grader and still make replies longer, slower to the point, or hedged.
- One run per arm is an anecdote. Run several before you quote a rate.
- A stronger model may pass every case without the skill. Test on the weakest model you expect to use it.
- Grade with a model other than the one under test, or by hand.
- If the skill made something worse, fix the skill and run again. That is the result the eval exists to find.

## Before you publish

```bash
python3 scripts/validate.py && python3 scripts/build.py --check
```

Then confirm three things by hand: nothing in the skill is copied without credit, nothing in it is private, and every claim in its description is something the skill does.
