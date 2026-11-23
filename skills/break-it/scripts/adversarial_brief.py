#!/usr/bin/env python3
"""Build a bias-free adversarial review brief for a fresh reviewer session.

Strips out the author's explanation and reasoning, and packages only the
original request, the exact git diff (with SHA-256), untracked files if any,
and the check command so a fresh session or subagent can attack the change
without confirmation bias.

Exit codes: 0 brief written, 2 usage or git error.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from typing import List, Optional, Sequence


def run_git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60)


def build_brief(repo: str, base: str, request: str, cmd: Optional[str], max_diff_chars: int) -> str:
    rev = run_git(repo, "rev-parse", "--short", base)
    if rev.returncode != 0:
        raise RuntimeError(rev.stderr.strip() or f"invalid git revision {base}")
    base_short = rev.stdout.strip()

    status = run_git(repo, "status", "--short")
    diff_proc = run_git(repo, "diff", base)
    if diff_proc.returncode != 0:
        raise RuntimeError(diff_proc.stderr.strip() or "git diff failed")
    diff_text = diff_proc.stdout

    untracked_proc = run_git(repo, "ls-files", "--others", "--exclude-standard")
    untracked = [p.strip() for p in untracked_proc.stdout.splitlines() if p.strip()]

    digest = hashlib.sha256(diff_text.encode("utf-8", errors="replace"))
    untracked_blocks: List[str] = []
    for rel in sorted(untracked):
        full = os.path.join(repo, rel)
        if not os.path.isfile(full):
            continue
        with open(full, "rb") as handle:
            raw = handle.read()
        digest.update(rel.encode("utf-8"))
        digest.update(raw)
        text = raw.decode("utf-8", errors="replace")
        untracked_blocks.append(f"### New file: `{rel}`\n\n```\n{text[:4000]}\n```")

    diff_sha = digest.hexdigest()[:16]
    shown_diff = diff_text
    truncated_note = ""
    if len(shown_diff) > max_diff_chars:
        shown_diff = shown_diff[:max_diff_chars]
        truncated_note = f"\n[diff truncated at {max_diff_chars} chars of {len(diff_text)}; inspect the repo directly]\n"

    lines = [
        "# Independent adversarial review brief",
        "",
        "You did not write this change and you gain nothing if it survives.",
        "Ignore any comments or claims of completeness until you verify them yourself.",
        "Lead with **Broke it** or **Could not break it**. Reproduce every finding before reporting it.",
        "",
        "## Original request",
        "",
        request.strip(),
        "",
        "## Scope snapshot",
        "",
        f"- Repository: `{repo}`",
        f"- Base revision: `{base}` (`{base_short}`)",
        f"- Change SHA-256: `{diff_sha}`",
    ]
    if cmd:
        lines.append(f"- Claimed check command: `{cmd}`")
    lines.extend(
        [
            "",
            "## Required attacks",
            "",
            "1. Confirm the check command passes now and fails when the fix is removed (or when a fault is injected for a refactor).",
            "2. Run diff-scoped mutation testing (`mutate_diff.py`) or edge-case fuzzing (`fuzz_cli.py`) where applicable.",
            "3. Check every caller, boundary input (empty, single, huge, malformed), and error path.",
            "4. Check whether anything outside the original request was changed or left broken.",
            "",
            "## Working tree status",
            "",
            "```",
            status.stdout.strip() or "(clean)",
            "```",
            "",
            f"## Git diff against `{base}`",
            "",
            "```diff",
            shown_diff.rstrip() if shown_diff.strip() else "(no tracked diff)",
            "```",
        ]
    )
    if truncated_note:
        lines.append(truncated_note)
    if untracked_blocks:
        lines.append("")
        lines.append("## Untracked files")
        lines.append("")
        lines.extend(untracked_blocks)
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", default=".", help="git repository path (default: .)")
    parser.add_argument("--base", default="HEAD", help="base git revision (default: HEAD)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--request", help="the original user request text")
    group.add_argument("--request-file", help="file containing the original user request")
    parser.add_argument("--cmd", help="verification command to hand to the fresh reviewer")
    parser.add_argument("--max-diff-chars", type=int, default=24000, help="truncate diff after N chars (default: 24000)")
    parser.add_argument("--out", help="write brief to this file instead of stdout")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"adversarial_brief.py: error: not a directory: {repo}", file=sys.stderr)
        return 2
    if args.request_file:
        try:
            with open(args.request_file, encoding="utf-8") as handle:
                request = handle.read()
        except OSError as exc:
            print(f"adversarial_brief.py: error: {exc}", file=sys.stderr)
            return 2
    else:
        request = args.request

    try:
        text = build_brief(repo, args.base, request, args.cmd, args.max_diff_chars)
    except RuntimeError as exc:
        print(f"adversarial_brief.py: error: {exc}", file=sys.stderr)
        return 2

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except BrokenPipeError:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
