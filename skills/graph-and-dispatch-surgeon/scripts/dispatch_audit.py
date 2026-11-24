#!/usr/bin/env python3
"""Audit between-pass GPU stalls, graph recaptures and dynamic-width exactness.

Pinpoints why a served decode loop leaves GPU silicon idle between passes:
host-side acceptance syncs, CUDA graph recaptures on row-width shifts, Metal
command-buffer waits, serialized grammar/proposer CPU work, and enqueue gaps.
Also gates exact 64-hex `token_sha256` invariance across forced row widths,
draft depths and graph-on vs graph-off modes. Uses only Python 3.9+ stdlib.

Subcommands:
  audit-gaps         Classify per-step between-pass bubbles and recapture storms
                     from a JSON/JSONL step trace and compute zero-stall gain.
  verify-invariance  Prove graph-on, indirect-dispatch and forced dynamic-width
                     runs match the plain scalar reference hash for hash with
                     zero post-warmup recaptures.

Exit codes: 0 healthy/pass, 1 stalled or invariance/recapture fail, 2 input error.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


class Fail(Exception):
    """Usage, missing file or schema error -> exit 2."""


def load_steps(path: Path) -> List[Dict[str, object]]:
    if not path.is_file():
        raise Fail(f"step trace not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise Fail(f"{path}: trace is empty")
    if text.startswith("[") or (text.startswith("{") and "\n" not in text):
        parsed = json.loads(text)
        rows = parsed if isinstance(parsed, list) else (parsed.get("steps") or [parsed])
    else:
        rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    out = [r for r in rows if isinstance(r, dict)]
    if not out:
        raise Fail(f"{path}: no step records found")
    return out


def cmd_audit_gaps(args: argparse.Namespace) -> int:
    steps = load_steps(Path(args.trace))
    gpu_list, sync_list, enq_list, gram_list, draft_list, wall_list = [], [], [], [], [], []
    recaptures = 0
    width_transitions = 0
    recapture_on_width_change = 0
    prev_rows = None

    for idx, s in enumerate(steps, 1):
        g = float(s.get("gpu_ms") or 0.0)
        sy = float(s.get("host_sync_ms") or s.get("cmdbuf_wait_ms") or 0.0)
        eq = float(s.get("enqueue_gap_ms") or 0.0)
        gr = float(s.get("grammar_ms") or 0.0)
        dr = float(s.get("draft_ms") or 0.0)
        w = float(s.get("wall_ms") or s.get("step_ms") or (g + sy + eq + gr + dr))
        if not all(math.isfinite(x) and x >= 0 for x in (g, sy, eq, gr, dr, w)) or w <= 0:
            raise Fail(f"step {idx}: invalid timing values")
        gpu_list.append(g)
        sync_list.append(sy)
        enq_list.append(eq)
        gram_list.append(gr)
        draft_list.append(dr)
        wall_list.append(w)

        recap = bool(s.get("graph_recaptured") or s.get("pipeline_rebuilt"))
        if recap:
            recaptures += 1
        rows = s.get("rows")
        if prev_rows is not None and rows is not None and rows != prev_rows:
            width_transitions += 1
            if recap:
                recapture_on_width_change += 1
        prev_rows = rows

    total_wall = sum(wall_list)
    total_gpu = sum(gpu_list)
    total_sync = sum(sync_list)
    total_enq = sum(enq_list)
    total_serial = sum(gram_list) + sum(draft_list)

    gpu_busy_pct = round(100.0 * total_gpu / total_wall, 1)
    sync_pct = round(100.0 * total_sync / total_wall, 1)
    enq_pct = round(100.0 * total_enq / total_wall, 1)
    serial_pct = round(100.0 * total_serial / total_wall, 1)
    recap_pct = round(100.0 * recaptures / len(steps), 1)

    target_wall = total_gpu / 0.97 if total_gpu > 0 else total_wall
    zero_stall_gain_pct = round(max(0.0, ((total_wall / max(1e-6, target_wall)) - 1.0) * 100.0), 1)

    if recap_pct > 5.0:
        pathology = "GRAPH_RECAPTURE_STORM"
        fix = "Indirection through device-resident metadata buffers (live row count M, tree mask, paged KV block table) so one captured graph or Metal ICB handles varying widths without recapture."
    elif gpu_busy_pct >= args.min_gpu_busy_pct and recap_pct <= 1.0:
        pathology = "DISPATCH_HEALTHY"
        fix = "Between-pass dispatch is already tight; move to kernel-contract or one-stream-lanes."
    else:
        dominant = max(
            [
                (sync_pct, "HOST_SYNC_STALL", "Move greedy and seeded-sampled acceptance onto the GPU and remove synchronous host waits between passes."),
                (serial_pct, "SERIAL_PROPOSER_OR_GRAMMAR", "Overlap late_draft and grammar_parallel beside the in-flight verification pass."),
                (enq_pct, "ENQUEUE_GAP_STARVATION", "Capture the decode step into a CUDA graph or Metal indirect command buffer to eliminate host launch gaps."),
            ],
            key=lambda x: x[0],
        )
        pathology, fix = dominant[1], dominant[2]

    report = {
        "format": "dispatch-audit-v1",
        "steps": len(steps),
        "median_wall_ms": round(statistics.median(wall_list), 3),
        "median_gpu_ms": round(statistics.median(gpu_list), 3),
        "gpu_busy_pct": gpu_busy_pct,
        "host_sync_pct": sync_pct,
        "enqueue_gap_pct": enq_pct,
        "serial_proposer_grammar_pct": serial_pct,
        "recaptures": recaptures,
        "recapture_rate_pct": recap_pct,
        "width_transitions": width_transitions,
        "recaptures_on_width_change": recapture_on_width_change,
        "zero_stall_ceiling_gain_pct": zero_stall_gain_pct,
        "pathology": pathology,
        "recommended_fix": fix,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(
        f"dispatch ({len(steps)} steps): GPU busy={gpu_busy_pct}% | host_sync={sync_pct}% | "
        f"enqueue_gap={enq_pct}% | serial_draft/grammar={serial_pct}% | recaptures={recaptures} ({recap_pct}%)"
    )
    print(f"zero-stall ceiling: +{zero_stall_gain_pct:.1f}% | pathology={pathology}")
    print(f"fix: {fix}")
    if pathology == "DISPATCH_HEALTHY":
        print("DISPATCH: HEALTHY")
        return 0
    print(f"DISPATCH: STALLED ({pathology})")
    return 1


def _require_sha(val: object, label: str) -> str:
    if not isinstance(val, str) or SHA256_RE.fullmatch(val) is None:
        raise Fail(f"{label}: expected full 64-hex token_sha256")
    return val.lower()


def cmd_verify_invariance(args: argparse.Namespace) -> int:
    p = Path(args.matrix)
    if not p.is_file():
        raise Fail(f"invariance matrix not found: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("runs"), list) or not doc["runs"]:
        raise Fail("matrix JSON requires 'reference_token_sha256' and non-empty 'runs' list")
    ref_sha = _require_sha(doc.get("reference_token_sha256"), "reference_token_sha256")

    hash_mismatches: List[str] = []
    recapture_fails: List[str] = []
    widths_seen = set()

    for idx, r in enumerate(doc["runs"], 1):
        label = str(r.get("label") or f"run_{idx}")
        got_sha = _require_sha(r.get("token_sha256"), label)
        if got_sha != ref_sha:
            hash_mismatches.append(label)
        post_recap = int(r.get("post_warmup_recaptures") or 0)
        if post_recap > args.max_post_warmup_recaptures:
            recapture_fails.append(f"{label} ({post_recap} recaptures)")
        if r.get("rows") is not None:
            widths_seen.add(int(r["rows"]))

    passed = (not hash_mismatches) and (not recapture_fails)
    out = {
        "reference_token_sha256": ref_sha,
        "runs_checked": len(doc["runs"]),
        "widths_covered": sorted(widths_seen),
        "hash_mismatches": hash_mismatches,
        "recapture_failures": recapture_fails,
        "passed": passed,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(
        f"invariance: {len(doc['runs'])} runs (widths={sorted(widths_seen)}) | "
        f"hash_mismatches={len(hash_mismatches)} | recapture_failures={len(recapture_fails)}"
    )
    for m in hash_mismatches:
        print(f"  [HASH MISMATCH] {m}")
    for rf in recapture_fails:
        print(f"  [RECAPTURE]     {rf}")
    if passed:
        print("INVARIANCE: PASS")
        return 0
    print("INVARIANCE: FAIL")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gap = sub.add_parser("audit-gaps", help="classify between-pass decode stalls and graph recapture storms")
    p_gap.add_argument("trace", help="step trace JSON/JSONL file")
    p_gap.add_argument("--min-gpu-busy-pct", type=float, default=90.0, help="GPU busy %% threshold for HEALTHY (default: 90)")
    p_gap.add_argument("--json", dest="json_out")

    p_inv = sub.add_parser("verify-invariance", help="gate exact token_sha256 and zero recaptures across dynamic widths")
    p_inv.add_argument("matrix", help="JSON matrix with reference_token_sha256 and forced-width runs")
    p_inv.add_argument("--max-post-warmup-recaptures", type=int, default=0)
    p_inv.add_argument("--json", dest="json_out")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "audit-gaps":
            return cmd_audit_gaps(args)
        if args.cmd == "verify-invariance":
            return cmd_verify_invariance(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"dispatch_audit.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
