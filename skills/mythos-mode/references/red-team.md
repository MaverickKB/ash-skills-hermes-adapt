# Red-teaming: how to attack work before it ships

Use this playbook when you are about to report work that matters, and when you are asked to review someone else's work, plan, claim, number, or instructions. Red-teaming here means one thing: trying on purpose to make something fail, so that the failure is found by you, now, and not by the person later.

## Contents

1. The stance
2. Step 1: write down what is being claimed
3. Step 2: name what would prove each claim false
4. Step 3: the seven angles of attack
5. Step 4: test the tester
6. Step 5: attack the report
7. Red-teaming a plan or design
8. Red-teaming a claim or a number
9. Red-teaming a document, a prompt, or a set of instructions
10. Red-teaming your own answer to a question
11. Reporting what you found
12. When to stop
13. Signs you are not really doing it
14. A worked example

## The stance

Building and breaking are different jobs, and you cannot do both at once. While you build, you want the thing to work. That wish is useful for building and it blinds you to the places where the thing does not work. So switch on purpose. Say to yourself: for the next few minutes I am paid to find the failure, and finding one is a win.

Three rules hold the whole method together.

1. **A check counts only if it could have failed.** A test that passes whether or not the bug is present has told you nothing. Before you trust a green result, ask what it would have looked like if the work were broken.
2. **A finding counts only if you reproduced it.** A suspicion is a lead. Run it down before you report it. A plausible bug that is not real costs the author time and costs you your credibility.
3. **The description of a thing is a claim about the thing.** A README, a comment, a commit message, a status report, and your own memory of the code all describe. None of them is the system. Check the system.

## Step 1: write down what is being claimed

You cannot attack a fog. Turn the work into a list of claims, each written so it can fail.

"The export works" cannot fail. "Running `export.py --month 2026-03` writes one CSV with one row per March invoice, and the amounts sum to the ledger total" can.

Then add the claims nobody said out loud. These are where most failures live.

| Unstated claim | The question it hides |
|---|---|
| It works somewhere other than here | Did it only run on my machine, my branch, my data? |
| The thing I changed is the thing that ran | Was the new code path actually executed, or a cache, a fallback, an old build? |
| Nothing else got worse | Which callers, cases, or users did I not look at? |
| It handles the inputs I did not try | Empty, huge, malformed, duplicated, out of order? |
| The result is repeatable | Would a second run give the same answer? |
| The docs and the code agree | Did I update the command, the flag, the example? |
| The check I ran is able to fail | Has anyone ever seen it go red? |

## Step 2: name what would prove each claim false

For each claim, write the observation that would show it is false. Be specific enough that you could go and look.

- Claim: "the migration is safe to rerun." Disproof: run it twice on a copy and compare row counts and checksums after each run.
- Claim: "the fix handles all date formats." Disproof: a format from the bug reports that still parses wrongly.
- Claim: "the new index made the page faster." Disproof: the same page timed with the index dropped is just as fast.

Then rank the attacks. You will not run all of them, so run the best ones first.

- How likely is this claim to be false?
- How bad is it if it is false and nobody notices?
- How cheap is the check?

Run first the checks that are cheap and aimed at a failure that is likely or expensive. A thirty-second check against a disaster beats an hour-long check against an inconvenience.

## Step 3: the seven angles of attack

Work through these in order for anything that matters. For small work, pick the two or three that fit.

### 1. Inputs

Feed it what the author did not have in mind.

- nothing: empty string, empty file, empty list, zero rows, null
- one item, and two identical items
- the largest plausible input, and one past the stated limit
- the boundary and one step either side of it: 0, 1, -1, the last day of the month, midnight
- malformed: truncated file, wrong type, missing field, extra field, wrong encoding
- awkward text: spaces, quotes, unicode, a name with an apostrophe, a path with a space
- out of order, duplicated, or arriving twice
- hostile: input written by someone who wants it to misbehave

### 2. Assumptions

List what has to be true for the work to be right. Then find the one you checked least and check it against the real thing.

Typical unchecked assumptions: the file is where the docs say, the API returns the field in every case, the column is never null, the config in the repo is the config in production, the function is only called from one place, the library version matches the docs you read.

A cheap and powerful version of this: take every concrete fact the work depends on (a path, a flag, a field name, a version) and look each one up in the real system. This finds more errors per minute than any other check.

### 3. Environment and state

- a clean checkout or a fresh machine, with nothing left over from your earlier runs
- a different version of the runtime or a dependency
- a missing permission, a read-only disk, no network
- running it twice in a row
- stopping it halfway and starting again
- two copies running at the same time
- a different working directory, time zone, or locale

### 4. The path

Confirm that the code you changed is the code that ran. This is the most common way a correct-looking result turns out to mean nothing.

- Find evidence from a log, a trace, or a printed marker that the new path executed. Reading the dispatch logic does not count.
- Look for anything that could have stepped in front: a cache, a feature flag, a fallback branch, a stale build artifact, a different binary on the path, a mock.
- If you can switch your change off, do it and confirm the result changes. If nothing changes, your change was not what produced the result.

### 5. The check itself

See "Step 4: test the tester" below. This angle gets its own section because it is the one most often skipped.

### 6. The measurement

When the claim is a number, attack how the number was obtained.

- **Noise.** Measure the same thing against itself. The spread between two identical runs is the noise, and a difference smaller than that is not a result.
- **Order and warm-up.** The first run is often different from later ones. Alternate the order, or one arm gets the lucky slot every time.
- **Sample size.** One run is an anecdote. Say how many runs, and show the spread.
- **What was compared.** Same data, same settings, same machine state on both sides? One difference only?
- **What the metric cannot see.** A faster average can hide a slower worst case. A passing rate can hide which cases fail.
- **The arithmetic.** Recompute it. Check that parts sum to the total and that the percentage has the right base.

### 7. The neighbours

Look at what sits next to the change.

- every caller of the function you changed, and every reader of the file or format you changed
- the other cases, modes, or platforms that share the code
- config, docs, and scripts that live outside the place you edited and still use the old name
- the failure path: what happens now when the thing you call fails

## Step 4: test the tester

Before you trust any check, make it fail once on purpose. This is called planting a fault, and it is the single habit that most separates real verification from the appearance of it.

| What you are trusting | How to plant a fault |
|---|---|
| A unit test for your fix | Remove the fix and confirm the test fails |
| A test suite | Change one comparison or one constant in the code and see whether any test notices |
| A validator or linter | Feed it an input that is wrong in one small way |
| A comparison or hash check | Compare two things that must differ and confirm it says they differ |
| A search that returned nothing | Search the same way for something you know exists |
| A monitor or alert | Trigger the condition and confirm the alert fires |
| A reviewer, human or model | Seed a document with known flaws, keep the answer key apart, and count how many are found |
| A benchmark | Compare a build against itself and confirm it reports no difference |

Make the planted fault small. A validator that catches a file full of garbage may still miss one wrong row, and one wrong row is what real mistakes look like.

Two controls cover most cases. The positive control is a known-good input that must pass. The negative control is a known-bad input that must fail. A check with only a positive control has never been shown to fail.

## Step 5: attack the report

The work can be right and the report about it wrong. Read your draft one sentence at a time and ask what evidence stands behind each one.

Watch for words that claim more than you did.

| Word in the draft | What it promises | Write this unless you truly did it |
|---|---|---|
| "works", "fixed" | You ran it and saw the right behavior | "passes the three cases I ran" |
| "all", "every", "never" | You checked the whole set | the count you checked, out of the total |
| "tested" | A test that could fail was run | which test, and what it covers |
| "verified" | You looked at the real thing | how you looked |
| "exact", "identical" | A comparison was made | what was compared with what |
| "safe" | You considered how it could do harm | which harms you ruled out |

Then ask two last questions. What would a hostile reader check first? Go and check that. What did you not check? Say so in the report, in one plain sentence. A stated gap is information. A hidden one is a trap for the person who trusts you.

## Red-teaming a plan or design

A plan cannot be run, so attack what it depends on.

1. **What has to be true?** List the conditions the plan needs. Pick the least certain one and find the cheapest way to test it before any real work starts.
2. **Run the failure in advance.** Imagine it is a month later and the plan has failed. Write the three most likely reasons. Check or guard against each one now.
3. **Look at how such things usually go.** Find the nearest similar efforts and ask how long they took and how often they worked. Your plan is probably not the exception.
4. **Argue the other side honestly.** State the best case for the alternative you rejected, as its strongest supporter would. If you cannot, you have not understood the choice.
5. **Follow the second step.** Who reacts to this change, what breaks downstream, what does it cost to keep running, what does it cost to undo?
6. **Find the step you cannot take back.** Move it as late as possible, or add a way back before you reach it.
7. **Name the cheapest experiment that could kill the plan.** If one exists, run it first.

## Red-teaming a claim or a number

Someone hands you a result: "40% faster", "no regressions", "users prefer B".

- **Where does it come from?** Is this the primary source or a summary of one? Who measured it, how, and when?
- **Recompute it.** Do the stated inputs give the stated number? Is the size plausible?
- **What was it compared with?** Under the same conditions? Was the baseline chosen to flatter?
- **Would the evidence look different if the claim were false?** If a failing result and a passing one would have produced the same report, the report tells you nothing.
- **What was left out?** Which cases, runs, or users are missing, and would they change the picture?
- **How far does it reach?** A result measured on one machine, one dataset, or one week covers that and nothing wider.

## Red-teaming a document, a prompt, or a set of instructions

Instructions fail in their own ways. Read them as the most literal reader would.

1. **Pull out every checkable fact.** Paths, commands, flags, names, versions, numbers. Check each against the real system. Run every command.
2. **Look for rules with no mechanism.** "Always confirm the target is in scope" means nothing if nothing records the scope. For each rule, ask how the reader could actually do it.
3. **Look for contradictions.** Between sections, between files, and between the summary at the top and the detail below it.
4. **Look for references to things that are not there.** A script, a file, a section, a tool.
5. **Look for undefined terms and unstated thresholds.** "Slower", "large", "soon", "when appropriate".
6. **Look for overlap.** Two documents that claim the same situation and give different orders.
7. **Follow it to the letter.** Walk through a realistic case, doing exactly what it says and nothing it does not say. Note where you had to guess.
8. **Ask who else can read it.** A file that holds an answer key, a secret, or private detail has to be out of reach of the wrong reader.

## Red-teaming your own answer to a question

You have no tool output to check, only what you wrote. Do this before you send it.

- Try the answer on one concrete case. Then try to build a case where it gives the wrong result.
- Check that you answered the question that was asked, and not an easier one beside it.
- Check the definition of the key term. Many wrong answers are right answers about a slightly different thing.
- Ask whether the fact could have changed since you learned it. If so, look it up or say it is from memory.
- Ask what an expert in the field would object to first.

## Reporting what you found

- Reproduce every finding before you report it.
- Put the most serious first. Wrong results, lost data, and security problems outrank misleading output, which outranks inconvenience, which outranks taste.
- For each finding give the place, how to trigger it, what happens, why it matters, and a suggested fix.
- Say what held up. A review that lists only faults hides which parts can be relied on.
- Say what you did not test.
- Do not pad. If you found two real problems, report two. If the work is sound, say so in one line.
- Keep taste separate from defects, and label it as taste.

## When to stop

Stop when the next attack is unlikely to change what anyone does, or when it would cost more than the failure it guards against. Then say what remains untested.

| Kind of work | Reasonable depth |
|---|---|
| A typo, a rename, a comment | Read the diff |
| A bug fix | Rerun the failing case, remove the fix to see the test fail, run the suite |
| A new feature | Inputs, the path, neighbours, and a planted fault in the new tests |
| A script that deletes, moves, or rewrites | A dry run, a run on a copy, a second run, and an interrupted run |
| A number someone will quote | Noise, order, recomputed arithmetic, and a second route to the same number |
| Anything touching money, security, or production data | All seven angles, with the gaps written down |

## Signs you are not really doing it

- Every check passed on the first try. Either the work is unusually good or the checks cannot fail. Find out which.
- You ran only the cases you built the thing to handle.
- You reread your own work and it looked right. It always does to its author.
- You are explaining why a failure you found "does not really count".
- Your review of someone else's work found only style points.
- You cannot name anything you did not check.

## A worked example

**The claim.** A teammate hands over `cleanup.py` with the note: "Removes build artifacts older than 30 days. Tested and safe."

**The claims, written to fail.**

1. It deletes only build artifacts.
2. It deletes only files older than 30 days.
3. It was tested with a test that could fail.
4. Running it twice, or stopping it halfway, does no harm.

**The attacks, in order of cost and danger.**

- *Read what it matches.* The script deletes anything matching `**/build/**`. Disproof of claim 1: a directory named `build` that holds something other than artifacts. A search finds `docs/build/handbook.pdf`, which is written by hand and checked in.
- *Follow a link out of the tree.* `build/cache` is a symbolic link to a shared directory outside the project. Disproof of claim 1 again: on a copy, point that link at a scratch directory holding an old file and do a dry run. The dry run lists the file in the scratch directory, so the script follows links.
- *Test the tester.* The "test" was a run with `--dry-run` that printed a list nobody compared with anything. Plant two files with known answers: a new file under `build/` that must survive, and a 31-day-old file that must go. The dry run leaves the new file alone and also leaves the old one, because the cutoff is compared in the wrong time zone. Claim 2 fails at the boundary.
- *Run it twice and interrupt it.* On a copy, a second run finds nothing left and exits cleanly. An interrupted run leaves no half-deleted directory. Claim 4 holds.

**The report.**

> Not safe to run yet. Two problems, both reproduced on a copy.
>
> 1. It would delete `docs/build/handbook.pdf`, which is hand-written and tracked. The pattern `**/build/**` matches any directory named `build`. Limit it to the three artifact directories by name.
> 2. It follows symbolic links. `build/cache` points at the shared cache outside the project, and the script would delete old files there. Skip links, and refuse any path that resolves outside the project root.
>
> One smaller issue: the 30-day cutoff is off by the local time zone offset, so a file 31 days old can survive.
>
> What held up: a second run and an interrupted run both leave the tree in a clean state.
>
> Not tested: behavior on the CI machines, where the directory layout differs.
