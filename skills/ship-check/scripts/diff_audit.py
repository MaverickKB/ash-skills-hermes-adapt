#!/usr/bin/env python3
"""Pre-PR diff audit gate: catches secrets, debug leftovers, stale callers and hygiene issues.

Scans the staged snapshot when changes are staged, otherwise the working tree, for:
  - Blockers: leaked secrets/keys/tokens, hardcoded home paths, merge conflict markers,
    debug breakpoints/markers (`breakpoint()`, `pdb.set_trace`, `debugger;`, `TMPDBG`, `dbg!`),
    changed Python function signatures with untouched external callers, or a failing `--cmd`.
  - Warnings: new `TODO`/`FIXME`/`HACK` comments, `console.log(` in non-test JS/TS,
    or non-test source files exceeding 350 lines.

Exit codes: 0 SHIP, 1 HOLD, 2 usage or git error.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from inspect import Parameter, Signature
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

SECRET_PATTERNS = [
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("hardcoded-secret", re.compile(r"""\b(?:api_key|secret_key|access_token|password)\s*[:=]\s*['"]([^'"]{12,})['"]""", re.I)),
    ("home-path", re.compile(r"(?:/Users/[A-Za-z0-9_.-]+/|/home/[A-Za-z0-9_.-]+/|[A-Z]:\\Users\\)")),
]
PLACEHOLDER_RE = re.compile(r"(example|dummy|placeholder|changeme|redacted|your[-_]|xxx|fake|test)", re.I)
CONFLICT_RE = re.compile(r"^(?:<{7}\s|={7}$|>{7}\s)")
DEBUG_BLOCKERS = [
    ("python-breakpoint", re.compile(r"\b(?:breakpoint\s*\(|pdb\.set_trace\s*\(|ipdb\.set_trace\s*\()")),
    ("js-debugger", re.compile(r"^\s*debugger\s*;?\s*$")),
    ("rust-dbg", re.compile(r"\bdbg!\s*\(")),
    ("temp-debug-marker", re.compile(r"\bTMPDBG(?:-[A-Za-z0-9]+)?\b")),
]
CONSOLE_LOG_RE = re.compile(r"\bconsole\.log\s*\(")
TODO_COMMENT_RE = re.compile(r"(?:#|//|/\*)\s*(?:TODO|FIXME|HACK|XXX)\b")
CODE_LINE_SKIP_RE = re.compile(r'^\s*(?:#|//|/\*|\*|["\']{3}|r?["\'])')
SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", "build", ".venv", "venv", "target", "private"}
GOD_FILE_LINES = 350


def is_test_path(rel: str) -> bool:
    norm = rel.replace("\\", "/")
    base = os.path.basename(norm)
    return (
        norm.startswith(("tests/", "test/", "__tests__/"))
        or "/tests/" in norm
        or "/test/" in norm
        or base.startswith("test_")
        or any(tok in base for tok in ("_test.", ".test.", ".spec."))
    )


def run_git(repo: str, *args: str, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=60)


def added_lines_from_diff(repo: str, base: str, env: Optional[Dict[str, str]] = None,
                          tree: Optional[str] = None) -> Dict[str, List[Tuple[int, str]]]:
    revisions = [base, tree] if tree else [base]
    proc = subprocess.run(["git", "diff", "--patch-with-raw", "-z", "--unified=0", "--no-renames",
                           "--no-color", "--no-ext-diff", "--no-textconv", *revisions, "--"],
                          cwd=repo, env=env, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(os.fsdecode(proc.stderr).strip() or f"git diff against {base} failed")
    raw, _, patch = proc.stdout.partition(b"\0\0")
    paths = [os.fsdecode(path) for path in raw.split(b"\0")[1::2]]
    patches = re.split(rb"^diff --git ", patch, flags=re.M)[1:]
    if len(paths) != len(patches):
        raise RuntimeError("could not associate every changed path with its patch")
    added: Dict[str, List[Tuple[int, str]]] = {path: [] for path in paths}
    for path, changes in zip(paths, patches):
        cur_line = None
        for line in changes.decode("utf-8", "replace").split("\n"):
            if line.startswith("@@ "):
                cur_line = int(line.split()[2][1:].split(",")[0])
            elif cur_line is not None and line.startswith("+"):
                added[path].append((cur_line, line[1:]))
                cur_line += 1
            elif cur_line is not None and line.startswith(" "):
                cur_line += 1

    if tree is not None:
        return added
    untracked = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z", env=env)
    if untracked.returncode == 0:
        for rel in untracked.stdout.split("\0"):
            full = os.path.join(repo, rel)
            if not rel or not os.path.isfile(full):
                continue
            try:
                raw = open(full, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            added[rel] = [(i, ln) for i, ln in enumerate(raw.splitlines(), 1)]
    return added


def python_signatures(source: str) -> Dict[str, Signature]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    sigs: Dict[str, Signature] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            args = node.args
            positional = args.posonlyargs + args.args
            required = len(positional) - len(args.defaults)
            params = [
                Parameter(arg.arg, Parameter.POSITIONAL_ONLY if i < len(args.posonlyargs) else Parameter.POSITIONAL_OR_KEYWORD,
                          default=Parameter.empty if i < required else Ellipsis)
                for i, arg in enumerate(positional)
            ]
            if args.vararg:
                params.append(Parameter(args.vararg.arg, Parameter.VAR_POSITIONAL))
            for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                params.append(Parameter(arg.arg, Parameter.KEYWORD_ONLY,
                                        default=Parameter.empty if default is None else Ellipsis))
            if args.kwarg:
                params.append(Parameter(args.kwarg.arg, Parameter.VAR_KEYWORD))
            sigs[node.name] = Signature(params)
    return sigs


def positional_contract(signature: Signature) -> List[Tuple[str, bool]]:
    return [(p.name, p.default is Parameter.empty) for p in signature.parameters.values()
            if p.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)]


def incompatible_call(call: ast.Call, old: Signature, new: Optional[Signature]) -> bool:
    if new is None or positional_contract(old) != positional_contract(new):
        return True
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(kw.arg is None for kw in call.keywords):
        # Adding optional keyword-only parameters preserves even calls with unknown unpacked values.
        previous = old.parameters
        return not (all(new.parameters.get(name) == param for name, param in previous.items())
                    and all(p.kind == Parameter.KEYWORD_ONLY and p.default is not Parameter.empty
                            for name, p in new.parameters.items() if name not in previous))
    positional = [None] * len(call.args)
    keywords = {kw.arg: None for kw in call.keywords if kw.arg is not None}
    try:
        old.bind(*positional, **keywords)
    except TypeError:
        return False
    try:
        new.bind(*positional, **keywords)
    except TypeError:
        return True
    return False


def referenced_calls(tree: ast.Module, changed: Set[str]) -> Iterator[Tuple[str, ast.Call]]:
    names = {name: {name} for name in changed}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.asname and alias.name in changed:
                    names.setdefault(alias.asname, set()).add(alias.name)
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        if isinstance(call.func, ast.Name):
            for name in sorted(names.get(call.func.id, ())):
                yield name, call
        elif isinstance(call.func, ast.Attribute) and call.func.attr in changed:
            yield call.func.attr, call


def check_signature_callers(repo: str, base: str, changed_files: Sequence[str],
                            env: Optional[Dict[str, str]] = None) -> List[Dict[str, object]]:
    changed_set = set(changed_files)
    changes: Dict[str, List[Tuple[str, Signature, Optional[Signature]]]] = {}
    for rel in changed_files:
        if not rel.endswith(".py") or is_test_path(rel):
            continue
        full = os.path.join(repo, rel)
        old_proc = run_git(repo, "show", f"{base}:{rel}", env=env)
        if old_proc.returncode != 0:
            continue
        old_sigs = python_signatures(old_proc.stdout)
        new_sigs = {}
        if os.path.isfile(full):
            with open(full, encoding="utf-8", errors="replace") as source:
                new_sigs = python_signatures(source.read())
        for name, old in old_sigs.items():
            new = new_sigs.get(name)
            if new != old:
                changes.setdefault(name, []).append((rel, old, new))

    if not changes:
        return []

    findings: List[Dict[str, object]] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, repo).replace(os.sep, "/")
            if rel in changed_set:
                continue
            try:
                with open(full, encoding="utf-8", errors="replace") as handle:
                    tree = ast.parse(handle.read())
            except (OSError, SyntaxError):
                continue
            for fn_name, call in referenced_calls(tree, set(changes)):
                for src_file, old, new in changes[fn_name]:
                    if not incompatible_call(call, old, new):
                        continue
                    reason = "function removed or renamed" if new is None else f"signature changed ({old} -> {new})"
                    findings.append({
                        "severity": "blocker", "kind": "untouched-caller", "file": rel, "line": call.lineno,
                        "detail": f"calls `{fn_name}` from `{src_file}` ({reason}) without being updated",
                    })
    return findings


def python_docstring_lines(path: str) -> Set[int]:
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return set()
    doc_lines: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                start = getattr(first, "lineno", 0)
                end = getattr(first, "end_lineno", start)
                doc_lines.update(range(start, end + 1))
    return doc_lines


def audit_checkout(repo: str, base: str, cmd: Optional[str], timeout: int,
                   env: Optional[Dict[str, str]] = None, tree: Optional[str] = None) -> Dict[str, object]:
    added_map = added_lines_from_diff(repo, base, env, tree)
    blockers: List[Dict[str, object]] = []
    warnings: List[Dict[str, object]] = []

    for rel, lines in sorted(added_map.items()):
        test_file = is_test_path(rel)
        is_code = rel.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".zig", ".sh"))
        full_path = os.path.join(repo, rel)
        doc_lines = python_docstring_lines(full_path) if rel.endswith(".py") and os.path.isfile(full_path) else set()
        for lineno, text in lines:
            if CONFLICT_RE.match(text):
                blockers.append({"severity": "blocker", "kind": "conflict-marker", "file": rel, "line": lineno, "detail": text.strip()[:80]})
            for label, pat in SECRET_PATTERNS:
                m = pat.search(text)
                if not m:
                    continue
                if label == "hardcoded-secret" and PLACEHOLDER_RE.search(m.group(1)):
                    continue
                if label == "home-path" and test_file:
                    continue
                blockers.append({"severity": "blocker", "kind": label, "file": rel, "line": lineno, "detail": f"matched {label}"})
            if is_code and not test_file:
                if lineno not in doc_lines and not CODE_LINE_SKIP_RE.match(text):
                    for label, pat in DEBUG_BLOCKERS:
                        if pat.search(text) and "re.compile(" not in text:
                            blockers.append({"severity": "blocker", "kind": label, "file": rel, "line": lineno, "detail": text.strip()[:80]})
                if rel.endswith((".js", ".jsx", ".ts", ".tsx")) and not CODE_LINE_SKIP_RE.match(text) and CONSOLE_LOG_RE.search(text):
                    warnings.append({"severity": "warning", "kind": "console-log", "file": rel, "line": lineno, "detail": text.strip()[:80]})
                if lineno not in doc_lines and TODO_COMMENT_RE.search(text):
                    warnings.append({"severity": "warning", "kind": "todo-added", "file": rel, "line": lineno, "detail": text.strip()[:80]})

        if is_code and not test_file:
            full = os.path.join(repo, rel)
            if os.path.isfile(full):
                line_count = len(open(full, encoding="utf-8", errors="replace").read().splitlines())
                if line_count > GOD_FILE_LINES:
                    warnings.append({
                        "severity": "warning",
                        "kind": "large-file",
                        "file": rel,
                        "line": line_count,
                        "detail": f"file has {line_count} lines (review threshold {GOD_FILE_LINES})",
                    })

    blockers.extend(check_signature_callers(repo, base, list(added_map.keys()), env))

    cmd_result: Optional[Dict[str, object]] = None
    if cmd:
        try:
            proc = subprocess.run(cmd, cwd=repo, env=env, shell=True, capture_output=True, text=True, timeout=timeout)
            cmd_result = {
                "command": cmd,
                "returncode": proc.returncode,
                "excerpt": "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:]),
            }
            if proc.returncode != 0:
                blockers.append({
                    "severity": "blocker",
                    "kind": "check-failed",
                    "file": "-",
                    "line": 0,
                    "detail": f"`{cmd}` exited {proc.returncode}",
                })
        except subprocess.TimeoutExpired:
            cmd_result = {"command": cmd, "returncode": -1, "excerpt": f"timed out after {timeout}s"}
            blockers.append({
                "severity": "blocker",
                "kind": "check-timeout",
                "file": "-",
                "line": 0,
                "detail": f"`{cmd}` timed out after {timeout}s",
            })

    return {
        "base": base,
        "files_audited": len(added_map),
        "blockers": blockers,
        "warnings": warnings,
        "cmd_result": cmd_result,
    }


def git_output(repo: str, *args: str, env: Optional[Dict[str, str]] = None) -> str:
    result = run_git(repo, *args, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def audit_repo(repo: str, base: str, cmd: Optional[str], timeout: int, mode: str = "auto") -> Dict[str, object]:
    repo = git_output(repo, "rev-parse", "--show-toplevel")
    if mode == "auto":
        staged = run_git(repo, "diff", "--cached", "--quiet", "--")
        if staged.returncode not in (0, 1):
            raise RuntimeError(staged.stderr.strip() or "could not inspect the index")
        mode = "staged" if staged.returncode else "worktree"
    if mode == "worktree":
        return {**audit_checkout(repo, base, cmd, timeout), "scope": mode}

    tree = git_output(repo, "write-tree")
    if base == "HEAD" and run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 1:
        base_tree = git_output(repo, "hash-object", "-t", "tree", os.devnull)
    else:
        base_tree = git_output(repo, "rev-parse", "--verify", f"{base}^{{tree}}")
    local_vars = set(git_output(repo, "rev-parse", "--local-env-vars").splitlines())
    env = {key: value for key, value in os.environ.items() if key not in local_vars}
    with tempfile.TemporaryDirectory(prefix="ship-check-") as snapshot:
        git_output(snapshot, "clone", "--quiet", "--shared", "--no-checkout", "--", repo, ".", env=env)
        git_output(snapshot, "read-tree", "--reset", "-u", tree, env=env)
        report = audit_checkout(snapshot, base_tree, cmd, timeout, env, tree)
    return {**report, "base": base, "scope": mode, "tree": tree}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", default=".", help="git repository root (default: .)")
    parser.add_argument("--base", default="HEAD", help="base git revision to diff against (default: HEAD)")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--staged", dest="mode", action="store_const", const="staged", help="audit and test the Git index")
    scope.add_argument("--worktree", dest="mode", action="store_const", const="worktree", help="include working-tree and untracked files")
    parser.set_defaults(mode="auto")
    parser.add_argument("--cmd", help="optional test or build command that must exit 0")
    parser.add_argument("--timeout", type=int, default=120, help="seconds for --cmd (default: 120)")
    parser.add_argument("--strict", action="store_true", help="treat warnings as HOLD blockers")
    parser.add_argument("--json", dest="json_out", help="write structured audit report to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"diff_audit.py: error: not a directory: {repo}", file=sys.stderr)
        return 2
    try:
        data = audit_repo(repo, args.base, args.cmd, args.timeout, args.mode)
    except RuntimeError as exc:
        print(f"diff_audit.py: error: {exc}", file=sys.stderr)
        return 2

    blockers = data["blockers"]
    warnings = data["warnings"]
    print(f"Audited {data['scope']}" + (f" tree {data['tree']}" if "tree" in data else ""))
    for item in blockers:
        print(f"  BLOCKER  {item['file']}:{item['line']} [{item['kind']}] {item['detail']}")
    for item in warnings:
        print(f"  WARN     {item['file']}:{item['line']} [{item['kind']}] {item['detail']}")

    hold = bool(blockers) or (args.strict and bool(warnings))
    data["verdict"] = "HOLD" if hold else "SHIP"
    print(f"VERDICT: {data['verdict']} ({data['files_audited']} file(s), {len(blockers)} blocker(s), {len(warnings)} warning(s))")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    return 1 if hold else 0


if __name__ == "__main__":
    sys.exit(main())
