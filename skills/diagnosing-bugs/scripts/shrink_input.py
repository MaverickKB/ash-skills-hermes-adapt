#!/usr/bin/env python3
"""Shrink a failing input file to the fewest lines that still make a command fail."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, NamedTuple, NoReturn, Optional, Sequence, Tuple

PROG = "shrink_input"
PLACEHOLDER = "{}"
USAGE = "shrink_input.py INPUT [-o OUTPUT] [-t SECONDS] [--match TEXT] [--max-runs N] -- COMMAND [ARG ...]"
DESCRIPTION = (
    "Remove chunks of lines from INPUT while COMMAND still fails, then write the smallest "
    "failing input found. Write {} in COMMAND where the file path goes. A run fails when "
    "it exits nonzero. INPUT itself is never changed."
)
EPILOG = (
    "examples:\n"
    "  shrink_input.py crash.json -- ./build/parser {}\n"
    "  shrink_input.py orders.csv --match KeyError -- python3 load_orders.py {}\n\n"
    "The original input is checked twice first. If it does not fail both times, nothing\n"
    "is shrunk. Use --match so that a different error, such as a syntax error caused by\n"
    "a removed line, does not count as the same failure.\n\n"
    "Each trial file is written to a temporary directory under INPUT's own file name.\n"
    "Its path replaces {} as plain text, so quote {} if the command goes through a shell.\n\n"
    "exit status: 0 wrote a failing input, 1 refused or could not confirm, 2 bad usage"
)


class Outcome(NamedTuple):
    fails: bool
    detail: str


class OneLineParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, f"{PROG}: error: {message}\n")


class Refusal(Exception):
    pass


class RunLimitReached(Exception):
    pass


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
    parser.add_argument("input", metavar="INPUT", type=Path, help="file that makes the command fail")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="where to write the result (default INPUT with .min before its suffix)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=positive_seconds,
        default=60.0,
        metavar="SECONDS",
        help="per-run limit; a run that exceeds it is killed and does not count as failing (default 60)",
    )
    parser.add_argument("--match", metavar="TEXT", help="count a run as failing only if its output contains TEXT")
    parser.add_argument(
        "--max-runs",
        type=positive_int,
        default=1000,
        metavar="N",
        help="stop after N runs of the command (default 1000)",
    )
    return parser


def default_output(source: Path) -> Path:
    return source.with_name(f"{source.stem}.min{source.suffix}")


def parse_args(argv: Sequence[str]) -> Tuple[argparse.Namespace, List[str]]:
    cut = argv.index("--") if "--" in argv else len(argv)
    parser = build_parser()
    args = parser.parse_args(list(argv[:cut]))
    command = list(argv[cut + 1 :])
    if not command:
        parser.error("no command given; put it after '--'")
    if not any(PLACEHOLDER in part for part in command):
        parser.error("the command has no {} placeholder for the input file path")
    if PLACEHOLDER not in command[0] and shutil.which(command[0]) is None:
        parser.error(f"command not found: {command[0]}")
    if not args.input.is_file():
        parser.error(f"input file not found: {args.input}")
    args.output = args.output or default_output(args.input)
    if args.output.resolve() == args.input.resolve():
        parser.error("the output path is the input file; choose another with -o")
    return args, command


def kill_tree(process: "subprocess.Popen[bytes]") -> None:
    if os.name != "posix":
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass


def run_command(command: Sequence[str], timeout: float) -> Tuple[Optional[int], bytes]:
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode, output
    except subprocess.TimeoutExpired:
        kill_tree(process)
        output, _ = process.communicate()
        return None, output
    except BaseException:
        kill_tree(process)
        raise


class FailureCheck:
    def __init__(
        self,
        command: Sequence[str],
        trial_file: Path,
        timeout: float,
        match: Optional[str],
        max_runs: int,
    ) -> None:
        self.command = list(command)
        self.trial_file = trial_file
        self.timeout = timeout
        self.match = match.encode("utf-8") if match else None
        self.max_runs = max_runs
        self.runs = 0
        self.smallest: Optional[List[bytes]] = None
        self._seen: Dict[bytes, bool] = {}

    def judge(self, path: Path) -> Outcome:
        command = [part.replace(PLACEHOLDER, str(path)) for part in self.command]
        exit_code, output = run_command(command, self.timeout)
        if exit_code is None:
            return Outcome(False, f"timed out after {self.timeout:g} s")
        if exit_code == 0:
            return Outcome(False, "exit 0")
        if self.match is not None and self.match not in output:
            return Outcome(False, f"exit {exit_code} without the --match text")
        return Outcome(True, f"exit {exit_code}")

    def fails(self, lines: Sequence[bytes]) -> bool:
        data = b"".join(lines)
        key = hashlib.sha256(data).digest()
        if key not in self._seen:
            if self.runs >= self.max_runs:
                raise RunLimitReached()
            self.runs += 1
            self.trial_file.write_bytes(data)
            self._seen[key] = self.judge(self.trial_file).fails
        if self._seen[key] and (self.smallest is None or len(lines) < len(self.smallest)):
            self.smallest = list(lines)
        return self._seen[key]


def confirm_original(check: FailureCheck, source: Path, lines: Sequence[bytes]) -> None:
    first = check.judge(source)
    if not first.fails:
        raise Refusal(f"the command does not fail on {source} ({first.detail}); nothing to shrink")
    second = check.judge(source)
    if not second.fails:
        raise Refusal(f"the command failed once and then did not ({second.detail}); make the failure repeatable first")
    if not check.fails(lines):
        raise Refusal(
            f"the command fails on {source} but not on a copy in a temporary directory; "
            "it may depend on where the file is"
        )


def sweep(lines: List[bytes], chunk: int, check: FailureCheck) -> List[bytes]:
    start = 0
    while start < len(lines):
        candidate = lines[:start] + lines[start + chunk :]
        if check.fails(candidate):
            lines = candidate
        else:
            start += chunk
    return lines


def note_progress(before: int, after: int, runs: int) -> None:
    if after < before:
        print(f"{PROG}: {before} -> {after} lines after {runs} runs", file=sys.stderr)


def shrink(lines: List[bytes], check: FailureCheck) -> Tuple[List[bytes], bool]:
    chunk = len(lines)
    try:
        while lines:
            before = len(lines)
            lines = sweep(lines, chunk, check)
            note_progress(before, len(lines), check.runs)
            if chunk == 1 and len(lines) == before:
                break
            chunk = max(chunk // 2, 1)
    except RunLimitReached:
        return (lines if check.smallest is None else check.smallest), False
    return lines, True


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def print_summary(original: int, lines: Sequence[bytes], check: FailureCheck, output: Path, finished: bool) -> None:
    print(f"original: {plural(original, 'line')}")
    print(f"smallest failing input found: {plural(len(lines), 'line')} after {plural(check.runs, 'run')}")
    print(f"wrote: {output}")
    if not lines:
        print("note: the command fails on an empty file, so the failure may not depend on this input at all.")
    if not finished:
        print(f"note: stopped at the limit of {check.max_runs} runs. The result fails but may not be the smallest.")


def saved_file_fails(check: FailureCheck, output: Path) -> bool:
    recheck = check.judge(output)
    if not recheck.fails:
        print(
            f"{PROG}: error: the saved file did not fail on a recheck ({recheck.detail}); "
            "the command is not repeatable",
            file=sys.stderr,
        )
    return recheck.fails


def run(args: argparse.Namespace, command: Sequence[str]) -> int:
    lines = args.input.read_bytes().splitlines(keepends=True)
    with tempfile.TemporaryDirectory(prefix="shrink_input_") as workdir:
        check = FailureCheck(command, Path(workdir) / args.input.name, args.timeout, args.match, args.max_runs)
        confirm_original(check, args.input, lines)
        smallest, finished = shrink(lines, check)
        args.output.write_bytes(b"".join(smallest))
        print_summary(len(lines), smallest, check, args.output, finished)
        return 0 if saved_file_fails(check, args.output) else 1


def main(argv: Sequence[str]) -> int:
    args, command = parse_args(argv)
    try:
        return run(args, command)
    except Refusal as refusal:
        print(f"{PROG}: refused: {refusal}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted", file=sys.stderr)
        return 130
    except OSError as error:
        print(f"{PROG}: error: {error.strerror or error}: {error.filename or command[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
