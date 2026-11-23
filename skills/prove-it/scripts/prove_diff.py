#!/usr/bin/env python3
"""Prove a git working-tree change against a base commit in an isolated worktree.

Runs the verification command on the current working tree, then runs a negative
control in a temporary copy or git worktree without modifying the repository:

  --mode fix       runs the same check (with any new or updated test files kept)
                   against --base and checks for the predicted failure diagnostic.
  --mode refactor  confirms the check passes on both --base and the current tree,
                   then injects a small fault on a changed line in a temporary
                   copy and confirms the check fails.

Exit codes: 0 PROVEN, 1 NOT PROVEN, 2 usage or environment error.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Sequence, Tuple

TEST_DIR_PREFIXES = ("tests/", "test/", "__tests__/")
TEST_SUFFIXES = (
    "_test.py",
    "_test.go",
    "_test.rs",
    ".test.js",
    ".test.ts",
    ".test.jsx",
    ".test.tsx",
    ".spec.js",
    ".spec.ts",
)


def run_git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )


def run_check(cmd: str, cwd: str, timeout: int) -> Dict[str, object]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        elapsed = round(time.monotonic() - start, 3)
        output = (proc.stdout + proc.stderr).strip()
        return {
            "returncode": proc.returncode,
            "timed_out": False,
            "elapsed_s": elapsed,
            "output": output,
            "excerpt": last_lines(output, 12),
        }
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.monotonic() - start, 3)
        out = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + (
            (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        )
        return {
            "returncode": -1,
            "timed_out": True,
            "elapsed_s": elapsed,
            "output": out.strip(),
            "excerpt": f"timed out after {timeout}s",
        }


def last_lines(text: str, limit: int = 12) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) <= limit:
        return "\n".join(lines)
    return "\n".join(lines[-limit:])


def control_interruption(result: Dict[str, object]) -> Optional[str]:
    if result["timed_out"]:
        return "control timed out before demonstrating the expected failure"
    status = int(result["returncode"])
    if status < 0 or status in (126, 127):
        return f"control command could not complete (exit {status})"
    return None


def failure_diagnostics(output: str) -> str:
    pytest_errors = re.findall(r"^E {3,}(.+)$", output, re.MULTILINE)
    if pytest_errors:
        return "\n".join(pytest_errors)
    # Python tracebacks echo source lines, including assertion messages that never ran.
    return re.sub(r'^  File "[^\n]+", line \d+[^\n]*\n(?:[ \t]+[^\n]*\n)*', "", output, flags=re.MULTILINE)


def is_test_path(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    base = os.path.basename(norm)
    if norm.startswith(TEST_DIR_PREFIXES) or "/tests/" in norm or "/test/" in norm:
        return True
    if base.startswith("test_") and base.endswith(".py"):
        return True
    return base.endswith(TEST_SUFFIXES)


def collect_changes(repo: str, base: str) -> Tuple[List[str], List[str], str]:
    diff = run_git(repo, "diff", "--name-only", base)
    if diff.returncode != 0:
        raise RuntimeError(diff.stderr.strip() or f"git diff against {base} failed")
    tracked = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    untracked_proc = run_git(repo, "ls-files", "--others", "--exclude-standard")
    untracked = [line.strip() for line in untracked_proc.stdout.splitlines() if line.strip()]
    raw_diff = run_git(repo, "diff", base)
    digest = hashlib.sha256(raw_diff.stdout.encode("utf-8", errors="replace"))
    for rel in sorted(untracked):
        full = os.path.join(repo, rel)
        if os.path.isfile(full):
            digest.update(rel.encode("utf-8"))
            with open(full, "rb") as handle:
                digest.update(handle.read())
    return sorted(set(tracked)), sorted(set(untracked)), digest.hexdigest()[:16]


def select_test_files(
    tracked: Sequence[str],
    untracked: Sequence[str],
    explicit: Sequence[str],
) -> List[str]:
    all_paths = sorted(set(tracked) | set(untracked))
    if not explicit:
        return [p for p in all_paths if is_test_path(p)]
    chosen: List[str] = []
    for rel in all_paths:
        norm = rel.replace("\\", "/")
        for pat in explicit:
            pnorm = pat.replace("\\", "/").rstrip("/")
            if norm == pnorm or norm.startswith(pnorm + "/") or fnmatch.fnmatch(norm, pnorm):
                chosen.append(rel)
                break
    return sorted(set(chosen))


def changed_lines_by_file(repo: str, base: str) -> Dict[str, List[int]]:
    proc = run_git(repo, "diff", "--unified=0", base)
    if proc.returncode != 0:
        return {}
    result: Dict[str, List[int]] = {}
    current_file: Optional[str] = None
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            result.setdefault(current_file, [])
        elif line.startswith("@@ ") and current_file:
            parts = line.split(" ")
            for token in parts:
                if token.startswith("+"):
                    body = token[1:]
                    if "," in body:
                        start_s, count_s = body.split(",", 1)
                        start, count = int(start_s), int(count_s)
                    else:
                        start, count = int(body), 1
                    if count > 0:
                        result[current_file].extend(range(start, start + count))
                    break
    return result


def build_python_mutants(source: str, target_lines: Sequence[int]) -> List[Tuple[int, str, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    line_set = set(target_lines)
    lines = source.splitlines(keepends=True)
    mutants: List[Tuple[int, str, str]] = []
    seen = set()

    cmp_swaps = {
        ast.Eq: ("==", "!="),
        ast.NotEq: ("!=", "=="),
        ast.Lt: ("<", "<="),
        ast.LtE: ("<=", "<"),
        ast.Gt: (">", ">="),
        ast.GtE: (">=", ">"),
    }
    bin_swaps = {
        ast.Add: ("+", "-"),
        ast.Sub: ("-", "+"),
        ast.Mult: ("*", "+"),
    }

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None or lineno not in line_set or lineno > len(lines):
            continue
        orig = lines[lineno - 1]
        candidates: List[Tuple[str, str]] = []
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            pair = cmp_swaps.get(type(node.ops[0]))
            if pair and pair[0] in orig:
                candidates.append((f"{pair[0]} -> {pair[1]}", orig.replace(pair[0], pair[1], 1)))
        elif isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And) and " and " in orig:
                candidates.append(("and -> or", orig.replace(" and ", " or ", 1)))
            elif isinstance(node.op, ast.Or) and " or " in orig:
                candidates.append(("or -> and", orig.replace(" or ", " and ", 1)))
        elif isinstance(node, ast.BinOp):
            pair = bin_swaps.get(type(node.op))
            if pair and f" {pair[0]} " in orig:
                candidates.append((f"{pair[0]} -> {pair[1]}", orig.replace(f" {pair[0]} ", f" {pair[1]} ", 1)))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            old_b = "True" if node.value else "False"
            new_b = "False" if node.value else "True"
            if old_b in orig:
                candidates.append((f"{old_b} -> {new_b}", orig.replace(old_b, new_b, 1)))
        elif isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                indent = orig[: len(orig) - len(orig.lstrip())]
                newline = "\n" if orig.endswith("\n") else ""
                candidates.append(("return expr -> return None", f"{indent}return None{newline}"))

        for label, mutated_line in candidates:
            if mutated_line == orig:
                continue
            new_src = "".join(lines[: lineno - 1] + [mutated_line] + lines[lineno:])
            try:
                ast.parse(new_src)
            except SyntaxError:
                continue
            key = (lineno, mutated_line)
            if key not in seen:
                seen.add(key)
                mutants.append((lineno, label, new_src))
    return mutants


def build_lexical_mutants(source: str, target_lines: Sequence[int]) -> List[Tuple[int, str, str]]:
    line_set = set(target_lines)
    lines = source.splitlines(keepends=True)
    mutants: List[Tuple[int, str, str]] = []
    swaps = [
        ("===", "!=="), ("!==", "==="), ("==", "!="), ("!=", "=="),
        ("<=", "<"), (">=", ">"), ("&&", "||"), ("||", "&&"),
        ("true", "false"), ("false", "true"), (" + ", " - "), (" - ", " + "),
    ]
    for idx, orig in enumerate(lines, 1):
        if idx not in line_set:
            continue
        stripped = orig.strip()
        if not stripped or stripped.startswith(("//", "/*", "*", "import ", "package ", "use ")):
            continue
        for old, new in swaps:
            if old in orig:
                mut_line = orig.replace(old, new, 1)
                if mut_line != orig:
                    new_src = "".join(lines[: idx - 1] + [mut_line] + lines[idx:])
                    mutants.append((idx, f"{old.strip()} -> {new.strip()}", new_src))
                    break
    return mutants


def copy_working_tree(repo: str, dest: str) -> None:
    def ignore(_src: str, names: List[str]) -> List[str]:
        skip = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store"}
        return [n for n in names if n in skip or n.endswith(".pyc")]

    shutil.copytree(repo, dest, dirs_exist_ok=True, symlinks=True, ignore=ignore)


def create_base_worktree(repo: str, base: str, dest: str) -> None:
    proc = run_git(repo, "worktree", "add", "--detach", dest, base)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"could not create git worktree at {base}")


def remove_base_worktree(repo: str, dest: str) -> None:
    run_git(repo, "worktree", "remove", "--force", dest)
    if os.path.exists(dest):
        shutil.rmtree(dest, ignore_errors=True)
    run_git(repo, "worktree", "prune")


def run_fix_proof(
    repo: str,
    base: str,
    cmd: str,
    keep_tests: Sequence[str],
    expect_failure: Optional[str],
    timeout: int,
) -> Tuple[bool, Dict[str, object]]:
    tracked, untracked, diff_sha = collect_changes(repo, base)
    test_files = select_test_files(tracked, untracked, keep_tests)
    current = run_check(cmd, repo, timeout)
    info: Dict[str, object] = {
        "mode": "fix",
        "base": base,
        "diff_sha256": diff_sha,
        "command": cmd,
        "changed_files": tracked,
        "untracked_files": untracked,
        "kept_test_files": test_files,
        "expected_failure": expect_failure,
        "current": current,
    }
    if current["returncode"] != 0:
        info["reason"] = f"check failed on current tree (exit {current['returncode']})"
        return False, info

    tmp = tempfile.mkdtemp(prefix="prove-diff-base-")
    try:
        create_base_worktree(repo, base, tmp)
        for rel in test_files:
            src = os.path.join(repo, rel)
            dst = os.path.join(tmp, rel)
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        control = run_check(cmd, tmp, timeout)
    finally:
        remove_base_worktree(repo, tmp)

    info["control"] = control
    if control["returncode"] == 0:
        info["reason"] = "check still passed on base without the change"
        return False, info
    interrupted = control_interruption(control)
    if interrupted:
        info["reason"] = interrupted
        return False, info
    if not expect_failure or not expect_failure.strip():
        info["reason"] = "base failed; supply the predicted diagnostic with --expect-failure to verify why"
        return False, info
    if expect_failure in str(current["output"]):
        info["reason"] = "expected failure text also appeared in the successful check"
        return False, info
    if expect_failure not in failure_diagnostics(str(control["output"])):
        info["reason"] = f"base failed, but output did not contain expected text {expect_failure!r}"
        return False, info
    info["reason"] = f"passes now and fails on {base} with the predicted diagnostic {expect_failure!r}"
    return True, info


def run_refactor_proof(
    repo: str,
    base: str,
    cmd: str,
    fault_spec: Optional[Tuple[str, str, str]],
    timeout: int,
) -> Tuple[bool, Dict[str, object]]:
    tracked, untracked, diff_sha = collect_changes(repo, base)
    current = run_check(cmd, repo, timeout)
    info: Dict[str, object] = {
        "mode": "refactor",
        "base": base,
        "diff_sha256": diff_sha,
        "command": cmd,
        "changed_files": tracked,
        "untracked_files": untracked,
        "current": current,
    }
    if current["returncode"] != 0:
        info["reason"] = f"check failed on current tree (exit {current['returncode']})"
        return False, info

    base_tmp = tempfile.mkdtemp(prefix="prove-diff-base-")
    try:
        create_base_worktree(repo, base, base_tmp)
        base_run = run_check(cmd, base_tmp, timeout)
    finally:
        remove_base_worktree(repo, base_tmp)

    info["base_run"] = base_run
    if base_run["returncode"] != 0:
        info["reason"] = f"check failed on base {base} (exit {base_run['returncode']}); not a behavior-preserving baseline"
        return False, info

    control_result = run_refactor_fault_control(repo, base, cmd, tracked, fault_spec, timeout)
    info["control"] = control_result
    if not control_result.get("killed"):
        info["reason"] = str(control_result.get("detail", "fault control did not fail"))
        return False, info

    info["reason"] = f"passes on {base} and current tree; fault control failed ({control_result['mutation']})"
    return True, info


def run_refactor_fault_control(
    repo: str,
    base: str,
    cmd: str,
    tracked: Sequence[str],
    fault_spec: Optional[Tuple[str, str, str]],
    timeout: int,
) -> Dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="prove-diff-fault-") as scratch:
        copy_working_tree(repo, scratch)
        clean = run_check(cmd, scratch, timeout)
        if clean["returncode"] != 0:
            return {"killed": False, "detail": "check failed in the clean scratch copy before fault injection",
                    "returncode": clean["returncode"], "excerpt": clean["excerpt"]}
        if fault_spec is not None:
            rel_file, old_text, new_text = fault_spec
            target = os.path.join(scratch, rel_file)
            if not os.path.isfile(target):
                return {"killed": False, "detail": f"fault file not found: {rel_file}"}
            with open(target, encoding="utf-8") as handle:
                content = handle.read()
            if old_text not in content:
                return {"killed": False, "detail": f"fault target text not found in {rel_file}"}
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(content.replace(old_text, new_text, 1))
            res = run_check(cmd, scratch, timeout)
            interrupted = control_interruption(res)
            if interrupted:
                return {"killed": False, "detail": interrupted, "excerpt": res["excerpt"]}
            return {
                "killed": res["returncode"] != 0,
                "mutation": f"{rel_file}: {old_text!r} -> {new_text!r}",
                "returncode": res["returncode"],
                "excerpt": res["excerpt"],
                "detail": "manual fault survived" if res["returncode"] == 0 else "manual fault failed check",
            }

        changed_map = changed_lines_by_file(repo, base)
        text_exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".zig")
        candidates = [f for f in tracked if (f.endswith(".py") or f.endswith(text_exts)) and not is_test_path(f)]
        attempts = 0
        for rel in candidates:
            target = os.path.join(scratch, rel)
            if not os.path.isfile(target):
                continue
            with open(target, encoding="utf-8") as handle:
                original = handle.read()
            line_nums = changed_map.get(rel) or list(range(1, len(original.splitlines()) + 1))
            mutants = build_python_mutants(original, line_nums) if rel.endswith(".py") else build_lexical_mutants(original, line_nums)
            for lineno, label, mutated in mutants[:5]:
                attempts += 1
                with open(target, "w", encoding="utf-8") as handle:
                    handle.write(mutated)
                try:
                    res = run_check(cmd, scratch, timeout)
                finally:
                    with open(target, "w", encoding="utf-8") as handle:
                        handle.write(original)
                interrupted = control_interruption(res)
                if interrupted:
                    return {"killed": False, "detail": interrupted, "excerpt": res["excerpt"]}
                if res["returncode"] != 0:
                    return {
                        "killed": True,
                        "mutation": f"{rel}:{lineno} ({label})",
                        "returncode": res["returncode"],
                        "excerpt": res["excerpt"],
                        "attempts": attempts,
                    }
        if attempts == 0:
            return {
                "killed": False,
                "detail": "no mutable source lines in diff; pass --fault-file/--fault-old/--fault-new",
            }
        return {
            "killed": False,
            "attempts": attempts,
            "detail": f"all {attempts} injected fault(s) survived; check may not exercise the refactored lines",
        }


def print_report(proven: bool, info: Dict[str, object]) -> None:
    print(f"mode: {info['mode']}")
    print(f"base: {info['base']} (diff sha256: {info['diff_sha256']})")
    print(f"command: {info['command']}")
    cur = info["current"]
    assert isinstance(cur, dict)
    print(f"current: exit {cur['returncode']} ({cur['elapsed_s']}s)")
    if cur.get("excerpt"):
        print(f"  {cur['excerpt']}")
    if "base_run" in info:
        br = info["base_run"]
        assert isinstance(br, dict)
        print(f"base: exit {br['returncode']} ({br['elapsed_s']}s)")
    if "control" in info:
        ctrl = info["control"]
        assert isinstance(ctrl, dict)
        if info["mode"] == "fix":
            kept = info.get("kept_test_files") or []
            print(f"control ({info['base']}, kept tests: {kept}): exit {ctrl['returncode']} ({ctrl['elapsed_s']}s)")
            if ctrl.get("excerpt"):
                print(f"  {ctrl['excerpt']}")
        else:
            mut = ctrl.get("mutation", "none")
            print(f"control (fault {mut}): {'failed as expected' if ctrl.get('killed') else 'SURVIVED'}")
    verdict = "PROVEN" if proven else "NOT PROVEN"
    print(f"PROOF: {verdict} ({info['reason']})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cmd", required=True, help="shell command that verifies the work")
    parser.add_argument("--repo", default=".", help="path to the git repository (default: .)")
    parser.add_argument("--base", default="HEAD", help="base git revision to compare against (default: HEAD)")
    parser.add_argument("--mode", choices=("fix", "refactor"), default="fix", help="proof mode (default: fix)")
    parser.add_argument(
        "--keep-tests",
        action="append",
        default=[],
        help="relative path or glob of test files to carry onto --base in fix mode (repeatable)",
    )
    parser.add_argument(
        "--expect-failure",
        help="predicted diagnostic required for PROVEN in fix mode; must be absent from the passing run",
    )
    parser.add_argument("--fault-file", help="relative file path for an explicit fault control in refactor mode")
    parser.add_argument("--fault-old", help="text to replace in --fault-file for refactor fault control")
    parser.add_argument("--fault-new", help="replacement text in --fault-file for refactor fault control")
    parser.add_argument("--timeout", type=int, default=120, help="seconds per check run (default: 120)")
    parser.add_argument("--json", dest="json_out", help="write structured proof receipt to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"prove_diff.py: error: not a directory: {repo}", file=sys.stderr)
        return 2
    rev = run_git(repo, "rev-parse", "--verify", args.base)
    if rev.returncode != 0:
        print(f"prove_diff.py: error: invalid git repo or base {args.base!r}: {rev.stderr.strip()}", file=sys.stderr)
        return 2

    fault_args = (args.fault_file, args.fault_old, args.fault_new)
    if any(x is not None for x in fault_args) and not all(x is not None for x in fault_args):
        print("prove_diff.py: error: --fault-file, --fault-old and --fault-new must be passed together", file=sys.stderr)
        return 2
    fault_spec = (args.fault_file, args.fault_old, args.fault_new) if args.fault_file else None

    try:
        if args.mode == "fix":
            proven, info = run_fix_proof(repo, args.base, args.cmd, args.keep_tests, args.expect_failure, args.timeout)
        else:
            proven, info = run_refactor_proof(repo, args.base, args.cmd, fault_spec, args.timeout)
    except RuntimeError as exc:
        print(f"prove_diff.py: error: {exc}", file=sys.stderr)
        return 2

    info["verdict"] = "PROVEN" if proven else "NOT PROVEN"
    print_report(proven, info)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(info, handle, indent=2)
    return 0 if proven else 1


if __name__ == "__main__":
    sys.exit(main())
