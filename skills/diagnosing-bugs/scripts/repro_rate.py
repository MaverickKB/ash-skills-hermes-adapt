#!/usr/bin/env python3
"""Run a command many times and report how often it fails."""

from __future__ import annotations

import argparse
import math
import os
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import List, NamedTuple, NoReturn, Optional, Sequence, Set, Tuple

PROG = "repro_rate"
USAGE = "repro_rate.py [-n RUNS] [-j JOBS] [-t SECONDS] [--save FILE] -- COMMAND [ARG ...]"
DESCRIPTION = (
    "Run COMMAND several times and report failures out of the total, the distinct exit "
    "codes, and the time per run. A run fails when it exits nonzero or times out. "
    "The output of the first failing run is saved to a file."
)
EPILOG = (
    "examples:\n"
    "  repro_rate.py -n 50 -- python3 -m unittest tests.test_sync\n"
    "  repro_rate.py -n 200 -j 8 -t 30 -- sh -c './build/app < case.txt'\n\n"
    "With -j above 1 the runs overlap, which can hide or cause failures in tests that\n"
    "share files or ports. 'First failing run' then means the lowest run number.\n\n"
    "exit status: 0 no run failed, 1 at least one run failed, 2 bad usage"
)


class RunResult(NamedTuple):
    index: int
    exit_code: Optional[int]
    seconds: float
    output: bytes


class OneLineParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, f"{PROG}: error: {message}\n")


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {text}")
    return value


def positive_seconds(text: str) -> float:
    value = float(text)
    if not value > 0 or math.isinf(value):
        raise argparse.ArgumentTypeError(f"must be a positive number of seconds, got {text}")
    return value


def build_parser() -> OneLineParser:
    parser = OneLineParser(
        prog=PROG,
        usage=USAGE,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-n", "--runs", type=positive_int, default=20, help="number of runs (default 20)")
    parser.add_argument("-j", "--jobs", type=positive_int, default=1, help="runs in parallel (default 1)")
    parser.add_argument(
        "-t",
        "--timeout",
        type=positive_seconds,
        metavar="SECONDS",
        help="per-run limit; a run that exceeds it is killed and counted as failed",
    )
    parser.add_argument(
        "--save",
        default="repro_rate_failure.log",
        metavar="FILE",
        help="where to save the first failing run's output (default %(default)s)",
    )
    return parser


def parse_args(argv: Sequence[str]) -> Tuple[argparse.Namespace, List[str]]:
    cut = argv.index("--") if "--" in argv else len(argv)
    parser = build_parser()
    args = parser.parse_args(list(argv[:cut]))
    command = list(argv[cut + 1 :])
    if not command:
        parser.error("no command given; put it after '--'")
    if shutil.which(command[0]) is None:
        parser.error(f"command not found: {command[0]}")
    return args, command


def kill_tree(process: "subprocess.Popen[bytes]") -> None:
    if os.name != "posix":
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass


class Runner:
    def __init__(self, command: Sequence[str], timeout: Optional[float]) -> None:
        self.command = list(command)
        self.timeout = timeout
        self._active: Set["subprocess.Popen[bytes]"] = set()
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    def run(self, index: int) -> Optional[RunResult]:
        if self._stopping.is_set():
            return None
        started = time.perf_counter()
        process = subprocess.Popen(
            self.command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        with self._lock:
            self._active.add(process)
        try:
            exit_code, output = self._wait(process)
        finally:
            with self._lock:
                self._active.discard(process)
        seconds = time.perf_counter() - started
        return RunResult(index, exit_code, seconds, b"" if exit_code == 0 else output)

    def _wait(self, process: "subprocess.Popen[bytes]") -> Tuple[Optional[int], bytes]:
        try:
            output, _ = process.communicate(timeout=self.timeout)
            return process.returncode, output
        except subprocess.TimeoutExpired:
            kill_tree(process)
            output, _ = process.communicate()
            return None, output

    def stop_all(self) -> None:
        self._stopping.set()
        with self._lock:
            active = list(self._active)
        for process in active:
            kill_tree(process)


def run_all(runner: Runner, runs: int, jobs: int) -> List[RunResult]:
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(runner.run, index) for index in range(1, runs + 1)]
        try:
            results = [future.result() for future in futures]
        except BaseException:
            runner.stop_all()
            raise
    return [result for result in results if result is not None]


def describe_code(code: Optional[int]) -> str:
    if code is None:
        return "timeout"
    if code >= 0:
        return str(code)
    try:
        return f"signal {-code} ({signal.Signals(-code).name})"
    except ValueError:
        return f"signal {-code}"


def exit_code_summary(results: Sequence[RunResult]) -> str:
    counts = Counter(result.exit_code for result in results)
    ordered = sorted(counts, key=lambda code: (code is None, code if code is not None else 0))
    return ", ".join(f"{describe_code(code)} x{counts[code]}" for code in ordered)


def confidence_note(failed: int, runs: int) -> str:
    if failed == 0:
        return f"note: 0 of {runs} failed. That only shows the failure rate is below about 3 in {runs}."
    if failed == runs:
        return "note: it fails every time, so one run is enough to test a fix."
    clean_runs = math.ceil(3 * runs / failed)
    return f"note: at this rate a fix needs about {clean_runs} clean runs in a row before you can trust it."


def save_output(path: str, output: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(output)


def report(command: Sequence[str], results: Sequence[RunResult], save_path: str) -> int:
    runs = len(results)
    failures = [result for result in results if result.exit_code != 0]
    timeouts = sum(1 for result in results if result.exit_code is None)
    times = [result.seconds for result in results]
    print(f"command: {shlex.join(command)}")
    print(f"runs: {runs}  failed: {len(failures)} ({100 * len(failures) / runs:.1f}%)  timeouts: {timeouts}")
    print(f"exit codes: {exit_code_summary(results)}")
    print(f"seconds per run: min {min(times):.3f}  median {statistics.median(times):.3f}  max {max(times):.3f}")
    if failures:
        first = min(failures, key=lambda result: result.index)
        save_output(save_path, first.output)
        print(f"first failing run: #{first.index}, output saved to {save_path}")
    print(confidence_note(len(failures), runs))
    return 1 if failures else 0


def main(argv: Sequence[str]) -> int:
    args, command = parse_args(argv)
    runner = Runner(command, args.timeout)
    try:
        results = run_all(runner, args.runs, args.jobs)
        return report(command, results, args.save)
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted", file=sys.stderr)
        return 130
    except OSError as error:
        print(f"{PROG}: error: {error.strerror or error}: {error.filename or command[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
