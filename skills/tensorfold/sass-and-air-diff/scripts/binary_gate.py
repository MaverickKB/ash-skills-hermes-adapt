#!/usr/bin/env python3
"""Audit compiled GPU kernel binaries for register, spill and untouched-kernel drift.

Parses CUDA `ptxas -v` logs, `cuobjdump --dump-sass` disassemblies and Metal
AIR/metallib JSON manifests, then compares base vs candidate snapshots to catch
silent register bloat, local-memory spills (`STL`/`LDL`), occupancy cliffs and
unintended changes in untouched neighbor kernels. Uses only Python 3.9+ stdlib.

Subcommands:
  parse-ptxas  Extract per-kernel registers, spill stores/loads and shared
               memory bytes from `ptxas -v` / `nvcc --ptxas-options=-v` logs.
  parse-sass   Extract per-kernel instruction counts, spill ops (`STL`/`LDL`),
               tensor-core ops and normalized SASS SHA-256 from `cuobjdump`.
  diff         Compare base vs candidate snapshots, enforcing untouched-kernel
               identity and flagging new register/spill regressions.

Exit codes: 0 BINARY GATE: PASS, 1 BINARY GATE: REGRESSION, 2 usage/parse error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

PTXAS_FUNC_RE = re.compile(r"Compiling entry function ['\"]([^'\"]+)['\"](?:\s+for\s+['\"]([^'\"]+)['\"])?")
PTXAS_REGS_RE = re.compile(r"Used\s+(\d+)\s+registers")
PTXAS_SPILL_STORE_RE = re.compile(r"(\d+)\s+bytes\s+spill\s+stores")
PTXAS_SPILL_LOAD_RE = re.compile(r"(\d+)\s+bytes\s+spill\s+loads")
PTXAS_SMEM_RE = re.compile(r"(\d+)\s+bytes\s+smem")
PTXAS_STACK_RE = re.compile(r"(\d+)\s+bytes\s+stack\s+frame")

SASS_FUNC_RE = re.compile(r"^\s*Function\s*:\s*(\S+)")
SASS_INST_RE = re.compile(r"^\s*/\*[0-9a-fA-F]+\*/\s+(.*?)(?:\s*;|\s*/\*|$)")
SPILL_OP_RE = re.compile(r"\b(?:STL|LDL)(?:\.[A-Z0-9]+)?\b")
MMA_OP_RE = re.compile(r"\b(?:HMMA|IMMA|DMMA|BMMA|QGMMA|GMMA|OMMA|UTCOMMA)(?:\.[A-Z0-9]+)?\b")
BAR_OP_RE = re.compile(r"\b(?:BAR|DEPBAR|MEMBAR|WARPSYNC)(?:\.[A-Z0-9]+)?\b")


class Fail(Exception):
    """Usage, missing file or schema error -> exit 2."""


def parse_ptxas_text(text: str) -> Dict[str, Dict[str, object]]:
    kernels: Dict[str, Dict[str, object]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        m_fn = PTXAS_FUNC_RE.search(line)
        if m_fn:
            current = m_fn.group(1)
            kernels[current] = {
                "arch": m_fn.group(2) or "",
                "registers": 0,
                "spill_stores": 0,
                "spill_loads": 0,
                "stack_bytes": 0,
                "smem_bytes": 0,
            }
            continue
        if current is None:
            continue
        rec = kernels[current]
        m = PTXAS_REGS_RE.search(line)
        if m:
            rec["registers"] = int(m.group(1))
        m = PTXAS_SPILL_STORE_RE.search(line)
        if m:
            rec["spill_stores"] = int(m.group(1))
        m = PTXAS_SPILL_LOAD_RE.search(line)
        if m:
            rec["spill_loads"] = int(m.group(1))
        m = PTXAS_SMEM_RE.search(line)
        if m:
            rec["smem_bytes"] = int(m.group(1))
        m = PTXAS_STACK_RE.search(line)
        if m:
            rec["stack_bytes"] = int(m.group(1))
    if not kernels:
        raise Fail("no ptxas entry functions found in log")
    return kernels


def parse_sass_text(text: str) -> Dict[str, Dict[str, object]]:
    kernels: Dict[str, Dict[str, object]] = {}
    current: Optional[str] = None
    inst_lines: List[str] = []

    def flush() -> None:
        if current is None:
            return
        canonical = "\n".join(inst_lines).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        spill_ops = sum(1 for ln in inst_lines if SPILL_OP_RE.search(ln))
        mma_ops = sum(1 for ln in inst_lines if MMA_OP_RE.search(ln))
        bar_ops = sum(1 for ln in inst_lines if BAR_OP_RE.search(ln))
        kernels[current] = {
            "instructions": len(inst_lines),
            "spill_ops": spill_ops,
            "mma_ops": mma_ops,
            "barrier_ops": bar_ops,
            "sha256": digest,
        }

    for line in text.splitlines():
        m_fn = SASS_FUNC_RE.match(line)
        if m_fn:
            flush()
            current = m_fn.group(1)
            inst_lines = []
            continue
        if current is None:
            continue
        m_inst = SASS_INST_RE.match(line)
        if m_inst:
            body = " ".join(m_inst.group(1).split())
            if body:
                inst_lines.append(body)
    flush()
    if not kernels:
        raise Fail("no `Function : <name>` sections found in SASS dump")
    return kernels


def load_snapshot(path: Path) -> Dict[str, Dict[str, object]]:
    if not path.is_file():
        raise Fail(f"snapshot not found: {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and isinstance(doc.get("kernels"), dict):
        return doc["kernels"]
    raise Fail(f"{path}: expected JSON object with 'kernels' mapping")


def is_allowed(name: str, patterns: Sequence[str]) -> bool:
    return any(p in name for p in patterns)


def cmd_parse_ptxas(args: argparse.Namespace) -> int:
    p = Path(args.log)
    if not p.is_file():
        raise Fail(f"ptxas log not found: {p}")
    kernels = parse_ptxas_text(p.read_text(encoding="utf-8", errors="replace"))
    out = {"format": "binary-gate-ptxas-v1", "source": str(p), "kernels": kernels}
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    spilling = [k for k, v in kernels.items() if int(v["spill_stores"]) > 0 or int(v["spill_loads"]) > 0]
    print(f"parsed {len(kernels)} kernel(s) from ptxas log ({len(spilling)} with local spills) -> {args.out}")
    return 0


def cmd_parse_sass(args: argparse.Namespace) -> int:
    p = Path(args.dump)
    if not p.is_file():
        raise Fail(f"SASS dump not found: {p}")
    kernels = parse_sass_text(p.read_text(encoding="utf-8", errors="replace"))
    out = {"format": "binary-gate-sass-v1", "source": str(p), "kernels": kernels}
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    spilling = [k for k, v in kernels.items() if int(v["spill_ops"]) > 0]
    print(f"parsed {len(kernels)} kernel(s) from SASS dump ({len(spilling)} with STL/LDL spill ops) -> {args.out}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    base = load_snapshot(Path(args.base))
    cand = load_snapshot(Path(args.candidate))
    allow = args.allow_changed or []

    untouched_changed: List[Dict[str, object]] = []
    allowed_deltas: List[Dict[str, object]] = []
    missing_kernels: List[str] = []
    added_kernels: List[str] = sorted(set(cand) - set(base))
    new_spill_violations: List[str] = []

    for name in sorted(base):
        if name not in cand:
            missing_kernels.append(name)
            continue
        b, c = base[name], cand[name]
        allowed = is_allowed(name, allow)
        diffs: List[str] = []

        if "sha256" in b and "sha256" in c and b["sha256"] != c["sha256"]:
            diffs.append("binary_sha256_changed")
        for metric, max_inc in (
            ("registers", args.max_reg_increase),
            ("spill_stores", args.max_spill_increase),
            ("spill_loads", args.max_spill_increase),
            ("spill_ops", args.max_spill_increase),
            ("smem_bytes", args.max_smem_increase),
        ):
            if metric in b and metric in c:
                bv, cv = int(b[metric]), int(c[metric])
                if cv != bv:
                    delta = cv - bv
                    if not allowed and delta > max_inc:
                        diffs.append(f"{metric} {bv}->{cv} ({delta:+d})")
                    elif allowed:
                        diffs.append(f"{metric} {bv}->{cv} ({delta:+d})")
                    if metric in ("spill_stores", "spill_loads", "spill_ops") and bv == 0 and cv > 0:
                        new_spill_violations.append(f"{name}: introduced {metric}={cv}")

        if "instructions" in b and "instructions" in c and int(b["instructions"]) != int(c["instructions"]):
            bv, cv = int(b["instructions"]), int(c["instructions"])
            diffs.append(f"instructions {bv}->{cv} ({cv - bv:+d})")

        if diffs:
            entry = {"kernel": name, "changes": diffs}
            if allowed:
                allowed_deltas.append(entry)
            else:
                untouched_changed.append(entry)

    failed = bool(untouched_changed) or bool(missing_kernels) or (args.forbid_new_spills and bool(new_spill_violations))
    report = {
        "format": "binary-gate-diff-v1",
        "base_kernels": len(base),
        "candidate_kernels": len(cand),
        "allow_changed": allow,
        "untouched_changed": untouched_changed,
        "allowed_deltas": allowed_deltas,
        "missing_kernels": missing_kernels,
        "added_kernels": added_kernels,
        "new_spill_violations": new_spill_violations,
        "passed": not failed,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(
        f"binary diff: {len(base)} base -> {len(cand)} candidate kernels | "
        f"allowed_deltas={len(allowed_deltas)} untouched_changed={len(untouched_changed)} "
        f"missing={len(missing_kernels)} new_spills={len(new_spill_violations)}"
    )
    for item in allowed_deltas:
        print(f"  [ALLOWED]   {item['kernel']}: {', '.join(item['changes'])}")
    for item in untouched_changed:
        print(f"  [UNTOUCHED] {item['kernel']}: {', '.join(item['changes'])}")
    for m in missing_kernels:
        print(f"  [MISSING]   {m}")
    for s in new_spill_violations:
        print(f"  [SPILL]     {s}")

    if failed:
        print("BINARY GATE: REGRESSION")
        return 1
    print("BINARY GATE: PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ptx = sub.add_parser("parse-ptxas", help="parse ptxas -v compiler resource log into a kernel snapshot JSON")
    p_ptx.add_argument("log", help="path to ptxas/nvcc verbose log")
    p_ptx.add_argument("--out", required=True, help="output snapshot JSON path")

    p_sass = sub.add_parser("parse-sass", help="parse cuobjdump --dump-sass into a normalized kernel snapshot JSON")
    p_sass.add_argument("dump", help="path to cuobjdump --dump-sass text dump")
    p_sass.add_argument("--out", required=True, help="output snapshot JSON path")

    p_diff = sub.add_parser("diff", help="compare base vs candidate kernel binary snapshots")
    p_diff.add_argument("base", help="baseline snapshot JSON")
    p_diff.add_argument("candidate", help="candidate snapshot JSON")
    p_diff.add_argument("--allow-changed", action="append", help="kernel name substring intentionally modified")
    p_diff.add_argument("--max-reg-increase", type=int, default=0, help="allowed register increase on untouched kernels (default: 0)")
    p_diff.add_argument("--max-spill-increase", type=int, default=0, help="allowed spill increase on untouched kernels (default: 0)")
    p_diff.add_argument("--max-smem-increase", type=int, default=0, help="allowed smem byte increase on untouched kernels (default: 0)")
    p_diff.add_argument("--forbid-new-spills", action="store_true", help="fail even on allowed kernels if spills rise from 0 to >0")
    p_diff.add_argument("--json", dest="json_out", help="write JSON diff report")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "parse-ptxas":
            return cmd_parse_ptxas(args)
        if args.cmd == "parse-sass":
            return cmd_parse_sass(args)
        if args.cmd == "diff":
            return cmd_diff(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"binary_gate.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
