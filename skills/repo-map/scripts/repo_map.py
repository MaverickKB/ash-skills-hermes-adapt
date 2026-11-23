#!/usr/bin/env python3
"""Generate a compact, token-budgeted codebase map for rapid orientation.

Scans a repository and prints:
  - language and line-count breakdown
  - detected test, build and lint commands from manifests and CI workflows
  - top-level directory roles
  - hub files ranked by import in-degree and size
  - top exported symbols (Python, TypeScript, JavaScript, Go, Rust, Zig) with path:line

Stays strictly within --budget-tokens and --max-lines.

Exit codes: 0 success, 2 invalid directory or usage.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "target",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "private",
}
CODE_LANGS = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".zig": "Zig",
    ".sh": "Shell",
    ".sql": "SQL",
}
TS_EXPORT_RE = re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|interface|type)\s+([A-Za-z_]\w*)")
GO_EXPORT_RE = re.compile(r"^func\s+(?:\([^)]+\)\s+)?([A-Z]\w*)\s*\(|^type\s+([A-Z]\w*)\s+")
RUST_PUB_RE = re.compile(r"^\s*pub\s+(?:async\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_]\w*)")
ZIG_PUB_RE = re.compile(r"^\s*pub\s+(?:fn|const)\s+([A-Za-z_]\w*)")
CI_RUN_RE = re.compile(r"^\s*run:\s*(.+)$")


def detect_commands(repo: str) -> List[str]:
    cmds: List[str] = []
    pkg_json = os.path.join(repo, "package.json")
    if os.path.isfile(pkg_json):
        try:
            pkg = json.load(open(pkg_json, encoding="utf-8"))
            scripts = pkg.get("scripts") or {}
            for key in ("test", "build", "lint", "typecheck", "check"):
                if key in scripts:
                    cmds.append(f"npm run {key}: {scripts[key]}")
        except (OSError, ValueError):
            pass

    if os.path.isfile(os.path.join(repo, "pyproject.toml")):
        cmds.append("pyproject.toml detected (try pytest / python3 -m unittest)")
    if os.path.isfile(os.path.join(repo, "Cargo.toml")):
        cmds.append("cargo test / cargo check")
    if os.path.isfile(os.path.join(repo, "go.mod")):
        cmds.append("go test ./...")
    if os.path.isfile(os.path.join(repo, "build.zig")):
        cmds.append("zig build test")

    wf_dir = os.path.join(repo, ".github", "workflows")
    if os.path.isdir(wf_dir):
        for name in sorted(os.listdir(wf_dir)):
            if not name.endswith((".yml", ".yaml")):
                continue
            try:
                for line in open(os.path.join(wf_dir, name), encoding="utf-8", errors="replace"):
                    m = CI_RUN_RE.match(line)
                    if m:
                        val = m.group(1).strip()
                        if val and val != "|":
                            cmds.append(f"CI ({name}): {val}")
            except OSError:
                continue
    return cmds[:12]


def extract_symbols(path: str, ext: str) -> Tuple[List[Tuple[int, str]], List[str]]:
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return [], []

    symbols: List[Tuple[int, str]] = []
    imports: List[str] = []

    if ext == ".py":
        try:
            tree = ast.parse(text)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        args = ", ".join(a.arg for a in node.args.args[:4])
                        symbols.append((node.lineno, f"def {node.name}({args})"))
                elif isinstance(node, ast.ClassDef):
                    if not node.name.startswith("_"):
                        symbols.append((node.lineno, f"class {node.name}"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        imports.append(a.name.split(".")[-1])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module.split(".")[-1])
        except SyntaxError:
            pass
        return symbols, imports

    for lineno, line in enumerate(text.splitlines(), 1):
        if ext in (".ts", ".tsx", ".js", ".jsx"):
            m = TS_EXPORT_RE.match(line)
            if m:
                symbols.append((lineno, m.group(1)))
            imp = re.search(r"""from\s+['"]([^'"]+)['"]""", line)
            if imp:
                imports.append(os.path.splitext(os.path.basename(imp.group(1)))[0])
        elif ext == ".go":
            m = GO_EXPORT_RE.match(line)
            if m:
                symbols.append((lineno, m.group(1) or m.group(2)))
        elif ext == ".rs":
            m = RUST_PUB_RE.match(line)
            if m:
                symbols.append((lineno, m.group(1)))
        elif ext == ".zig":
            m = ZIG_PUB_RE.match(line)
            if m:
                symbols.append((lineno, m.group(1)))
            imp = re.search(r"""@import\("([^"]+)"\)""", line)
            if imp:
                imports.append(os.path.splitext(os.path.basename(imp.group(1)))[0])
    return symbols, imports


def build_map(repo: str) -> Dict[str, object]:
    lang_files: Counter = Counter()
    lang_lines: Counter = Counter()
    dir_counts: Counter = Counter()
    file_stats: List[Dict[str, object]] = []
    in_degree: Counter = Counter()

    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for fname in sorted(files):
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, repo).replace(os.sep, "/")
            top = rel.split("/", 1)[0] if "/" in rel else "."
            dir_counts[top] += 1
            ext = os.path.splitext(fname)[1].lower()
            if ext not in CODE_LANGS:
                continue
            try:
                loc = len(open(full, encoding="utf-8", errors="replace").read().splitlines())
            except OSError:
                continue
            lang = CODE_LANGS[ext]
            lang_files[lang] += 1
            lang_lines[lang] += loc
            syms, imps = extract_symbols(full, ext)
            for target in set(imps):
                in_degree[target] += 1
            stem = os.path.splitext(fname)[0]
            file_stats.append({
                "file": rel,
                "stem": stem,
                "lang": lang,
                "lines": loc,
                "symbols": syms,
            })

    for item in file_stats:
        item["in_degree"] = in_degree.get(str(item["stem"]), 0)
        item.pop("stem", None)

    def is_test_or_trap(rel_path: str) -> int:
        top = rel_path.split("/", 1)[0]
        return 1 if top in ("tests", "test", "__tests__", "traps", "evals") else 0

    file_stats.sort(key=lambda x: (is_test_or_trap(str(x["file"])), -int(x["in_degree"]), -int(x["lines"]), str(x["file"])))
    return {
        "repo": repo,
        "languages": {k: {"files": lang_files[k], "lines": lang_lines[k]} for k in sorted(lang_files)},
        "commands": detect_commands(repo),
        "directories": dict(dir_counts.most_common(15)),
        "files": file_stats,
    }


def render_map(data: Dict[str, object], budget_tokens: int, max_lines: int) -> str:
    max_chars = max(400, budget_tokens * 4)
    out: List[str] = []

    def add(line: str) -> bool:
        if len(out) >= max_lines - 1:
            return False
        if sum(len(x) + 1 for x in out) + len(line) + 1 > max_chars:
            return False
        out.append(line)
        return True

    add(f"# Repo map: {os.path.basename(str(data['repo']))}")
    langs = data["languages"]
    assert isinstance(langs, dict)
    lang_str = ", ".join(f"{k} ({v['files']} files, {v['lines']} LOC)" for k, v in langs.items()) or "no code files"
    add(f"Languages: {lang_str}")

    cmds = data["commands"]
    assert isinstance(cmds, list)
    if cmds:
        add("")
        add("## Commands")
        for c in cmds:
            if not add(f"- {c}"):
                break

    dirs = data["directories"]
    assert isinstance(dirs, dict)
    if dirs:
        add("")
        add("## Top directories")
        for d, cnt in dirs.items():
            if not add(f"- `{d}` ({cnt} files)"):
                break

    files = data["files"]
    assert isinstance(files, list)
    if files:
        add("")
        add("## Key files and exported symbols")
        for item in files:
            header = f"- `{item['file']}` ({item['lines']} LOC, imported_by={item['in_degree']})"
            if not add(header):
                out.append("... (truncated to budget)")
                break
            syms = item["symbols"]
            assert isinstance(syms, list)
            if syms:
                shown = ", ".join(f"{sig}@L{ln}" for ln, sig in syms[:6])
                if len(syms) > 6:
                    shown += f" (+{len(syms) - 6} more)"
                if not add(f"    {shown}"):
                    out.append("... (truncated to budget)")
                    break
    return "\n".join(out) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", default=".", help="repository root directory (default: .)")
    parser.add_argument("--budget-tokens", type=int, default=1800, help="approximate output token budget (default: 1800)")
    parser.add_argument("--max-lines", type=int, default=160, help="maximum output lines (default: 160)")
    parser.add_argument("--json", dest="json_out", help="write full structured repo map to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"repo_map.py: error: not a directory: {repo}", file=sys.stderr)
        return 2
    data = build_map(repo)
    sys.stdout.write(render_map(data, args.budget_tokens, args.max_lines))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
