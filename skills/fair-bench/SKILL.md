---
name: fair-bench
description: Runs an honest A/B benchmark of two shell commands and answers one question, whether a change is actually faster. The bundled script warms both commands up, measures how far two runs of the same command drift apart, compares base and candidate in a mirrored order, and prints one verdict with the numbers behind it, FASTER, SLOWER or WITHIN NOISE. Use whenever someone says a change is faster or slower, before quoting a speed number in a commit, pull request or report, before merging a performance change, and when a benchmark gap looks too small or too good to trust. Times the whole command by default, or reads a rate or score that a benchmark client prints.
compatibility: The bundled script needs Python 3.9 or later and uses only the standard library. It runs commands through the system shell. Load average is reported where the operating system provides it.
---

# Fair bench

"Is my change faster?" has three honest answers: yes, no, and "this test cannot tell". Timing each version once gives the third answer and reports it as the first. The script here runs both versions under a fixed protocol and says which of the three you have.

## When to use it

- Someone says a change is faster or slower. That includes you.
- You are about to write a speed number in a commit, a pull request or a report.
- A performance change is about to be merged.
- Two builds, flags or configurations need comparing on one machine.

When the claim is someone else's and you are judging their evidence, use the sibling skill `speedup-referee` if it is available. This skill makes a measurement. That one checks a claim.

## The one command

```bash
python3 scripts/ab_bench.py --base 'BASE COMMAND' --candidate 'CANDIDATE COMMAND'
```

The script is `scripts/ab_bench.py` in this skill's folder. Both commands go through the shell exactly as written, so pass only commands you would type yourself.

Timing two builds of a command-line tool on the same input:

```bash
python3 scripts/ab_bench.py \
  --base './build-base/mytool compress sample.bin -o /dev/null' \
  --candidate './build-new/mytool compress sample.bin -o /dev/null' \
  --require not-slower --json bench.json
```

Comparing a rate printed by a benchmark client, where higher is better:

```bash
python3 scripts/ab_bench.py \
  --base './bench-client --url http://127.0.0.1:8001 --requests requests.txt' \
  --candidate './bench-client --url http://127.0.0.1:8002 --requests requests.txt' \
  --metric-regex 'requests/s: ([0-9.]+)' --higher-is-better \
  --timeout 600 --cooldown 5 --require faster --json bench.json
```

The default metric is wall-clock seconds of the whole command. `--metric-regex` reads capture group 1 of the first match in the command's output instead. `--rounds`, `--aa-rounds` and `--warmup` set the counts, which default to 5, 3 and 1. `--timeout` stops a hung run. `--cooldown` pauses between runs. `--help` lists the rest.

## What the protocol does and why

The protocol is fixed by the arguments and printed as the first line, so the numbers cannot shape the method.

1. **Warm-up.** Each command runs once, unscored. The first run of a job is often the fastest or the slowest, because caches are cold and the machine has not settled.
2. **Noise phase.** Base runs against base for three rounds, in the same order as the real comparison, with the two slots labelled as fake arms. The largest gap between them is the noise floor. A gap smaller than what one command shows against itself is not a result.
3. **Comparison.** Five rounds, each in the order base, candidate, candidate, base. A round's value for an arm is the mean of its two runs. Each arm gets one early slot and one late slot, so a machine that is steadily heating up or slowing down favours neither.
4. **Verdict.** FASTER only if the candidate wins every round and the median gap is larger than the noise floor. SLOWER by the mirror-image rule. Anything else is WITHIN NOISE. Every round must agree because one good round is an anecdote. The median is used so that one wild round cannot carry the number.
5. **Machine context.** The platform, CPU count and load average before and after are printed, with a warning when the machine was busy or the load moved, because other work moves the number.

With the defaults, two identical commands get a FASTER or SLOWER verdict about once in 90 runs, if their rounds are independent. With three rounds it is once in 20. With one round it is once in 4. That is why five rounds is the default.

## How to read the output

```text
protocol: warm-up 1 run per arm, unscored; noise 3 rounds of base against base; comparison 5 rounds in order base, candidate, candidate, base; verdict FASTER or SLOWER only if one arm wins every round and the median gap is above the noise floor
metric: wall-clock seconds, lower is better
base: ./build-base/mytool compress sample.bin -o /dev/null
candidate: ./build-new/mytool compress sample.bin -o /dev/null
machine: Linux-6.8.0-x86_64-with-glibc2.39, 8 CPUs
load average before: 0.21 0.18 0.15 (1, 5, 15 min)
warm-up 1: base 2.47, candidate 2.15 (unscored)

noise phase: base against base
round     fake-base  fake-candidate     gap %
    1         2.405           2.425     +0.83
    2         2.415           2.395     -0.83
    3         2.405           2.445     +1.66
noise floor: 1.66% (largest gap between two arms that are the same command)

comparison
round          base       candidate     gap %  better
    1          2.42            2.11    -12.81  candidate
    2          2.41            2.12    -12.03  candidate
    3         2.425            2.11    -12.99  candidate
    4          2.41            2.13    -11.62  candidate
    5          2.42            2.12    -12.40  candidate
median gap: -12.40% (candidate better in 5 of 5 rounds, lower is better)

load average after: 0.94 0.36 0.21 (1, 5, 15 min)
VERDICT: FASTER (median gap -12.40%, noise floor 1.66%, candidate better in 5 of 5 rounds, lower is better)
```

- **Gap.** It is `(candidate - base) / base`. For time, -20% means the candidate took 20% less time, which is 1.25 times the speed. Say which of the two you are quoting.
- **Noise floor.** The largest gap between two arms that were the same command. Read every other number against it.
- **Better.** Who won each round. A verdict needs a clean sweep.
- **WITHIN NOISE.** This test could not tell the two apart. It does not prove they are equal.
- **Warning lines.** The machine was busy, the load moved, or there were too few rounds. Report the warning with the result.
- **Exit code.** 0 means the benchmark ran, whatever the verdict. With `--require`, 1 means the requirement was not met. 2 means bad usage or a run that failed, timed out or printed no metric. The error names the run and shows the last lines of its output.
- **JSON file.** `--json` keeps every raw sample, the order the runs happened in, the noise floor, the verdict and the machine context. Attach it to the claim so a reader can check it.

## What it cannot tell you

- **Whether the output is right.** A command that skips work is faster. Compare the two outputs before you time anything.
- **Cells you did not run.** One input, one size and one thread count were measured. A win on a small input says nothing about a large one.
- **Other machines.** The verdict holds for this hardware, this operating system and this build.
- **Thermal throttling.** The script sees load average, not temperature or clock speed. If the machine slows down partway through, the noise floor was measured earlier and may no longer describe it. Use `--cooldown` and mains power for long runs.
- **Short commands.** Wall-clock time includes starting the shell and the process. For a command that takes a few milliseconds, start-up is most of what gets timed and the gap looks smaller than it is. Loop the work inside the command, or print a rate and use `--metric-regex`.
- **Why.** It says that the candidate is faster, not what made it so.

## Rules for the agent

1. Decide the rounds, the metric and `--require` before the first run, and write them down. A method chosen after seeing numbers can be chosen to fit them.
2. Never rerun until the verdict flips. Each extra try is another chance for noise to give the answer you wanted. If a run was spoiled by an error or a warning, fix the cause, and report the rerun as a rerun.
3. Report WITHIN NOISE as "no measurable difference". Do not write "slightly faster", because the test did not show that.
4. Quote the noise floor beside any number. "12% less time, noise floor 2%" can be judged. "12% faster" cannot.
5. Change one thing. Both commands get the same input, flags, thread count and output destination, or the benchmark measures the other difference.
6. Check correctness first. A faster wrong answer is not a speedup.
7. For concurrent workloads, give each stream its own input. Identical streams hit the same caches and fall into step with each other, so the rate swings between runs and does not describe real traffic.
8. Stop other heavy work and leave the machine alone during the run. Other load moves the numbers, and the script can only warn about it.
9. Do not widen the claim. One verdict covers one workload on one machine, so run the other cells before you speak for them.
