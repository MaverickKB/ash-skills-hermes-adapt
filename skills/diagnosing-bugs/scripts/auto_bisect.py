#!/usr/bin/env python3
"""Automated git bisect in an isolated worktree with endpoint and flake guards.

Validates that --good passes and --bad fails BEFORE bisecting, supports
--build-cmd (marking unbuildable commits as exit 125 skip) and --repeats N
for flaky regressions, and leaves the main working tree untouched.

Exit codes: 0 culprit found, 1 inconclusive, 2 usage or invalid endpoint.
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


def run_git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


def run_shell(cmd: str, cwd: str, timeout: int) -> Tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout, env=env)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout}s"


def evaluate_commit(
    worktree: str,
    cmd: str,
    build_cmd: Optional[str],
    repeats: int,
    timeout: int,
) -> Tuple[int, str]:
    """Return 0 (good), 1 (bad), or 125 (skip/unbuildable)."""
    if build_cmd:
        b_rc, b_out = run_shell(build_cmd, worktree, timeout)
        if b_rc != 0:
            return 125, f"build failed (exit {b_rc}): {b_out[-120:]}"
    for attempt in range(1, repeats + 1):
        rc, out = run_shell(cmd, worktree, timeout)
        if rc == 125:
            return 125, f"check requested skip on run {attempt}/{repeats}: {out[-120:]}"
        if rc != 0:
            return 1, f"failed on run {attempt}/{repeats} (exit {rc}): {out[-120:]}"
    return 0, f"passed {repeats}/{repeats}"


def remove_worktree(repo: str, worktree: str) -> None:
    run_git(repo, "worktree", "remove", "--force", worktree)
    if os.path.exists(worktree):
        shutil.rmtree(worktree, ignore_errors=True)


def find_candidates(worktree: str, good: str, bad: str, args: argparse.Namespace,
                    trials: List[Dict[str, object]]) -> Tuple[List[str], str]:
    def probe(sha: str) -> int:
        checkout = run_git(worktree, "checkout", "--detach", sha)
        if checkout.returncode:
            raise RuntimeError(f"could not check out {sha[:12]}: {checkout.stderr.strip()}")
        rc, detail = evaluate_commit(worktree, args.cmd, args.build_cmd, args.repeats, args.timeout)
        label = {0: "good", 1: "bad", 125: "skip"}[rc]
        trials.append({"commit": sha[:12], "status": label, "detail": detail})
        print(f"  step {len(trials):>2}  {sha[:8]}  {label:<4}  {detail}")
        return rc

    bases = run_git(worktree, "merge-base", "--all", good, bad)
    if bases.returncode == 1:
        return [], "the endpoints have no common ancestor"
    if bases.returncode:
        raise RuntimeError(bases.stderr.strip())
    for sha in bases.stdout.splitlines():
        if sha != good and (sha == bad or probe(sha) != 0):
            return [], f"no verified good baseline at merge base {sha[:12]}"

    good_bounds = [good]
    skipped = set()
    while True:
        ranked = run_git(worktree, "rev-list", "--bisect-all", bad, "--not", *good_bounds)
        if ranked.returncode:
            raise RuntimeError(ranked.stderr.strip())
        candidates = [line.split()[0] for line in ranked.stdout.splitlines()]
        if candidates == [bad]:
            return candidates, ""
        sha = next((oid for oid in candidates if oid != bad and oid not in skipped), None)
        if sha is None:
            return candidates, "skipped revisions leave the first bad commit ambiguous"
        rc = probe(sha)
        if rc == 125:
            skipped.add(sha)
        elif rc == 0:
            good_bounds.append(sha)
        else:
            bad = sha


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", default=".", help="git repository root (default: .)")
    parser.add_argument("--good", required=True, help="known good commit/tag")
    parser.add_argument("--bad", default="HEAD", help="known bad commit/tag (default: HEAD)")
    parser.add_argument("--cmd", required=True, help="check command (exit 0 = good, 125 = skip, other nonzero = bad)")
    parser.add_argument("--build-cmd", help="optional build command (nonzero exit marks commit as skipped)")
    parser.add_argument("--repeats", type=int, default=1, help="runs per commit to catch flaky bugs (default: 1)")
    parser.add_argument("--timeout", type=int, default=60, help="seconds per command run (default: 60)")
    parser.add_argument("--json", dest="json_out", help="write structured bisect report to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.repeats < 1:
        print("auto_bisect.py: error: --repeats must be at least 1", file=sys.stderr)
        return 2
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"auto_bisect.py: error: not a directory: {repo}", file=sys.stderr)
        return 2

    good_rev = run_git(repo, "rev-parse", "--verify", f"{args.good}^{{commit}}")
    bad_rev = run_git(repo, "rev-parse", "--verify", f"{args.bad}^{{commit}}")
    if good_rev.returncode != 0 or bad_rev.returncode != 0:
        print("auto_bisect.py: error: invalid --good or --bad revision", file=sys.stderr)
        return 2
    good_sha = good_rev.stdout.strip()
    bad_sha = bad_rev.stdout.strip()

    worktree = tempfile.mkdtemp(prefix="auto-bisect-")
    try:
        add = run_git(repo, "worktree", "add", "--detach", worktree, bad_sha)
        if add.returncode != 0:
            print(f"auto_bisect.py: error: {add.stderr.strip()}", file=sys.stderr)
            return 2

        bad_rc, bad_detail = evaluate_commit(worktree, args.cmd, args.build_cmd, args.repeats, args.timeout)
        if bad_rc != 1:
            print(f"auto_bisect.py: error: bad endpoint {args.bad} ({bad_sha[:8]}) did not fail ({bad_detail})", file=sys.stderr)
            return 2

        co = run_git(worktree, "checkout", "--detach", good_sha)
        if co.returncode != 0:
            print(f"auto_bisect.py: error: {co.stderr.strip()}", file=sys.stderr)
            return 2
        good_rc, good_detail = evaluate_commit(worktree, args.cmd, args.build_cmd, args.repeats, args.timeout)
        if good_rc != 0:
            print(f"auto_bisect.py: error: good endpoint {args.good} ({good_sha[:8]}) did not pass ({good_detail})", file=sys.stderr)
            return 2

        print(f"endpoints verified: good {good_sha[:8]} ({good_detail}), bad {bad_sha[:8]} ({bad_detail})")

        trials: List[Dict[str, object]] = []
        candidates, reason = find_candidates(worktree, good_sha, bad_sha, args, trials)
        culprit = candidates[0] if len(candidates) == 1 and not reason else None
        verdict = "FOUND" if culprit else "INCONCLUSIVE"
        subject = None
        if culprit:
            show = run_git(worktree, "show", "--stat", "--format=%s", culprit)
            if show.returncode:
                raise RuntimeError(show.stderr.strip())
            subject, _, stat = show.stdout.partition("\n")
            print(f"BISECT: FOUND {culprit[:12]} ({subject})")
            if stat.strip():
                print(stat.strip())
        else:
            print(f"BISECT: INCONCLUSIVE ({reason})")
            for candidate in candidates:
                print(f"  candidate {candidate}")
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "verdict": verdict,
                        "good": good_sha,
                        "bad": bad_sha,
                        "culprit": culprit,
                        "subject": subject,
                        "candidates": candidates,
                        "reason": reason,
                        "trials": trials,
                    },
                    handle,
                    indent=2,
                )
        return 0 if culprit else 1
    except RuntimeError as exc:
        print(f"auto_bisect.py: error: {exc}", file=sys.stderr)
        return 2
    finally:
        remove_worktree(repo, worktree)


if __name__ == "__main__":
    sys.exit(main())
