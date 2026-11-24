#!/usr/bin/env python3
"""Model multi-rank collective overhead and gate deterministic TensorFold scaling.

Evaluates whether Tensor Parallel (TP), Expert Parallel (EP), Pipeline Parallel
(PP) or single-node `--ssd-experts` wins on your real interconnect (NVLink,
PCIe, MCDMA peer fabric, Thunderbolt 5, RoCE), and gates multi-rank serving
receipts for identical per-rank binaries, deterministic fixed-order token-hash
parity against single-rank execution, and real scaling efficiency. Uses only
the Python 3.9+ standard library.

Subcommands:
  model-collectives  Compute per-step ring/tree all-reduce latency + transfer
                     time across layers, ranks and link topology, comparing TP
                     vs PP/EP overhead and emitting a viability verdict.
  gate-ranks         Verify multi-rank receipts for uniform per-rank binary
                     hashes, single-vs-multi-rank 64-hex token_sha256 parity
                     and minimum scaling efficiency.

Exit codes: 0 viable/pass, 1 collective-bound or parity/binary/scaling fail, 2 error.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Link topology presets: (effective_unidirectional_bw_gbs, per_collective_latency_us)
TOPOLOGIES: Dict[str, Tuple[float, float]] = {
    "nvlink5": (900.0, 3.5),
    "nvlink4": (450.0, 4.5),
    "pcie5": (52.0, 14.0),
    "pcie4": (26.0, 18.0),
    "mcdma-tb5": (10.0, 22.0),
    "roce-400g": (45.0, 16.0),
}

SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


class Fail(Exception):
    """Usage, missing file or schema error -> exit 2."""


def resolve_link(topology: Optional[str], bw_gbs: Optional[float], lat_us: Optional[float]) -> Tuple[float, float]:
    bw, lat = TOPOLOGIES.get(topology or "", (0.0, 0.0))
    if bw_gbs is not None:
        bw = bw_gbs
    if lat_us is not None:
        lat = lat_us
    if bw <= 0 or lat < 0:
        raise Fail("pass --topology or positive --link-bw-gbs and nonnegative --link-latency-us")
    return bw, lat


def cmd_model_collectives(args: argparse.Namespace) -> int:
    if args.layers < 1 or args.hidden < 1 or args.batch_rows < 1 or args.ranks < 2 or args.single_rank_step_ms <= 0:
        raise Fail("layers, hidden, batch-rows, single-rank-step-ms must be positive and ranks >= 2")
    bw_gbs, lat_us = resolve_link(args.topology, args.link_bw_gbs, args.link_latency_us)

    # Ring/tree all-reduce payload per collective: batch_rows * hidden * dtype_bytes
    payload_bytes = args.batch_rows * args.hidden * args.dtype_bytes
    total_syncs = args.layers * args.syncs_per_layer
    ring_factor = 2.0 * (args.ranks - 1) / args.ranks
    # Latency scales logarithmically for tree/direct P2P collectives.
    lat_steps = max(1.0, math.ceil(math.log2(args.ranks)))
    per_sync_us = (lat_us * lat_steps) + ((payload_bytes * ring_factor) / (bw_gbs * 1e9)) * 1e6
    tp_comm_ms = (total_syncs * per_sync_us) / 1000.0

    # Idealized compute/weight time scales as 1/ranks (capped by non-parallel overhead).
    non_parallel_frac = 0.08
    ideal_compute_ms = args.single_rank_step_ms * (non_parallel_frac + (1.0 - non_parallel_frac) / args.ranks)
    tp_step_ms = ideal_compute_ms + tp_comm_ms
    tp_speedup = args.single_rank_step_ms / tp_step_ms
    tp_scaling_eff = tp_speedup / args.ranks
    comm_share_pct = round(100.0 * tp_comm_ms / tp_step_ms, 1)

    # Pipeline parallel sends only (ranks - 1) stage-boundary activations per step (much lower sync count).
    pp_syncs = args.ranks - 1
    pp_comm_ms = (pp_syncs * (lat_us + (payload_bytes / (bw_gbs * 1e9)) * 1e6)) / 1000.0

    if tp_speedup <= 1.05:
        verdict = "COLLECTIVE_BOUND_ABORT"
        rec = (
            f"TP on {args.topology or 'custom'} is slower or flat ({tp_speedup:.2f}x across {args.ranks} ranks) "
            f"because {total_syncs} all-reduces add {tp_comm_ms:.2f} ms ({comm_share_pct}% of step). "
            "Use Pipeline Parallel (PP), Expert Parallel (EP), or single-node `--ssd-experts` instead of solo-decode TP."
        )
        code = 1
    elif comm_share_pct > 35.0 or tp_scaling_eff < 0.55:
        verdict = "EP_OR_PP_PREFERRED"
        rec = (
            f"TP gives {tp_speedup:.2f}x ({tp_scaling_eff*100:.0f}% scaling efficiency) with {comm_share_pct}% collective tax; "
            "consider PP for memory-fit solo decode, EP for MoE, or wider concurrent batches before relying on TP."
        )
        code = 0
    else:
        verdict = "TP_VIABLE"
        rec = f"TP scales cleanly ({tp_speedup:.2f}x across {args.ranks} ranks, {tp_scaling_eff*100:.0f}% efficiency, {comm_share_pct}% collective tax)."
        code = 0

    out = {
        "format": "fabric-collective-model-v1",
        "topology": args.topology or "custom",
        "link_bw_gbs": bw_gbs,
        "link_latency_us": lat_us,
        "ranks": args.ranks,
        "layers": args.layers,
        "hidden": args.hidden,
        "batch_rows": args.batch_rows,
        "total_tp_syncs": total_syncs,
        "payload_kib": round(payload_bytes / 1024.0, 2),
        "single_rank_step_ms": args.single_rank_step_ms,
        "tp_comm_ms": round(tp_comm_ms, 3),
        "tp_step_ms": round(tp_step_ms, 3),
        "tp_comm_share_pct": comm_share_pct,
        "tp_speedup": round(tp_speedup, 3),
        "tp_scaling_efficiency": round(tp_scaling_eff, 3),
        "pp_boundary_comm_ms": round(pp_comm_ms, 3),
        "verdict": verdict,
        "recommendation": rec,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(
        f"fabric model ({out['topology']}, {args.ranks} ranks, M={args.batch_rows}): "
        f"{total_syncs} syncs x {out['payload_kib']} KiB -> comm={tp_comm_ms:.2f} ms ({comm_share_pct}% of {tp_step_ms:.2f} ms step)"
    )
    print(f"TP projected speedup: {tp_speedup:.2f}x ({tp_scaling_eff*100:.0f}% efficiency) | PP boundary comm={pp_comm_ms:.3f} ms")
    print(f"FABRIC: {verdict}")
    print(f"note: {rec}")
    return code


def _require_sha(val: object, label: str) -> str:
    if not isinstance(val, str) or SHA256_RE.fullmatch(val) is None:
        raise Fail(f"{label}: missing or invalid 64-hex SHA-256")
    return val.lower()


def cmd_gate_ranks(args: argparse.Namespace) -> int:
    p = Path(args.receipt)
    if not p.is_file():
        raise Fail(f"rank receipt not found: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("single_rank"), dict) or not isinstance(doc.get("multi_rank"), dict):
        raise Fail("receipt requires 'single_rank' and 'multi_rank' objects")

    sr = doc["single_rank"]
    mr = doc["multi_rank"]
    sr_cells = sr.get("cells") or {}
    mr_cells = mr.get("cells") or {}
    ranks_info = mr.get("ranks") or []
    if not sr_cells or not mr_cells or not ranks_info:
        raise Fail("single_rank.cells, multi_rank.cells and multi_rank.ranks must be non-empty")

    if not isinstance(ranks_info, list) or len(ranks_info) < 2:
        raise Fail("multi_rank.ranks must be a list of at least 2 ranks")

    rank_ids = set()
    bin_hashes = set()
    for index, record in enumerate(ranks_info):
        rank = record.get("rank") if isinstance(record, dict) else None
        if type(rank) is not int or rank < 0:
            raise Fail(f"multi_rank.ranks[{index}] requires a nonnegative integer rank ID")
        if rank in rank_ids:
            raise Fail(f"duplicate rank ID {rank} in multi_rank.ranks")
        rank_ids.add(rank)
        bin_hashes.add(_require_sha(record.get("binary_sha256"), f"rank[{rank}] binary"))
    n_ranks = len(rank_ids)
    issues: List[str] = []
    if len(bin_hashes) != 1:
        issues.append(f"rank binary mismatch: found {len(bin_hashes)} distinct binary hashes across {n_ranks} ranks")

    # 2. Deterministic fixed-order reduction parity against single-rank reference
    if set(sr_cells) != set(mr_cells):
        raise Fail("single_rank and multi_rank receipts cover different cell sets")
    parity_mismatches: List[str] = []
    for cname in sorted(sr_cells):
        s_sha = _require_sha(sr_cells[cname].get("token_sha256"), f"single_rank[{cname}]")
        m_sha = _require_sha(mr_cells[cname].get("token_sha256"), f"multi_rank[{cname}]")
        if s_sha != m_sha:
            parity_mismatches.append(cname)
    if parity_mismatches and not args.allow_reduction_drift:
        issues.append(f"reduction-order token drift vs single-rank on cell(s): {', '.join(parity_mismatches)}")

    # 3. Scaling efficiency check
    sr_tps = float(sr.get("decode_tps") or 0.0)
    mr_tps = float(mr.get("decode_tps") or 0.0)
    if not math.isfinite(sr_tps) or not math.isfinite(mr_tps) or sr_tps <= 0 or mr_tps <= 0:
        raise Fail("single_rank.decode_tps and multi_rank.decode_tps must be finite and positive")
    speedup = mr_tps / sr_tps
    eff = speedup / n_ranks
    if eff < args.min_scaling_eff:
        issues.append(f"scaling efficiency {eff*100:.1f}% ({speedup:.2f}x on {n_ranks} ranks) is below {args.min_scaling_eff*100:.1f}% gate")

    out = {
        "ranks": n_ranks,
        "uniform_binary_sha256": len(bin_hashes) == 1,
        "parity_mismatches": parity_mismatches,
        "single_rank_tps": sr_tps,
        "multi_rank_tps": mr_tps,
        "speedup": round(speedup, 3),
        "scaling_efficiency": round(eff, 3),
        "issues": issues,
        "passed": len(issues) == 0,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(
        f"multi-rank gate ({n_ranks} ranks): {sr_tps:.1f} -> {mr_tps:.1f} tok/s "
        f"({speedup:.2f}x, {eff*100:.1f}% efficiency) | parity_mismatches={len(parity_mismatches)}"
    )
    for iss in issues:
        print(f"  [ISSUE] {iss}")
    if issues:
        print("FABRIC GATE: FAIL")
        return 1
    print("FABRIC GATE: PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mod = sub.add_parser("model-collectives", help="model per-step TP collective latency vs PP/EP on your link topology")
    p_mod.add_argument("--layers", type=int, required=True)
    p_mod.add_argument("--hidden", type=int, required=True)
    p_mod.add_argument("--batch-rows", type=int, default=1, help="active rows per decode step (default: 1)")
    p_mod.add_argument("--dtype-bytes", type=int, default=2, help="activation bytes per element (default: 2 for BF16)")
    p_mod.add_argument("--ranks", type=int, required=True, help="number of GPUs or hosts (>= 2)")
    p_mod.add_argument("--topology", choices=tuple(TOPOLOGIES))
    p_mod.add_argument("--link-bw-gbs", type=float)
    p_mod.add_argument("--link-latency-us", type=float)
    p_mod.add_argument("--syncs-per-layer", type=int, default=2, help="all-reduces per transformer layer (default: 2)")
    p_mod.add_argument("--single-rank-step-ms", type=float, required=True, help="measured 1-rank step time in ms")
    p_mod.add_argument("--json", dest="json_out")

    p_gate = sub.add_parser("gate-ranks", help="verify per-rank binary hashes, deterministic reduction parity and scaling")
    p_gate.add_argument("receipt", help="multi-rank serving receipt JSON")
    p_gate.add_argument("--min-scaling-eff", type=float, default=0.55, help="minimum speedup/ranks ratio (default: 0.55)")
    p_gate.add_argument("--allow-reduction-drift", action="store_true", help="allow documented FP reduction drift with maintainer sign-off")
    p_gate.add_argument("--json", dest="json_out")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "model-collectives":
            return cmd_model_collectives(args)
        if args.cmd == "gate-ranks":
            return cmd_gate_ranks(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"fabric_plan.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
