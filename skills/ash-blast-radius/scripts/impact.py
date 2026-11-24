#!/usr/bin/env python3
"""Map the blast radius of a symbol, file, or git diff across a repository.

Builds a static AST import/alias/caller graph for Python and a regex symbol and
import graph for TypeScript, JavaScript, Go, Rust, Zig, Markdown, and configs.
Finds direct callers, aliased imports (such as `from a import f as g`),
transitive importers, covering tests, doc mentions, and circular imports.

Exit codes: 0 report generated, 1 cycles found with --fail-on-cycles, 2 error.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

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
CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".zig")
DOC_EXTS = (".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml")
TEST_HINTS = ("tests/", "test/", "__tests__/", "test_", "_test.", ".test.", ".spec.")

JS_IMPORT_RE = re.compile(
    r"""import\s+(?:(?:\*\s+as\s+(\w+))|(?:\{([^}]+)\})|(\w+))\s+from\s+['"]([^'"]+)['"]"""
)
JS_ALIAS_ITEM_RE = re.compile(r"\b(\w+)\s+as\s+(\w+)\b")
ZIG_IMPORT_RE = re.compile(r"""(?:const|var)\s+(\w+)\s*=\s*@import\(\s*"([^"]+)"\s*\)""")
RUST_USE_RE = re.compile(r"\buse\s+([A-Za-z0-9_:{}*,\s]+);")
PY_DEF_RE = re.compile(r"^\+\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
GENERIC_DEF_RE = re.compile(
    r"^\+\s*(?:export\s+)?(?:pub\s+)?(?:async\s+)?(?:function|fn|func|class|struct|type)\s+([A-Za-z_]\w*)"
)


def is_test_file(rel: str) -> bool:
    norm = rel.replace("\\", "/")
    base = os.path.basename(norm)
    return (
        norm.startswith(("tests/", "test/", "__tests__/"))
        or "/tests/" in norm
        or "/test/" in norm
        or base.startswith("test_")
        or any(tok in base for tok in ("_test.", ".test.", ".spec."))
    )


def module_name_for_py(rel: str) -> str:
    norm = rel.replace("\\", "/")
    if norm.endswith("/__init__.py"):
        norm = norm[: -len("/__init__.py")]
    elif norm.endswith(".py"):
        norm = norm[:-3]
    return norm.replace("/", ".")


def walk_repo(repo: str) -> Tuple[List[str], List[str]]:
    code_files: List[str] = []
    doc_files: List[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, repo).replace(os.sep, "/")
            if name.endswith(CODE_EXTS):
                code_files.append(rel)
            elif name.endswith(DOC_EXTS):
                doc_files.append(rel)
    return code_files, doc_files


def resolve_relative_module(package: str, level: int, module: Optional[str]) -> str:
    if level == 0:
        return module or ""
    parts = package.split(".")
    base = parts[:len(parts) - level + 1] if level <= len(parts) else []
    return ".".join(part for part in [*base, module] if part)


def analyze_python_file(repo: str, rel: str) -> Dict[str, object]:
    full = os.path.join(repo, rel)
    try:
        with open(full, encoding="utf-8", errors="replace") as handle:
            src = handle.read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return {"module": module_name_for_py(rel), "defs": [], "imports": [], "aliases": {}, "refs": []}

    mod = module_name_for_py(rel)
    package = mod if rel.endswith("/__init__.py") else mod.rpartition(".")[0]
    defs: List[str] = []
    imports: Set[str] = set()
    aliases: Dict[str, Tuple[str, str]] = {}
    refs: List[Tuple[str, int]] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append(node.name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                local = alias.asname or alias.name.split(".")[0]
                if alias.asname:
                    aliases[local] = (alias.name, alias.name)
        elif isinstance(node, ast.ImportFrom):
            target_mod = resolve_relative_module(package, node.level, node.module)
            if target_mod:
                imports.add(target_mod)
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = (target_mod, alias.name)
                if target_mod:
                    imports.add(f"{target_mod}.{alias.name}")
        elif isinstance(node, ast.Name):
            refs.append((node.id, getattr(node, "lineno", 0)))
        elif isinstance(node, ast.Attribute):
            refs.append((node.attr, getattr(node, "lineno", 0)))

    return {
        "module": mod,
        "defs": defs,
        "imports": sorted(imports),
        "aliases": aliases,
        "refs": refs,
    }


def extract_git_targets(repo: str, rev: str) -> Tuple[List[str], List[str]]:
    diff_files = subprocess.run(["git", "diff", "--name-only", rev], cwd=repo, capture_output=True, text=True, timeout=30)
    if diff_files.returncode != 0:
        raise RuntimeError(diff_files.stderr.strip() or f"git diff against {rev} failed")
    files = [line.strip() for line in diff_files.stdout.splitlines() if line.strip() and not is_test_file(line.strip())]
    diff_text = subprocess.run(["git", "diff", rev], cwd=repo, capture_output=True, text=True, timeout=30)
    symbols: Set[str] = set()
    for line in diff_text.stdout.splitlines():
        if line.startswith("+++"):
            continue
        m = PY_DEF_RE.match(line) or GENERIC_DEF_RE.match(line)
        if m:
            symbols.add(m.group(1))
    return sorted(set(files)), sorted(symbols)


def cycles_through(start: str, edges: Dict[str, List[str]], finished: Set[str]) -> Iterator[List[str]]:
    path = [start]
    stack = [iter(edges[start])]
    closed = [False]
    blocked = {start}
    dependents: Dict[str, Set[str]] = defaultdict(set)
    while stack:
        neighbor = next(stack[-1], None)
        if neighbor is not None:
            if neighbor == start and len(path) > 1:
                yield path + [start]
                closed[-1] = True
            elif neighbor not in blocked and neighbor not in finished:
                blocked.add(neighbor)
                path.append(neighbor)
                stack.append(iter(edges.get(neighbor, [])))
                closed.append(False)
            continue

        node = path.pop()
        stack.pop()
        if closed.pop():
            if closed:
                closed[-1] = True
            # A route back to start also reopens predecessor searches that depended on it.
            pending = [node]
            while pending:
                reopened = pending.pop()
                if reopened in blocked:
                    blocked.remove(reopened)
                    pending.extend(dependents.pop(reopened, ()))
        else:
            for successor in edges.get(node, []):
                dependents[successor].add(node)


def find_cycles(graph: Dict[str, Set[str]], focus: Set[str]) -> List[List[str]]:
    edges = {node: sorted(neighbors) for node, neighbors in graph.items()}
    cycles: List[List[str]] = []
    finished: Set[str] = set()
    for start in sorted(focus or graph):
        if start in edges:
            cycles.extend(cycles_through(start, edges, finished))
            finished.add(start)
    return cycles


def analyze_impact(repo: str, target_files: Sequence[str], target_symbols: Sequence[str]) -> Dict[str, object]:
    code_files, doc_files = walk_repo(repo)
    py_info: Dict[str, Dict[str, object]] = {}
    mod_to_file: Dict[str, str] = {}
    for rel in code_files:
        if rel.endswith(".py"):
            info = analyze_python_file(repo, rel)
            py_info[rel] = info
            mod_to_file[str(info["module"])] = rel

    file_set = set(f.replace("\\", "/") for f in target_files)
    symbol_set = set(target_symbols)
    explicit_symbols = set(target_symbols)
    for rel in list(file_set):
        if rel in py_info:
            for d in py_info[rel]["defs"]:
                if not str(d).startswith("_") and str(d) != "main":
                    symbol_set.add(str(d))

    target_mods = {str(py_info[f]["module"]) for f in file_set if f in py_info}
    target_mod_tails = {m.split(".")[-1] for m in target_mods}
    target_stems = {os.path.splitext(os.path.basename(f))[0] for f in file_set}

    import_graph: Dict[str, Set[str]] = {}
    reverse_graph: Dict[str, Set[str]] = {}
    for rel, info in py_info.items():
        src_mod = str(info["module"])
        import_graph.setdefault(src_mod, set())
        for imp in info["imports"]:
            if imp in mod_to_file:
                import_graph[src_mod].add(imp)
                reverse_graph.setdefault(imp, set()).add(src_mod)

    direct_callers: List[Dict[str, object]] = []
    aliased_callers: List[Dict[str, object]] = []
    covering_tests: Set[str] = set()

    for rel in code_files:
        if rel in file_set:
            continue
        full = os.path.join(repo, rel)
        try:
            with open(full, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue

        if rel.endswith(".py") and rel in py_info:
            info = py_info[rel]
            aliases = info["aliases"]
            assert isinstance(aliases, dict)
            refs = info["refs"]
            assert isinstance(refs, list)
            matched_here = False
            for local_name, (orig_mod, orig_sym) in sorted(aliases.items()):
                mod_hit = bool(target_mods) and (orig_mod in target_mods or any(orig_mod.endswith("." + m) for m in target_mods))
                sym_hit = orig_sym in symbol_set
                if (sym_hit and (not target_mods or mod_hit)) or (mod_hit and not symbol_set):
                    ref_lines = sorted({ln for name, ln in refs if name == local_name and ln > 0})
                    if local_name != orig_sym:
                        aliased_callers.append({
                            "file": rel,
                            "symbol": orig_sym,
                            "alias": local_name,
                            "lines": ref_lines[:10],
                        })
                        matched_here = True
                    elif ref_lines:
                        direct_callers.append({
                            "file": rel,
                            "symbol": orig_sym,
                            "lines": ref_lines[:10],
                        })
                        matched_here = True
            imports_target = not target_mods or any(
                imp in target_mods or imp.split(".")[-1] in target_mod_tails
                for imp in info["imports"]
            )
            for sym in sorted(symbol_set):
                if sym in aliases:
                    continue
                if not imports_target and sym not in explicit_symbols:
                    continue
                ref_lines = sorted({ln for name, ln in refs if name == sym and ln > 0})
                if ref_lines:
                    direct_callers.append({"file": rel, "symbol": sym, "lines": ref_lines[:10]})
                    matched_here = True
            if matched_here and is_test_file(rel):
                covering_tests.add(rel)
        else:
            matched_here = False
            for match in JS_IMPORT_RE.finditer(text):
                named = match.group(2) or ""
                for orig_sym, alias in JS_ALIAS_ITEM_RE.findall(named):
                    if orig_sym in symbol_set:
                        lines = [i for i, ln in enumerate(text.splitlines(), 1) if re.search(rf"\b{re.escape(alias)}\b", ln)]
                        aliased_callers.append({"file": rel, "symbol": orig_sym, "alias": alias, "lines": lines[:10]})
                        matched_here = True
            for sym in sorted(symbol_set):
                pat = re.compile(rf"\b{re.escape(sym)}\b")
                lines = [i for i, ln in enumerate(text.splitlines(), 1) if pat.search(ln)]
                if lines:
                    direct_callers.append({"file": rel, "symbol": sym, "lines": lines[:10]})
                    matched_here = True
            for stem in sorted(target_stems):
                if not symbol_set and re.search(rf"""['"][^'"]*\b{re.escape(stem)}\b[^'"]*['"]""", text):
                    direct_callers.append({"file": rel, "symbol": stem, "lines": []})
                    matched_here = True
            if matched_here and is_test_file(rel):
                covering_tests.add(rel)

    transitive: Set[str] = set()
    roots = set(target_mods)
    if not file_set:
        roots.update(str(info["module"]) for info in py_info.values()
                     if explicit_symbols.intersection(info["defs"]))
    queue = list(roots)
    seen_mods = set(roots)
    while queue:
        cur = queue.pop(0)
        for importer in sorted(reverse_graph.get(cur, ())):
            if importer not in seen_mods:
                seen_mods.add(importer)
                imp_file = mod_to_file.get(importer, importer)
                transitive.add(imp_file)
                if is_test_file(imp_file):
                    covering_tests.add(imp_file)
                queue.append(importer)

    doc_mentions: List[Dict[str, object]] = []
    file_basenames = {os.path.basename(f) for f in file_set}
    needles = sorted(symbol_set | file_basenames)
    for rel in doc_files:
        full = os.path.join(repo, rel)
        try:
            with open(full, encoding="utf-8", errors="replace") as handle:
                lines_list = handle.read().splitlines()
        except OSError:
            continue
        for needle in needles:
            if len(needle) < 3:
                continue
            if rel.endswith(".md") and needle not in file_basenames:
                pat = re.compile(rf"`[^`\n]*\b{re.escape(needle)}\b[^`\n]*`")
            else:
                pat = re.compile(rf"\b{re.escape(needle)}\b")
            hits = [i for i, ln in enumerate(lines_list, 1) if pat.search(ln)]
            if hits:
                doc_mentions.append({"file": rel, "match": needle, "lines": hits[:8]})

    cycles = find_cycles(import_graph, roots) if roots else []

    return {
        "target_files": sorted(file_set),
        "target_symbols": sorted(symbol_set),
        "direct_callers": direct_callers,
        "aliased_callers": aliased_callers,
        "transitive_importers": sorted(transitive),
        "covering_tests": sorted(covering_tests),
        "doc_mentions": doc_mentions,
        "circular_imports": cycles,
    }


def print_report(data: Dict[str, object]) -> None:
    print(f"targets: files={data['target_files']}, symbols={data['target_symbols']}")
    print(f"aliased callers ({len(data['aliased_callers'])}):")
    for item in data["aliased_callers"]:
        print(f"  {item['file']}  {item['symbol']} as {item['alias']}  lines={item['lines']}")
    print(f"direct callers ({len(data['direct_callers'])}):")
    for item in data["direct_callers"]:
        print(f"  {item['file']}  {item['symbol']}  lines={item['lines']}")
    print(f"transitive importers ({len(data['transitive_importers'])}): {', '.join(data['transitive_importers']) or 'none'}")
    print(f"covering tests ({len(data['covering_tests'])}): {', '.join(data['covering_tests']) or 'none'}")
    print(f"doc/config mentions ({len(data['doc_mentions'])}):")
    for item in data["doc_mentions"]:
        print(f"  {item['file']}  ({item['match']})  lines={item['lines']}")
    cycles = data["circular_imports"]
    print(f"circular imports ({len(cycles)}):")
    for cyc in cycles:
        print("  " + " -> ".join(cyc))
    total_sites = len(data["direct_callers"]) + len(data["aliased_callers"]) + len(data["doc_mentions"])
    print(
        f"IMPACT: {total_sites} reference site(s), {len(data['aliased_callers'])} aliased, "
        f"{len(data['covering_tests'])} covering test file(s), {len(cycles)} cycle(s)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", default=".", help="repository root directory (default: .)")
    parser.add_argument("--symbol", action="append", default=[], help="symbol name to trace (repeatable)")
    parser.add_argument("--files", nargs="+", default=[], help="relative file paths to trace")
    parser.add_argument("--git-diff", dest="git_diff", help="trace files and symbols modified vs this git revision")
    parser.add_argument("--fail-on-cycles", action="store_true", help="exit 1 if circular imports touch the target")
    parser.add_argument("--json", dest="json_out", help="write structured impact report to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"impact.py: error: not a directory: {repo}", file=sys.stderr)
        return 2

    files = list(args.files)
    symbols = list(args.symbol)
    if args.git_diff:
        try:
            gfiles, gsyms = extract_git_targets(repo, args.git_diff)
        except RuntimeError as exc:
            print(f"impact.py: error: {exc}", file=sys.stderr)
            return 2
        files.extend(gfiles)
        symbols.extend(gsyms)

    if not files and not symbols:
        print("impact.py: error: pass --symbol, --files, or --git-diff", file=sys.stderr)
        return 2

    data = analyze_impact(repo, files, symbols)
    print_report(data)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    if args.fail_on_cycles and data["circular_imports"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
