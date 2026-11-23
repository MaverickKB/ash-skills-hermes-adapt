#!/usr/bin/env python3
"""Lint a spec, RFC, or ADR for unfalsifiable requirements and repo drift.

Checks a Markdown specification for:
  1. Missing essential sections (goal, out of scope, failure modes, acceptance checks,
     and rollback when the spec modifies schema, state, or production deployments).
  2. Weasel/unmeasurable requirements ('fast', 'scalable', 'gracefully', 'TBD', etc.)
     that lack a numeric threshold or concrete check in the same sentence.
  3. References to repository file paths that do not exist on disk (with --repo).
  4. Untraced requirement IDs (such as REQ-01) missing from code or tests (--check-trace).

Exit codes: 0 SPEC CLEAN, 1 SPEC FINDINGS, 2 usage or read error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple

FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

REQUIRED_SECTIONS = [
    ("goal", re.compile(r"\b(goal|problem|objective|summary|overview|context)\b", re.I)),
    ("out-of-scope", re.compile(r"\b(out of scope|non-goals?|not in scope|excluded|boundaries)\b", re.I)),
    ("failure-modes", re.compile(r"\b(failure modes?|edge cases?|risks?|error handling|constraints)\b", re.I)),
    ("acceptance", re.compile(r"\b(acceptance|verification|done when|success criteria|test plan|checks)\b", re.I)),
]
ROLLBACK_TRIGGER_RE = re.compile(r"\b(migrat\w*|schema|production|deploy|backfill|delete|drop|cutover)\b", re.I)
ROLLBACK_SECTION_RE = re.compile(r"\b(rollback|reversib\w*|undo|recovery|backout)\b", re.I)

WEASEL_RE = re.compile(
    r"\b(fast|quickly|scalable|highly available|gracefully|appropriately|seamless(?:ly)?|"
    r"robust|as needed|soon|minimal|reasonable|efficiently|cleanly|properly|user-friendly|"
    r"tbd|tba|todo|etc\.)\b",
    re.I,
)
MEASURABLE_RE = re.compile(
    r"(\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds?|min|minutes?|hours?|days?|%|bytes?|kb|mb|gb|tb|"
    r"req/s|rps|qps|rows?|items?|retries|attempts?|lines?|chars?|tokens?|p50|p95|p99)\b|"
    r"\b(?:<=|>=|<|>|==)\s*\d|\bexit\s+\d\b)",
    re.I,
)
PATH_LIKE_RE = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|zig|sql|md|json|ya?ml|toml|sh)$"
)
PLANNED_TAG_RE = re.compile(r"\((?:planned|new|gone|removed|future)\)", re.I)
REQ_ID_RE = re.compile(r"\b((?:REQ|AC|SPEC)-\d{1,4})\b")
SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", "build", ".venv", "venv", "target", "private"}


def parse_spec(text: str) -> Tuple[List[str], List[Tuple[int, str]]]:
    headings: List[str] = []
    prose: List[Tuple[int, str]] = []
    fenced = False
    for idx, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith(">"):
            continue
        m = HEADING_RE.match(line)
        if m:
            headings.append(m.group(1).strip())
            continue
        prose.append((idx, line))
    return headings, prose


def lint_spec(
    text: str,
    repo: Optional[str],
    check_sections: bool,
    check_trace: bool,
    require_rollback: bool,
) -> Dict[str, object]:
    headings, prose_lines = parse_spec(text)
    findings: List[Dict[str, object]] = []

    if check_sections:
        for label, pattern in REQUIRED_SECTIONS:
            if not any(pattern.search(h) for h in headings):
                findings.append({
                    "kind": "missing-section",
                    "line": 1,
                    "detail": f"missing required section covering `{label}`",
                })
        needs_rollback = require_rollback or bool(ROLLBACK_TRIGGER_RE.search(text))
        if needs_rollback and not any(ROLLBACK_SECTION_RE.search(h) for h in headings):
            findings.append({
                "kind": "missing-section",
                "line": 1,
                "detail": "spec mentions state/schema/deployment changes with no rollback or reversibility section",
            })

    for lineno, line in prose_lines:
        clean = INLINE_CODE_RE.sub(" ", line)
        for sentence in re.split(r"(?<=[.!?])\s+", clean):
            m = WEASEL_RE.search(sentence)
            if m and not MEASURABLE_RE.search(sentence):
                findings.append({
                    "kind": "unmeasurable",
                    "line": lineno,
                    "detail": f"vague term {m.group(0)!r} with no numeric threshold or concrete check: {sentence.strip()[:90]}",
                })
                break

    if repo:
        for lineno, line in prose_lines:
            for match in INLINE_CODE_RE.finditer(line):
                token = match.group(1).strip()
                if not PATH_LIKE_RE.match(token):
                    continue
                after = line[match.end() : match.end() + 24]
                if PLANNED_TAG_RE.search(after):
                    continue
                if not os.path.exists(os.path.join(repo, token)):
                    findings.append({
                        "kind": "missing-path",
                        "line": lineno,
                        "detail": f"referenced path `{token}` does not exist in {repo} (mark `(planned)` if not created yet)",
                    })

    req_ids = sorted(set(REQ_ID_RE.findall(text)))
    untraced: List[str] = []
    if check_trace and repo and req_ids:
        found: Set[str] = set()
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if not name.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".zig")):
                    continue
                full = os.path.join(root, name)
                try:
                    content = open(full, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                found.update(REQ_ID_RE.findall(content))
        untraced = [rid for rid in req_ids if rid not in found]
        for rid in untraced:
            findings.append({
                "kind": "untraced-req",
                "line": 1,
                "detail": f"requirement `{rid}` is not referenced in any code or test file under {repo}",
            })

    return {
        "verdict": "CLEAN" if not findings else "FINDINGS",
        "headings": headings,
        "req_ids": req_ids,
        "untraced": untraced,
        "findings": findings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("spec", help="path to the Markdown specification file")
    parser.add_argument("--repo", help="repository root to verify referenced file paths and requirement traces")
    parser.add_argument("--no-section-check", action="store_true", help="skip required-heading checks")
    parser.add_argument("--require-rollback", action="store_true", help="always require a rollback/reversibility section")
    parser.add_argument("--check-trace", action="store_true", help="verify every REQ-/AC-/SPEC- ID appears in repo code or tests")
    parser.add_argument("--json", dest="json_out", help="write structured report to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not os.path.isfile(args.spec):
        print(f"spec_lint.py: error: spec file not found: {args.spec}", file=sys.stderr)
        return 2
    repo = os.path.abspath(args.repo) if args.repo else None
    if repo and not os.path.isdir(repo):
        print(f"spec_lint.py: error: repo directory not found: {repo}", file=sys.stderr)
        return 2
    if args.check_trace and not repo:
        print("spec_lint.py: error: --check-trace requires --repo", file=sys.stderr)
        return 2
    try:
        with open(args.spec, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"spec_lint.py: error: {exc}", file=sys.stderr)
        return 2

    data = lint_spec(text, repo, not args.no_section_check, args.check_trace, args.require_rollback)
    for item in data["findings"]:
        print(f"  {item['kind']:<16} line {item['line']}: {item['detail']}")
    count = len(data["findings"])
    print(f"SPEC: CLEAN ({len(data['req_ids'])} req IDs)" if count == 0 else f"SPEC: FINDINGS ({count} issue(s))")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    return 0 if count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
