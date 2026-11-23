#!/usr/bin/env python3
"""Fair A/B benchmark of two shell commands.

Runs BASE and CANDIDATE under a protocol that is fixed before the first
measurement, then prints one verdict: FASTER, SLOWER or WITHIN NOISE.

Both commands are run through the shell exactly as given (sh -c on POSIX), so
quoting, pipes and && all work, and so does everything else the shell allows.
Pass only commands you would type yourself. Commands get no standard input.
"""
import argparse
import itertools
import json
import math
import os
import platform
import re
import signal
import statistics
import subprocess
import sys
import time

EPILOG = """\
protocol:
  1. warm-up     each command runs --warmup times, unscored, because the first
                 run of a job is often the fastest or the slowest
  2. noise       --aa-rounds rounds of base against base, in the same mirrored
                 order as the comparison; the noise floor is the largest gap
                 seen between the two fake arms
  3. comparison  --rounds rounds in the order base, candidate, candidate, base;
                 a round's value for an arm is the mean of its two runs
  4. verdict     FASTER if the candidate wins every round and the median gap
                 is larger than the noise floor; SLOWER by the mirror-image
                 rule; otherwise WITHIN NOISE

metric:
  Wall-clock seconds of the whole command by default, lower is better. With
  --metric-regex the metric is capture group 1 of the first match in the
  command's combined stdout and stderr, read as a number.
  The gap is (candidate - base) / base, in percent.

examples:
  time two builds of a tool on the same input
    ab_bench.py --base './build-base/tool in.dat' --candidate './build-new/tool in.dat'

  compare a rate printed by a benchmark client, and fail unless it is faster
    ab_bench.py --base './client --port 8001' --candidate './client --port 8002' \\
        --metric-regex 'requests/s: ([0-9.]+)' --higher-is-better \\
        --require faster --json result.json

exit codes:
  0  the benchmark ran, whatever the verdict (or --require was met)
  1  --require was given and the verdict did not meet it
  2  bad usage, a command failed or timed out, or the metric regex did not match
  130  interrupted; the command that was running is stopped first
"""

EXIT_OK = 0
EXIT_REQUIREMENT = 1
EXIT_ERROR = 2
EXIT_INTERRUPTED = 130

FASTER = "FASTER"
SLOWER = "SLOWER"
WITHIN_NOISE = "WITHIN NOISE"

REAL_ARMS = ("base", "candidate")
FAKE_ARMS = ("fake-base", "fake-candidate")

TAIL_LINES = 8             # lines of a failed run's output shown under the error
TAIL_WIDTH = 200           # characters kept from each of those lines
HIGH_LOAD_PER_CPU = 0.5    # 1-minute load per CPU, before the run, that means a busy machine
LOAD_SHIFT_PER_CPU = 0.25  # change in 1-minute load per CPU that counts as a shift
MIN_LOAD_SHIFT = 1.0       # a shift smaller than one busy core is not reported
FEW_ROUNDS = 3             # below this, one arm sweeps every round by chance at least half the time


class BenchError(Exception):
    """A run that cannot be scored. Carries that run's output for the report."""

    def __init__(self, message, output=None):
        super().__init__(message)
        self.output = output


def mirrored(first, second):
    """One round. Each arm gets one early slot and one late slot, so steady drift cancels."""
    return [first, second, second, first]


def build_schedule(warmup, aa_rounds, rounds):
    """Return every run in the order it will happen. No measurement can change it."""
    plan = []

    def add(phase, number, position, slot, command):
        plan.append({"seq": len(plan) + 1, "phase": phase, "round": number,
                     "position": position, "slot": slot, "command": command})

    for number in range(1, warmup + 1):
        for position, arm in enumerate(REAL_ARMS, 1):
            add("warmup", number, position, arm, arm)
    for number in range(1, aa_rounds + 1):
        for position, slot in enumerate(mirrored(*FAKE_ARMS), 1):
            add("noise", number, position, slot, "base")
    for number in range(1, rounds + 1):
        for position, slot in enumerate(mirrored(*REAL_ARMS), 1):
            add("comparison", number, position, slot, slot)
    return plan


def describe(step, warmup):
    """Name one run so an error can say exactly which run it was."""
    if step["phase"] == "warmup":
        return f"warm-up {step['round']} of {warmup} ({step['slot']} command)"
    if step["phase"] == "noise":
        return f"noise round {step['round']}, run {step['position']} of 4 ({step['slot']}, base command)"
    return f"comparison round {step['round']}, run {step['position']} of 4 ({step['slot']} command)"


def kill_group(proc):
    """Stop the shell and everything it started, so a stopped run leaves nothing behind."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        pass


def decode(raw):
    return (raw or b"").decode("utf-8", errors="replace")


def run_command(command, timeout):
    """Run one command through the shell. Return (wall seconds, combined output, exit status)."""
    options = {"shell": True, "stdin": subprocess.DEVNULL,
               "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    if os.name == "posix":
        options["start_new_session"] = True  # own process group, so a timeout can stop the whole tree
    start = time.perf_counter()
    proc = subprocess.Popen(command, **options)
    try:
        raw, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        try:
            raw, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            raw = b""
        raise BenchError(f"timed out after {timeout:g} s", decode(raw)) from None
    except BaseException:
        kill_group(proc)
        raise
    wall = time.perf_counter() - start
    return wall, decode(raw), proc.returncode


def parse_metric(pattern, output):
    """Return capture group 1 of the first match as a finite number."""
    match = pattern.search(output)
    if match is None:
        raise BenchError(f"metric regex did not match its output (regex: {pattern.pattern})", output)
    token = match.group(1)
    try:
        value = float(token)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value):
        raise BenchError(f"metric regex captured {token!r}, which is not a finite number", output)
    return value


def measure(command, pattern, timeout):
    """Run one command and return (wall seconds, metric value)."""
    wall, output, status = run_command(command, timeout)
    if status < 0:
        raise BenchError(f"was killed by signal {-status}", output)
    if status != 0:
        raise BenchError(f"exited with status {status}", output)
    return wall, (wall if pattern is None else parse_metric(pattern, output))


def summarize_round(phase, number, runs, arms):
    """Reduce the four runs of one mirrored round to two means and a relative gap."""
    first, second = arms
    means = {arm: statistics.fmean(run["value"] for run in runs if run["slot"] == arm) for arm in arms}
    if means[first] == 0:
        raise BenchError(f"{phase} round {number}: the {first} value is zero, so a relative gap has no meaning")
    return {"round": number,
            "order": [run["slot"] for run in runs],
            "values": [run["value"] for run in runs],
            "means": means,
            "gap_pct": 100.0 * (means[second] - means[first]) / abs(means[first])}


def decide(noise_gaps, gaps, higher_is_better):
    """Apply the verdict rule. Return (verdict, noise floor, median gap, rounds won, rounds lost)."""
    floor = max(abs(gap) for gap in noise_gaps)
    median = statistics.median(gaps)
    direction = 1.0 if higher_is_better else -1.0
    wins = sum(1 for gap in gaps if gap * direction > 0)
    losses = sum(1 for gap in gaps if gap * direction < 0)
    if wins == len(gaps) and abs(median) > floor:
        verdict = FASTER
    elif losses == len(gaps) and abs(median) > floor:
        verdict = SLOWER
    else:
        verdict = WITHIN_NOISE
    return verdict, floor, median, wins, losses


def requirement_met(require, verdict):
    if require == "faster":
        return verdict == FASTER
    if require == "not-slower":
        return verdict != SLOWER
    return True


def load_average():
    """Return the 1, 5 and 15 minute load averages, or None where the OS has none."""
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        return None


def load_warnings(before, after, cpus):
    """Say when other work on the machine could have moved the numbers."""
    warnings = []
    if not cpus or before is None:
        return warnings
    if before[0] >= HIGH_LOAD_PER_CPU * cpus:
        warnings.append(f"load average was {before[0]:.2f} on {cpus} CPUs before the run, "
                        "so other work was competing for the machine")
    if after is not None and abs(after[0] - before[0]) >= max(MIN_LOAD_SHIFT, LOAD_SHIFT_PER_CPU * cpus):
        warnings.append(f"load average moved from {before[0]:.2f} to {after[0]:.2f} on {cpus} CPUs during the run; "
                        "the benchmark's own work is counted in it, so if the commands do not use that many "
                        "cores, something else started or stopped")
    return warnings


def count(number, noun):
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def round_warnings(rounds):
    if rounds >= FEW_ROUNDS:
        return []
    return [f"only {count(rounds, 'comparison round')}: one arm wins every round by chance at least half "
            "the time, so the verdict rests on the noise floor alone"]


def format_value(value):
    """Six significant digits, and no exponent for a large rate such as 75305600."""
    return f"{value:.0f}" if 1e6 <= abs(value) < 1e15 else f"{value:.6g}"


def format_load(load):
    if load is None:
        return "not available on this platform"
    return " ".join(f"{value:.2f}" for value in load) + " (1, 5, 15 min)"


def tail_of(output):
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return [line[:TAIL_WIDTH] for line in lines[-TAIL_LINES:]]


def say(text=""):
    print(text, flush=True)


def protocol_line(args):
    parts = [f"warm-up {count(args.warmup, 'run')} per arm, unscored",
             f"noise {count(args.aa_rounds, 'round')} of base against base",
             f"comparison {count(args.rounds, 'round')} in order base, candidate, candidate, base",
             "verdict FASTER or SLOWER only if one arm wins every round and the median gap is above the noise floor"]
    if args.cooldown:
        parts.append(f"cooldown {args.cooldown:g} s between runs")
    if args.timeout:
        parts.append(f"timeout {args.timeout:g} s per run")
    if args.require:
        parts.append(f"require {args.require}")
    return "protocol: " + "; ".join(parts)


def run_benchmark(args, pattern):
    """Run the whole protocol, print the report and return the result record."""
    commands = {"base": args.base, "candidate": args.candidate}
    direction = "higher is better" if args.higher_is_better else "lower is better"
    metric_name = "wall-clock seconds" if pattern is None else f"capture group 1 of regex {pattern.pattern}"
    plan = build_schedule(args.warmup, args.aa_rounds, args.rounds)
    cpus = os.cpu_count()
    load_before = load_average()

    say(protocol_line(args))
    say(f"metric: {metric_name}, {direction}")
    say(f"base: {args.base}")
    say(f"candidate: {args.candidate}")
    say(f"machine: {platform.platform()}, {cpus if cpus else 'unknown number of'} CPUs")
    say(f"load average before: {format_load(load_before)}")

    runs = []
    rounds = {"noise": [], "comparison": []}
    arms = {"noise": FAKE_ARMS, "comparison": REAL_ARMS}
    headers = {
        "noise": "noise phase: base against base\nround     fake-base  fake-candidate     gap %",
        "comparison": "comparison\nround          base       candidate     gap %  better",
    }
    for (phase, number), steps in itertools.groupby(plan, key=lambda step: (step["phase"], step["round"])):
        group = []
        for step in steps:
            if runs and args.cooldown:
                time.sleep(args.cooldown)
            try:
                wall, value = measure(commands[step["command"]], pattern, args.timeout)
            except BenchError as exc:
                raise BenchError(f"{describe(step, args.warmup)}: {exc}", exc.output) from None
            record = dict(step, value=value, wall_seconds=wall)
            runs.append(record)
            group.append(record)
        if phase == "warmup":
            say(f"warm-up {number}: base {format_value(group[0]['value'])}, "
                f"candidate {format_value(group[1]['value'])} (unscored)")
            continue
        if not rounds[phase]:
            say()
            say(headers[phase])
        summary = summarize_round(phase, number, group, arms[phase])
        first, second = (summary["means"][arm] for arm in arms[phase])
        row = f"{number:>5}  {format_value(first):>12}  {format_value(second):>14}  {summary['gap_pct']:>+8.2f}"
        if phase == "comparison":
            better = (second > first) if args.higher_is_better else (second < first)
            summary["better"] = "candidate" if better else ("tie" if second == first else "base")
            row += f"  {summary['better']}"
        rounds[phase].append(summary)
        say(row)
        if phase == "noise" and number == args.aa_rounds:
            floor_so_far = max(abs(item["gap_pct"]) for item in rounds["noise"])
            say(f"noise floor: {floor_so_far:.2f}% (largest gap between two arms that are the same command)")

    verdict, floor, median, wins, losses = decide(
        [item["gap_pct"] for item in rounds["noise"]],
        [item["gap_pct"] for item in rounds["comparison"]],
        args.higher_is_better)
    load_after = load_average()
    warnings = round_warnings(args.rounds) + load_warnings(load_before, load_after, cpus)
    met = requirement_met(args.require, verdict)

    say(f"median gap: {median:+.2f}% (candidate better in {wins} of {args.rounds} rounds, {direction})")
    say()
    say(f"load average after: {format_load(load_after)}")
    for warning in warnings:
        say(f"warning: {warning}")
    if args.require:
        say(f"requirement: {args.require}, {'met' if met else 'not met'}")
    say(f"VERDICT: {verdict} (median gap {median:+.2f}%, noise floor {floor:.2f}%, "
        f"candidate better in {wins} of {args.rounds} rounds, {direction})")

    return {
        "tool": "ab_bench",
        "format": 1,
        "commands": commands,
        "metric": {"name": metric_name, "regex": args.metric_regex, "higher_is_better": args.higher_is_better},
        "protocol": {"warmup": args.warmup, "aa_rounds": args.aa_rounds, "rounds": args.rounds,
                     "round_order": mirrored(*REAL_ARMS), "timeout": args.timeout, "cooldown": args.cooldown,
                     "require": args.require},
        "runs": runs,
        "noise": {"rounds": rounds["noise"], "floor_pct": floor},
        "comparison": {"rounds": rounds["comparison"], "median_gap_pct": median,
                       "candidate_better_rounds": wins, "base_better_rounds": losses},
        "verdict": verdict,
        "requirement_met": met if args.require else None,
        "machine": {"platform": platform.platform(), "python": platform.python_version(), "cpu_count": cpus,
                    "load_average_before": load_before, "load_average_after": load_after},
        "warnings": warnings,
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="ab_bench.py", description=__doc__, epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    parser.add_argument("--base", required=True, metavar="CMD", help="shell command for the current behaviour")
    parser.add_argument("--candidate", required=True, metavar="CMD", help="shell command for the change under test")
    parser.add_argument("--rounds", type=int, default=5, metavar="N",
                        help="comparison rounds, four runs each (default 5)")
    parser.add_argument("--warmup", type=int, default=1, metavar="N",
                        help="unscored runs of each command before anything is measured (default 1)")
    parser.add_argument("--aa-rounds", type=int, default=3, metavar="N",
                        help="rounds of base against base that set the noise floor (default 3)")
    parser.add_argument("--metric-regex", metavar="REGEX",
                        help="read the metric from the command's output: capture group 1 of the first match")
    parser.add_argument("--higher-is-better", action="store_true",
                        help="the metric is a rate or a score, so bigger wins (needs --metric-regex)")
    parser.add_argument("--timeout", type=float, metavar="SECONDS",
                        help="stop a run that takes longer than this, and exit 2")
    parser.add_argument("--cooldown", type=float, default=0.0, metavar="SECONDS",
                        help="pause between runs, for a machine that needs to settle (default 0)")
    parser.add_argument("--require", choices=("faster", "not-slower"),
                        help="exit 1 unless the verdict is FASTER (faster) or is not SLOWER (not-slower)")
    parser.add_argument("--json", metavar="OUT.json",
                        help="write every raw sample, the order of runs, the noise floor, the verdict "
                             "and the machine context to this file")
    args = parser.parse_args(argv)

    for name in ("base", "candidate"):
        if not getattr(args, name).strip():
            parser.error(f"--{name} is an empty command")
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    if args.aa_rounds < 1:
        parser.error("--aa-rounds must be at least 1, because the verdict needs a noise floor")
    if args.warmup < 0:
        parser.error("--warmup cannot be negative")
    if args.timeout is not None and not (math.isfinite(args.timeout) and args.timeout > 0):
        parser.error("--timeout must be a positive number of seconds")
    if not (math.isfinite(args.cooldown) and args.cooldown >= 0):
        parser.error("--cooldown must be zero or a positive number of seconds")
    if args.higher_is_better and args.metric_regex is None:
        parser.error("--higher-is-better needs --metric-regex, because wall-clock time is always lower is better")
    pattern = None
    if args.metric_regex is not None:
        try:
            pattern = re.compile(args.metric_regex, re.MULTILINE)
        except re.error as exc:
            parser.error(f"--metric-regex is not a valid regular expression: {exc}")
        if pattern.groups < 1:
            parser.error("--metric-regex needs one capture group around the number, for example 'rate: ([0-9.]+)'")
    if args.json is not None:
        folder = os.path.dirname(os.path.abspath(args.json))
        if not os.path.isdir(folder) or not os.access(folder, os.W_OK) or os.path.isdir(args.json):
            parser.error(f"--json cannot write {args.json}")
    return args, pattern


def stop_on_signal(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    args, pattern = parse_args(argv)
    if hasattr(signal, "SIGTERM"):
        # A terminated benchmark must not leave its command running, where it would disturb the next one.
        signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        result = run_benchmark(args, pattern)
        if args.json is not None:
            try:
                with open(args.json, "w", encoding="utf-8") as handle:
                    json.dump(result, handle, indent=2)
                    handle.write("\n")
            except OSError as exc:
                raise BenchError(f"cannot write {args.json}: {exc.strerror or exc}") from None
    except BenchError as exc:
        sys.stdout.flush()
        print(f"error: {exc}", file=sys.stderr)
        if exc.output is not None:
            tail = tail_of(exc.output)
            print("  last lines of its output:" if tail else "  it printed nothing", file=sys.stderr)
            for line in tail:
                print(f"  | {line}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        sys.stdout.flush()
        print("error: interrupted before a verdict", file=sys.stderr)
        return EXIT_INTERRUPTED
    return EXIT_OK if result["requirement_met"] in (None, True) else EXIT_REQUIREMENT


if __name__ == "__main__":
    sys.exit(main())
