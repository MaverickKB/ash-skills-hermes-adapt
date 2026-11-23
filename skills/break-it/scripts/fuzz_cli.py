#!/usr/bin/env python3
"""Attack a CLI command or script with adversarial inputs in isolated sandboxes.

Replaces `{file}` and/or `{dir}` in `--cmd` with freshly constructed scratch
targets covering empty, single-byte, large, invalid UTF-8, unicode, spaces and
quotes in paths, outside symlink escape, read-only directory, and double-run
idempotency.

A clean error exit on bad input counts as HANDLED. Unhandled crashes (Python
tracebacks, segfaults, signals), timeouts, or modifying/deleting an outside
symlink canary count as findings.

Exit codes: 0 CLEAN, 1 BROKE IT (finding(s)), 2 usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

CRASH_PATTERNS = re.compile(
    r"(Traceback \(most recent call last\):|Segmentation fault|Bus error|Abort trap|"
    r"panic:|UnhandledPromiseRejection|FATAL ERROR:)",
    re.MULTILINE,
)

ALL_FILE_ATTACKS = (
    "empty-file",
    "single-byte",
    "large-file",
    "binary-garbage",
    "unicode-text",
    "space-in-path",
    "double-run",
)

ALL_DIR_ATTACKS = (
    "empty-dir",
    "space-in-path",
    "symlink-escape",
    "double-run",
    "readonly-dir",
)


def sha256_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def run_target(cmd_template: str, file_path: Optional[str], dir_path: Optional[str], cwd: str, timeout: int) -> Dict[str, object]:
    cmd = cmd_template
    if file_path is not None:
        cmd = cmd.replace("{file}", shlex.quote(file_path))
    if dir_path is not None:
        cmd = cmd.replace("{dir}", shlex.quote(dir_path))
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout, env=env)
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        return {
            "command": cmd,
            "returncode": proc.returncode,
            "timed_out": False,
            "output": combined,
            "excerpt": "\n".join(combined.splitlines()[-8:]),
        }
    except subprocess.TimeoutExpired:
        return {
            "command": cmd,
            "returncode": -1,
            "timed_out": True,
            "output": "",
            "excerpt": f"timed out after {timeout}s",
        }


def classify_run(run: Dict[str, object]) -> Tuple[bool, str]:
    if run["timed_out"]:
        return True, f"TIMEOUT ({run['excerpt']})"
    rc = int(run["returncode"])
    if rc < 0:
        return True, f"SIGNAL CRASH (exit {rc})"
    output = str(run["output"])
    match = CRASH_PATTERNS.search(output)
    if match:
        return True, f"UNHANDLED CRASH ({match.group(1)})"
    if rc == 0:
        return False, "OK (exit 0)"
    return False, f"HANDLED (exit {rc})"


def run_file_attack(name: str, cmd_template: str, large_kb: int, timeout: int, cwd: str) -> Dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"fuzz-{name}-") as scratch:
        if name == "empty-file":
            target = os.path.join(scratch, "empty.txt")
            open(target, "wb").close()
        elif name == "single-byte":
            target = os.path.join(scratch, "one.txt")
            with open(target, "wb") as handle:
                handle.write(b"a\n")
        elif name == "large-file":
            target = os.path.join(scratch, "large.txt")
            line = b"row,1234567890,payload-text-for-size-test\n"
            repeats = max(1, (large_kb * 1024) // len(line))
            with open(target, "wb") as handle:
                handle.write(line * repeats)
        elif name == "binary-garbage":
            target = os.path.join(scratch, "garbage.bin")
            with open(target, "wb") as handle:
                handle.write(b"\x80\xff\x00\xfe\x81\x00not-utf8\xff\n")
        elif name == "unicode-text":
            target = os.path.join(scratch, "unicode.txt")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("cafe\u0301 \u4e2d\u6587 \u202ereversed 'quotes' \"double\"\n")
        elif name == "space-in-path":
            sub = os.path.join(scratch, "dir with spaces")
            os.makedirs(sub, exist_ok=True)
            target = os.path.join(sub, "file 'quoted' name.txt")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("hello world\n")
        elif name == "double-run":
            target = os.path.join(scratch, "repeat.txt")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("alpha\nbeta\n")
            first = run_target(cmd_template, target, None, cwd, timeout)
            broken1, status1 = classify_run(first)
            if broken1:
                return {"attack": name, "broken": True, "status": f"run 1: {status1}", "excerpt": first["excerpt"]}
            second = run_target(cmd_template, target, None, cwd, timeout)
            broken2, status2 = classify_run(second)
            return {"attack": name, "broken": broken2, "status": f"run 2: {status2}", "excerpt": second["excerpt"]}
        else:
            raise RuntimeError(f"unknown file attack: {name}")

        res = run_target(cmd_template, target, None, cwd, timeout)
        broken, status = classify_run(res)
        return {"attack": name, "broken": broken, "status": status, "excerpt": res["excerpt"]}


def run_dir_attack(name: str, cmd_template: str, timeout: int, cwd: str) -> Dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"fuzz-{name}-") as outer:
        work = os.path.join(outer, "target")
        os.makedirs(work, exist_ok=True)

        if name == "empty-dir":
            res = run_target(cmd_template, None, work, cwd, timeout)
            broken, status = classify_run(res)
            return {"attack": name, "broken": broken, "status": status, "excerpt": res["excerpt"]}

        if name == "space-in-path":
            sub = os.path.join(work, "dir with spaces")
            os.makedirs(sub, exist_ok=True)
            with open(os.path.join(sub, "file 'quoted' 1.log"), "w", encoding="utf-8") as handle:
                handle.write("entry\n")
            res = run_target(cmd_template, None, work, cwd, timeout)
            broken, status = classify_run(res)
            return {"attack": name, "broken": broken, "status": status, "excerpt": res["excerpt"]}

        if name == "symlink-escape":
            outside_dir = os.path.join(outer, "outside")
            os.makedirs(outside_dir, exist_ok=True)
            canary = os.path.join(outside_dir, "secret-canary.log")
            with open(canary, "w", encoding="utf-8") as handle:
                handle.write("MUST NOT BE TOUCHED OR DELETED\n")
            os.utime(canary, (1000000000, 1000000000))
            before_hash = sha256_file(canary)
            link_path = os.path.join(work, "escaped-link.log")
            os.symlink(canary, link_path)
            dir_link = os.path.join(work, "escaped-dir")
            os.symlink(outside_dir, dir_link)
            res = run_target(cmd_template, None, work, cwd, timeout)
            after_hash = sha256_file(canary)
            if after_hash != before_hash:
                what = "DELETED" if after_hash is None else "MODIFIED"
                return {
                    "attack": name,
                    "broken": True,
                    "status": f"SYMLINK ESCAPE (outside canary file was {what})",
                    "excerpt": res["excerpt"],
                }
            broken, status = classify_run(res)
            return {"attack": name, "broken": broken, "status": status, "excerpt": res["excerpt"]}

        if name == "double-run":
            with open(os.path.join(work, "sample.log"), "w", encoding="utf-8") as handle:
                handle.write("line 1\n")
            first = run_target(cmd_template, None, work, cwd, timeout)
            broken1, status1 = classify_run(first)
            if broken1:
                return {"attack": name, "broken": True, "status": f"run 1: {status1}", "excerpt": first["excerpt"]}
            second = run_target(cmd_template, None, work, cwd, timeout)
            broken2, status2 = classify_run(second)
            return {"attack": name, "broken": broken2, "status": f"run 2: {status2}", "excerpt": second["excerpt"]}

        if name == "readonly-dir":
            sample = os.path.join(work, "locked.log")
            with open(sample, "w", encoding="utf-8") as handle:
                handle.write("readonly\n")
            os.chmod(sample, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.chmod(work, stat.S_IRUSR | stat.S_IXUSR)
            try:
                res = run_target(cmd_template, None, work, cwd, timeout)
            finally:
                os.chmod(work, stat.S_IRWXU)
                if os.path.exists(sample):
                    os.chmod(sample, stat.S_IRUSR | stat.S_IWUSR)
            broken, status = classify_run(res)
            return {"attack": name, "broken": broken, "status": status, "excerpt": res["excerpt"]}

        raise RuntimeError(f"unknown dir attack: {name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cmd", required=True, help="command template containing {file} and/or {dir}")
    parser.add_argument("--cwd", default=".", help="working directory for running --cmd (default: .)")
    parser.add_argument("--large-kb", type=int, default=512, help="size in KB for large-file attack (default: 512)")
    parser.add_argument("--timeout", type=int, default=20, help="seconds per attack run (default: 20)")
    parser.add_argument("--attacks", help="comma-separated subset of attack names to run")
    parser.add_argument("--json", dest="json_out", help="write structured findings to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    has_file = "{file}" in args.cmd
    has_dir = "{dir}" in args.cmd
    if not has_file and not has_dir:
        print("fuzz_cli.py: error: --cmd must contain {file} or {dir}", file=sys.stderr)
        return 2
    if has_file and has_dir:
        print("fuzz_cli.py: error: pass either {file} or {dir} in --cmd, not both", file=sys.stderr)
        return 2
    cwd = os.path.abspath(args.cwd)
    if not os.path.isdir(cwd):
        print(f"fuzz_cli.py: error: not a directory: {cwd}", file=sys.stderr)
        return 2

    available = ALL_FILE_ATTACKS if has_file else ALL_DIR_ATTACKS
    if args.attacks:
        chosen = [a.strip() for a in args.attacks.split(",") if a.strip()]
        bad = [a for a in chosen if a not in available]
        if bad:
            print(f"fuzz_cli.py: error: unknown attack(s) {bad}; valid: {', '.join(available)}", file=sys.stderr)
            return 2
    else:
        chosen = list(available)

    results: List[Dict[str, object]] = []
    for name in chosen:
        if has_file:
            results.append(run_file_attack(name, args.cmd, args.large_kb, args.timeout, cwd))
        else:
            results.append(run_dir_attack(name, args.cmd, args.timeout, cwd))

    findings = [r for r in results if r["broken"]]
    for r in results:
        tag = "FAIL" if r["broken"] else "ok  "
        print(f"  {tag}  {r['attack']:<16} {r['status']}")
        if r["broken"] and r.get("excerpt"):
            for line in str(r["excerpt"]).splitlines()[:5]:
                print(f"        {line}")

    verdict = "CLEAN" if not findings else f"BROKE IT ({len(findings)} of {len(results)} attacks)"
    print(f"FUZZ: {verdict}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"verdict": "CLEAN" if not findings else "BROKE IT", "results": results}, handle, indent=2)
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
