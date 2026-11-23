#!/usr/bin/env python3
"""Compare two commands or git revisions across an input corpus and shrink divergences.

Runs a determinism control on the base first, then executes base and candidate on
every input in the corpus and compares exit code, stdout, and optionally stderr.
On the first multi-line divergence, --shrink delta-reduces the input to the
smallest set of lines that still produces a mismatch.

Two ways to compare:
  1. Two explicit commands:
       diff_oracle.py --base-cmd 'old.py {}' --candidate-cmd 'new.py {}' --corpus cases.txt
  2. Current git working tree against a base commit (isolated temporary worktree):
       diff_oracle.py --git-base HEAD --cmd 'python3 app.py {}' --gen-edge

Place '{}' in the command where the input file path goes; without '{}', each
input is sent on standard input.

Exit codes: 0 IDENTICAL, 1 DIVERGED, 2 usage or command launch error, 3 INCONCLUSIVE (base nondeterministic).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

EDGE_CORPUS: List[Tuple[str, str]] = [
    ("empty", ""),
    ("single-newline", "\n"),
    ("single-char", "a\n"),
    ("zero", "0\n"),
    ("one", "1\n"),
    ("negative-one", "-1\n"),
    ("whitespace", "   \t  \n"),
    ("duplicates", "alpha\nalpha\nbeta\n"),
    ("quoted-and-spaces", "hello world\n\"quoted\", 'single'\n"),
    ("unicode", "cafe\u0301 \u2603 \u4e2d\u6587\n"),
    ("large-lines", "\n".join(f"item-{i},{i}" for i in range(200)) + "\n"),
]


class CommandUnavailable(Exception):
    def __init__(self, arm: str, returncode: int):
        self.arm = arm
        self.returncode = returncode
        super().__init__(f"{arm} command unavailable (shell exit {returncode})")


def run_one(cmd_template: str, text: str, cwd: str, timeout: int, scratch: str, arm: str = "command") -> Tuple[int, bytes, bytes]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if "{}" in cmd_template:
        fd, path = tempfile.mkstemp(prefix="oracle-in-", suffix=".txt", dir=scratch)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(text.encode("utf-8"))
            cmd = cmd_template.replace("{}", path)
            proc = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, timeout=timeout, env=env)
        finally:
            if os.path.exists(path):
                os.unlink(path)
    else:
        proc = subprocess.run(cmd_template, cwd=cwd, shell=True, input=text.encode("utf-8"),
                              capture_output=True, timeout=timeout, env=env)
    if proc.returncode in (126, 127):
        raise CommandUnavailable(arm, proc.returncode)
    return proc.returncode, proc.stdout, proc.stderr


def load_corpus(args: argparse.Namespace) -> List[Tuple[str, str]]:
    cases: List[Tuple[str, str]] = []
    if args.gen_edge:
        cases.extend(EDGE_CORPUS)
    if args.corpus:
        if os.path.isdir(args.corpus):
            for name in sorted(os.listdir(args.corpus)):
                full = os.path.join(args.corpus, name)
                if os.path.isfile(full):
                    with open(full, encoding="utf-8", errors="replace") as handle:
                        cases.append((name, handle.read()))
        elif os.path.isfile(args.corpus):
            with open(args.corpus, encoding="utf-8", errors="replace") as handle:
                if args.corpus.endswith(".jsonl"):
                    for idx, raw in enumerate(handle, 1):
                        raw = raw.strip()
                        if not raw:
                            continue
                        obj = json.loads(raw)
                        cid = str(obj.get("id", f"line-{idx}"))
                        cases.append((cid, str(obj["input"])))
                elif args.whole_file:
                    cases.append((os.path.basename(args.corpus), handle.read()))
                else:
                    for idx, line in enumerate(handle.read().splitlines(), 1):
                        cases.append((f"line-{idx}", line + "\n"))
        else:
            raise RuntimeError(f"corpus not found: {args.corpus}")
    if not cases:
        raise RuntimeError("no inputs to compare; pass --corpus PATH or --gen-edge")
    return cases


def outputs_match(
    base_res: Tuple[int, bytes, bytes],
    cand_res: Tuple[int, bytes, bytes],
    check_stderr: bool,
) -> Tuple[bool, str]:
    if base_res[0] != cand_res[0]:
        return False, f"exit code {base_res[0]} != {cand_res[0]}"
    if base_res[1] != cand_res[1]:
        idx = first_diff_index(base_res[1], cand_res[1])
        return False, f"stdout differs at byte {idx}"
    if check_stderr and base_res[2] != cand_res[2]:
        idx = first_diff_index(base_res[2], cand_res[2])
        return False, f"stderr differs at byte {idx}"
    return True, "match"


def first_diff_index(a: bytes, b: bytes) -> int:
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


def shrink_divergence(
    text: str,
    base_cmd: str,
    cand_cmd: str,
    base_cwd: str,
    cand_cwd: str,
    check_stderr: bool,
    timeout: int,
    scratch: str,
    max_trials: int = 120,
) -> Tuple[str, int]:
    lines = text.splitlines(keepends=True)
    if len(lines) <= 1:
        return text, 0
    trials = 0

    def diverges(candidate_lines: List[str]) -> bool:
        nonlocal trials
        if trials >= max_trials:
            return False
        trials += 1
        payload = "".join(candidate_lines)
        try:
            b_res = run_one(base_cmd, payload, base_cwd, timeout, scratch, "base")
            c_res = run_one(cand_cmd, payload, cand_cwd, timeout, scratch, "candidate")
        except subprocess.TimeoutExpired:
            return False
        same, _ = outputs_match(b_res, c_res, check_stderr)
        return not same

    granularity = 2
    while len(lines) > 1 and trials < max_trials:
        chunk = max(1, len(lines) // granularity)
        reduced = False
        i = 0
        while i < len(lines) and trials < max_trials:
            without = lines[:i] + lines[i + chunk :]
            if without and diverges(without):
                lines = without
                granularity = max(2, granularity - 1)
                reduced = True
                break
            i += chunk
        if not reduced:
            if granularity >= len(lines):
                break
            granularity = min(len(lines), granularity * 2)
    return "".join(lines), trials


def preview(data: bytes, limit: int = 120) -> str:
    text = data.decode("utf-8", errors="replace")
    return repr(text[:limit] + ("..." if len(text) > limit else ""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-cmd", help="command for the base implementation")
    parser.add_argument("--candidate-cmd", help="command for the candidate implementation")
    parser.add_argument("--git-base", help="git revision for base (runs --cmd in a temp worktree vs --repo)")
    parser.add_argument("--cmd", help="command run in both base worktree and current repo when using --git-base")
    parser.add_argument("--repo", default=".", help="git repo path for --git-base (default: .)")
    parser.add_argument("--corpus", help="file, .jsonl (id/input), or directory of input files")
    parser.add_argument("--whole-file", action="store_true", help="treat --corpus file as one multi-line input")
    parser.add_argument("--gen-edge", action="store_true", help="include built-in adversarial edge-case inputs")
    parser.add_argument("--check-stderr", action="store_true", help="require stderr to match too")
    parser.add_argument("--shrink", action="store_true", help="delta-shrink the first multi-line divergence")
    parser.add_argument("--timeout", type=int, default=30, help="seconds per invocation (default: 30)")
    parser.add_argument("--json", dest="json_out", help="write structured result to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.git_base:
        if not args.cmd:
            print("diff_oracle.py: error: --git-base requires --cmd", file=sys.stderr)
            return 2
        if args.base_cmd or args.candidate_cmd:
            print("diff_oracle.py: error: do not mix --git-base with --base-cmd/--candidate-cmd", file=sys.stderr)
            return 2
        base_cmd = cand_cmd = args.cmd
        cand_cwd = os.path.abspath(args.repo)
        base_worktree: Optional[str] = tempfile.mkdtemp(prefix="diff-oracle-base-")
    else:
        if not args.base_cmd or not args.candidate_cmd:
            print("diff_oracle.py: error: pass --base-cmd and --candidate-cmd, or --git-base and --cmd", file=sys.stderr)
            return 2
        base_cmd, cand_cmd = args.base_cmd, args.candidate_cmd
        cand_cwd = os.getcwd()
        base_worktree = None

    try:
        cases = load_corpus(args)
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        if base_worktree and os.path.exists(base_worktree):
            shutil.rmtree(base_worktree, ignore_errors=True)
        print(f"diff_oracle.py: error: {exc}", file=sys.stderr)
        return 2

    if base_worktree is not None:
        proc = subprocess.run(
            ["git", "worktree", "add", "--detach", base_worktree, args.git_base],
            cwd=cand_cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            shutil.rmtree(base_worktree, ignore_errors=True)
            print(f"diff_oracle.py: error: {proc.stderr.strip()}", file=sys.stderr)
            return 2
        base_cwd = base_worktree
    else:
        base_cwd = cand_cwd

    scratch = tempfile.mkdtemp(prefix="diff-oracle-io-")
    try:
        first_id, first_payload = cases[0]
        cid = first_id
        try:
            r1 = run_one(base_cmd, first_payload, base_cwd, args.timeout, scratch, "base")
            r2 = run_one(base_cmd, first_payload, base_cwd, args.timeout, scratch, "base")
        except subprocess.TimeoutExpired:
            print(f"diff_oracle.py: error: base timed out on control case {first_id}", file=sys.stderr)
            return 2
        stable, why = outputs_match(r1, r2, args.check_stderr)
        if not stable:
            print(f"control: base disagreed with itself on {first_id} ({why})")
            print(f"ORACLE: INCONCLUSIVE (base is nondeterministic on {first_id})")
            if args.json_out:
                with open(args.json_out, "w", encoding="utf-8") as handle:
                    json.dump({"verdict": "INCONCLUSIVE", "case": first_id, "reason": why}, handle, indent=2)
            return 3

        mismatches: List[Dict[str, object]] = []
        for idx, (cid, payload) in enumerate(cases):
            try:
                b_res = r1 if idx == 0 else run_one(base_cmd, payload, base_cwd, args.timeout, scratch, "base")
                c_res = run_one(cand_cmd, payload, cand_cwd, args.timeout, scratch, "candidate")
            except subprocess.TimeoutExpired:
                mismatches.append({"id": cid, "reason": f"timed out after {args.timeout}s"})
                continue
            same, reason = outputs_match(b_res, c_res, args.check_stderr)
            if not same:
                entry: Dict[str, object] = {
                    "id": cid,
                    "reason": reason,
                    "base_exit": b_res[0],
                    "candidate_exit": c_res[0],
                    "base_stdout": preview(b_res[1]),
                    "candidate_stdout": preview(c_res[1]),
                }
                if args.shrink and not mismatches and len(payload.splitlines()) > 1:
                    minimal, trials = shrink_divergence(
                        payload, base_cmd, cand_cmd, base_cwd, cand_cwd, args.check_stderr, args.timeout, scratch
                    )
                    entry["shrunk_input"] = minimal
                    entry["shrink_trials"] = trials
                    entry["original_lines"] = len(payload.splitlines())
                    entry["shrunk_lines"] = len(minimal.splitlines())
                mismatches.append(entry)

        total = len(cases)
        print(f"control: base deterministic on {first_id} (ok)")
        for item in mismatches[:10]:
            print(f"MISMATCH {item['id']}: {item['reason']}")
            if "base_stdout" in item:
                print(f"  base:      exit {item['base_exit']} stdout={item['base_stdout']}")
                print(f"  candidate: exit {item['candidate_exit']} stdout={item['candidate_stdout']}")
            if "shrunk_input" in item:
                print(f"  shrunk:    {item['original_lines']} -> {item['shrunk_lines']} lines: {item['shrunk_input']!r}")

        verdict = "IDENTICAL" if not mismatches else "DIVERGED"
        summary = f"ORACLE: IDENTICAL ({total} of {total})" if not mismatches else f"ORACLE: DIVERGED ({len(mismatches)} of {total})"
        print(summary)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump({"verdict": verdict, "total": total, "mismatches": mismatches}, handle, indent=2)
        return 0 if not mismatches else 1
    except CommandUnavailable as exc:
        print(f"ORACLE: ERROR ({cid}: {exc})", file=sys.stderr)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump({"verdict": "ERROR", "case": cid, "arm": exc.arm,
                           "returncode": exc.returncode, "reason": str(exc)}, handle, indent=2)
        return 2
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if base_worktree is not None:
            subprocess.run(["git", "worktree", "remove", "--force", base_worktree], cwd=cand_cwd, capture_output=True)
            if os.path.exists(base_worktree):
                shutil.rmtree(base_worktree, ignore_errors=True)
            subprocess.run(["git", "worktree", "prune"], cwd=cand_cwd, capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
