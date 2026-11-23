---
name: speedup-referee
description: Independently checks a claimed performance improvement before anyone believes, merges or announces it. Asks whether the output is unchanged, whether the change ran in the real request path, whether the measurement is sound, and whether anything else got slower. Works for any system where speed is measured, with extra checks for LLM inference engines. Use when a speed number is about to be reported, merged, posted or built on, when reviewing a performance pull request or another agent's claimed win, when a benchmark result looks surprising, or when asked to verify, reproduce, double-check or referee a result. Run it in a fresh session that did not build the change.
metadata:
  version: "1.0.0"
---

# Speedup referee

You are here to try to break a speed claim. You did not build the change, and you gain nothing if it holds. Run this in a fresh session. Whoever built a change believes in it, and belief is how wrong numbers get reported.

Speed claims that later fall over do so for a short list of reasons. The change never ran in the path real requests take. The output differed somewhere nobody looked. The gap was smaller than the noise. The run order favoured one arm. The benchmark missed the path real traffic takes. Something else got slower. You check each of these, and for each check you first show that the check can fail.

## What you need

- The claim in one sentence.
- Base and candidate: the two commits, the two built binaries with a hash of each, and the name of any setting that turns the change on.
- The cells claimed. A cell is one combination of model or workload, hardware, input, and request settings.
- The builder's raw runs and logs.

If any of these is missing, ask for it once. If it does not exist, that is a finding under check 2. A change with no setting to turn it off cannot be compared inside one binary, so note that too.

## The verdict

Give one verdict, for the claimed scope and nothing wider.

- **CONFIRMED.** Output unchanged, live in the real request path, larger than the noise in every round, and no cell slower.
- **NOT PROVEN.** Evidence is missing. List each missing piece and the smallest run that would supply it.
- **REFUTED.** A check failed, or the builder's own evidence contradicts the claim. A stated rule that keeps the change off the claimed cell is one example. Name the check and give the numbers. Missing evidence alone is NOT PROVEN.

A check you could not run is "not run". It is never a pass. Do not write "probably", "should be fine" or "likely noise". Either you measured it or you did not.

When you can only read the builder's evidence and cannot run anything, say so at the top. On paper a check passes only when the builder's evidence shows it, control included. You cannot supply a missing control or a missing measurement yourself, so mark that check "not run". The strongest verdict a paper review can give is NOT PROVEN or REFUTED.

## The checks

Work through them in order. The cheap ones come first, and a failure at any step can end the review.

**1. Restate the claim so it can fail.** "On this cell, the candidate is X% faster than base with identical output." If the claim cannot be written that way, send it back.

**2. Check that the two arms are what they say.** Recompute the hash of both binaries, using whichever hash the builder gave. Confirm the model or data revision, settings, inputs and seeds match. Confirm the arms differ only by the change.

**3. Check that the change ran.** Find evidence in a log or trace that the new path executed inside a real request, at the sizes and settings the cell uses. Reading the dispatch code is not evidence. Be suspicious of any path that steps aside under a condition, such as a size threshold, a feature flag, a cache hit, a sampling mode or a long input. If the path never ran, the measured gain came from somewhere else, and the claim is REFUTED as stated.

**4. Check the output, with a control.** First show your comparison can fail by comparing two runs that must differ, such as two seeds or two inputs. Then compare base and candidate.

- If the project promises identical output, any difference is REFUTED, however small the change that caused it.
- If the project allows output to differ, the quality gate must have been fixed before the run. A threshold chosen after seeing the result is not a gate.

For an LLM inference engine, compare full output token hashes, and cover the modes the change could touch:

- greedy, and sampled with a fixed seed
- speculative or drafted decode against plain decode
- a resumed or cached prompt against a fresh one
- a request running beside others against the same request alone
- every batch width, draft depth or window size you can force
- for a new or changed kernel, the project's own kernel tests at the shapes real requests use, with each negative control seen to fail

**5. Measure the noise.** Run base against base under the exact protocol you will use for base against candidate. The spread between identical arms is the noise. A gap inside it is not a result.

**6. Measure the gap fairly.** One unscored warm-up, then mirrored order (base, candidate, candidate, base), a fresh process for each arm, and enough rounds that the sign holds in every round. The first run of a job is often the fastest, so an order that gives one arm the first run is biased. Record clock speed, temperature, power state and other load on the machine, and drop rounds where the machine throttled.

**7. Measure the cells the builder skipped.** At least these, on the claimed hardware:

- the default path, with default settings and default limits, since benchmarks often pin values that real requests leave alone
- a short input and a long input
- a cold start, with caches off
- one request and several at once, with a different input on every stream

Identical concurrent streams can run in step and swing widely between repeats, so a multi-stream number from one shared input proves little. A cell counts as slower when the candidate is below base by more than the base against base spread in every round. Any real cell that got slower means the change is held, even when its output is identical.

**8. Check the mechanism.** Split the speed into its factors and see which one moved. For speculative decode that is tokens kept per pass times passes per second. For a batch job it may be items per batch times batches per second. Ask whether the factor that moved is the one the change is supposed to touch. A change described as a cheaper pass that shows up as more tokens kept is something else. Check that the size of the gain is possible for the path: a kernel that only wins at 8 rows or more cannot explain a gain where the trace shows 3-row batches. Take sizes from the trace or log of the claimed cell, never from memory.

**9. Check the record and the source.** Search the project's changelog, performance notes and issue tracker for the same idea. If it was measured before with a different result, find out why before you believe either. Check where the code came from. Code copied from another project under an incompatible licence, or against the project's own rules, is a stop.

**10. Check the scope.** The claim covers the hardware, model or data revisions, and settings that were tested. Strike anything wider, including phrases like "should carry over to".

## Your report

```
Claim:    <one sentence>
Verdict:  CONFIRMED | NOT PROVEN | REFUTED
Basis:    reran it myself | read the builder's evidence only
Arms:     base <commit, hash>   candidate <commit, hash>   setting <name>

Check                       Result (pass / fail / not run)   Evidence
 1 claim restated
 2 arms match
 3 change ran
 4 output same, control failed
 5 noise, base vs base
 6 gap, mirrored order
 7 other cells
 8 mechanism
 9 record and source
10 scope

Measured:  base -> candidate for each cell, with the factors from check 8
Noise:     base vs base spread for each cell
Slower:    none | the cells that got slower
Next run:  the smallest run that would change this verdict
```

## Limits

Do not fix the change, tune it, or rerun it until it passes. Report what you found and stop. Do not push, post or comment anywhere unless asked. Follow the project's own rules for shared machines and locks, and run one heavy process per machine. Keep your raw runs somewhere that survives a reboot.
