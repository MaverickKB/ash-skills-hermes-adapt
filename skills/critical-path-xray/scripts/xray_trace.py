#!/usr/bin/env python3
"""Parse served TensorFold traces, compute hardware rooflines and Amdahl bounds.

Ingests Nsight Systems (`nsys`) kernel summary CSVs, TensorFold JSON/JSONL step
traces, or `FZ_ROUND_TIMES`/`FZ_ROUND_LOG` key=value logs to break down where a
served request spends wall time (GPU compute/BW, host dispatch, idle gaps),
classify each kernel against chip roofline peaks, verify expected kernels
actually executed in the served path, and compute non-overlapping Amdahl
ceilings. Uses only the Python 3.9+ standard library.

Subcommands:
  audit     Parse a served trace (CSV, JSON/JSONL or FZ_ROUND_TIMES log),
            compute critical-path shares, Amdahl ceilings, roofline utilization
            and verify `--expect-kernel` actually ran.
  roofline  Compute arithmetic intensity, achieved GB/s and TFLOP/s, and
            BW-vs-compute bound classification for a single kernel measurement.
  bound     Screen and rank candidate optimizations by non-overlapping Amdahl
            end-to-end ceilings (counting shared overlap groups once).

Exit codes: 0 clean, 1 expected kernel missing or all candidates below screen, 2 error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Reference hardware peaks: (peak_memory_bw_gbs, peak_fp16_tflops)
CHIP_PROFILES: Dict[str, Tuple[float, float]] = {
    "rtx5090": (1792.0, 419.0),
    "rtx4090": (1008.0, 330.0),
    "h100": (3350.0, 989.0),
    "b200": (8000.0, 2250.0),
    "m3-max": (400.0, 28.0),
    "m4-max": (546.0, 36.0),
    "m5-max": (614.0, 48.0),
    "m3-ultra": (800.0, 56.0),
}

KV_NUM_RE = re.compile(r"([a-zA-Z0-9_]+)=([0-9]+(?:\.[0-9]+)?)")


class Fail(Exception):
    """Usage, missing file or parse error -> exit 2."""


def resolve_peaks(chip: Optional[str], peak_bw: Optional[float], peak_tflops: Optional[float]) -> Tuple[float, float]:
    bw, tf = CHIP_PROFILES.get(chip or "", (0.0, 0.0))
    if peak_bw is not None:
        bw = peak_bw
    if peak_tflops is not None:
        tf = peak_tflops
    return bw, tf


def classify_kernel(
    gpu_ms: float,
    bytes_moved: float,
    flops: float,
    launches: int,
    peak_bw_gbs: float,
    peak_tflops: float,
) -> Dict[str, object]:
    sec = max(1e-9, gpu_ms / 1000.0)
    achieved_bw = (bytes_moved / 1e9) / sec if bytes_moved > 0 else 0.0
    achieved_tf = (flops / 1e12) / sec if flops > 0 else 0.0
    ai = (flops / bytes_moved) if (flops > 0 and bytes_moved > 0) else 0.0
    bw_util = (achieved_bw / peak_bw_gbs) if (peak_bw_gbs > 0 and achieved_bw > 0) else None
    tf_util = (achieved_tf / peak_tflops) if (peak_tflops > 0 and achieved_tf > 0) else None
    us_per_launch = (gpu_ms * 1000.0 / launches) if launches > 0 else gpu_ms * 1000.0

    regime = "UNCLASSIFIED"
    if launches > 0 and us_per_launch < 15.0 and (bw_util is None or bw_util < 0.35):
        regime = "LAUNCH_BOUND"
    elif bw_util is not None and tf_util is not None:
        if bw_util >= 0.65 and bw_util >= tf_util:
            regime = "BW_BOUND"
        elif tf_util >= 0.55 and tf_util > bw_util:
            regime = "COMPUTE_BOUND"
        else:
            regime = "UNDERUTILIZED"
    elif bw_util is not None:
        regime = "BW_BOUND" if bw_util >= 0.65 else "UNDERUTILIZED"
    elif tf_util is not None:
        regime = "COMPUTE_BOUND" if tf_util >= 0.55 else "UNDERUTILIZED"

    return {
        "achieved_bw_gbs": round(achieved_bw, 2),
        "achieved_tflops": round(achieved_tf, 2),
        "arithmetic_intensity": round(ai, 2),
        "bw_util_pct": round(bw_util * 100.0, 1) if bw_util is not None else None,
        "compute_util_pct": round(tf_util * 100.0, 1) if tf_util is not None else None,
        "us_per_launch": round(us_per_launch, 2),
        "regime": regime,
    }


def parse_trace(path: Path) -> Dict[str, object]:
    if not path.is_file():
        raise Fail(f"trace file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise Fail(f"{path}: trace file is empty")

    # 1. Nsight Systems (`nsys stats --report cuda_gpu_kern_sum --format csv`)
    if path.suffix.lower() == ".csv":
        kernels: List[Dict[str, object]] = []
        total_gpu_ms = 0.0
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                raise Fail(f"{path}: empty CSV header")
            for row in reader:
                name = (row.get("Name") or row.get("Kernel Name") or row.get("kernel") or "").strip()
                if not name:
                    continue
                if row.get("Total Time (ns)"):
                    ms = float(row["Total Time (ns)"]) / 1e6
                elif row.get("Total Time (ms)") or row.get("gpu_ms"):
                    ms = float(row.get("Total Time (ms)") or row.get("gpu_ms"))
                else:
                    raise Fail(f"{path}: CSV missing 'Total Time (ns)' or 'gpu_ms' column")
                launches = int(float(row.get("Instances") or row.get("launches") or 1))
                b = float(row.get("bytes") or 0.0)
                fl = float(row.get("flops") or 0.0)
                total_gpu_ms += ms
                kernels.append({"name": name, "gpu_ms": ms, "launches": launches, "bytes": b, "flops": fl})
        if not kernels:
            raise Fail(f"{path}: no kernel rows found in CSV")
        return {
            "source_format": "nsys_csv",
            "wall_ms": total_gpu_ms,
            "gpu_ms": total_gpu_ms,
            "host_ms": 0.0,
            "idle_ms": 0.0,
            "kernels": kernels,
        }

    # 2. JSON or JSONL TensorFold step trace
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            records = parsed if isinstance(parsed, list) else (parsed.get("steps") or [parsed])
        except json.JSONDecodeError:
            records = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        wall_vals, gpu_vals, host_vals, idle_vals = [], [], [], []
        kern_agg: Dict[str, Dict[str, float]] = defaultdict(lambda: {"gpu_ms": 0.0, "launches": 0.0, "bytes": 0.0, "flops": 0.0})
        n_steps = 0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            n_steps += 1
            g = float(rec.get("gpu_ms") or 0.0)
            h = float(rec.get("host_ms") or 0.0)
            i = float(rec.get("idle_ms") or 0.0)
            w = float(rec.get("wall_ms") or rec.get("step_ms") or (g + h + i))
            if g == 0.0 and isinstance(rec.get("kernels"), list):
                g = sum(float(k.get("gpu_ms") or 0.0) for k in rec["kernels"] if isinstance(k, dict))
                if w <= 0:
                    w = g + h + i
            wall_vals.append(w)
            gpu_vals.append(g)
            host_vals.append(h)
            idle_vals.append(max(0.0, i if i > 0 else (w - g - h)))
            for k in rec.get("kernels") or []:
                if not isinstance(k, dict) or not k.get("name"):
                    continue
                slot = kern_agg[str(k["name"])]
                slot["gpu_ms"] += float(k.get("gpu_ms") or 0.0)
                slot["launches"] += float(k.get("launches") or 1.0)
                slot["bytes"] += float(k.get("bytes") or 0.0)
                slot["flops"] += float(k.get("flops") or 0.0)
        if n_steps == 0:
            raise Fail(f"{path}: no step records found in JSON")
        kernels = [
            {
                "name": name,
                "gpu_ms": v["gpu_ms"] / n_steps,
                "launches": max(1, int(round(v["launches"] / n_steps))),
                "bytes": v["bytes"] / n_steps,
                "flops": v["flops"] / n_steps,
            }
            for name, v in kern_agg.items()
        ]
        return {
            "source_format": "tensorfold_json",
            "steps": n_steps,
            "wall_ms": statistics.median(wall_vals),
            "gpu_ms": statistics.median(gpu_vals),
            "host_ms": statistics.median(host_vals),
            "idle_ms": statistics.median(idle_vals),
            "kernels": kernels,
        }

    # 3. FZ_ROUND_TIMES / key=value text log
    wall_vals, gpu_vals, host_vals, idle_vals = [], [], [], []
    for line in text.splitlines():
        kv = {m.group(1): float(m.group(2)) for m in KV_NUM_RE.finditer(line)}
        if not kv or not any(k in kv for k in ("gpu_ms", "wall_ms", "step_ms")):
            continue
        g = kv.get("gpu_ms", 0.0)
        h = kv.get("host_ms", 0.0)
        i = kv.get("idle_ms", 0.0)
        w = kv.get("wall_ms", kv.get("step_ms", g + h + i))
        wall_vals.append(w)
        gpu_vals.append(g)
        host_vals.append(h)
        idle_vals.append(max(0.0, i if "idle_ms" in kv else (w - g - h)))
    if not wall_vals:
        raise Fail(f"{path}: unrecognized trace format (expected nsys CSV, TensorFold JSON/JSONL, or FZ_ROUND_TIMES key=value log)")
    return {
        "source_format": "fz_round_log",
        "steps": len(wall_vals),
        "wall_ms": statistics.median(wall_vals),
        "gpu_ms": statistics.median(gpu_vals),
        "host_ms": statistics.median(host_vals),
        "idle_ms": statistics.median(idle_vals),
        "kernels": [],
    }


def cmd_audit(args: argparse.Namespace) -> int:
    parsed = parse_trace(Path(args.trace))
    peak_bw, peak_tf = resolve_peaks(args.chip, args.peak_bw_gbs, args.peak_tflops)
    wall_ms = max(1e-6, float(parsed["wall_ms"]))
    gpu_ms = float(parsed["gpu_ms"])
    host_ms = float(parsed["host_ms"])
    idle_ms = float(parsed["idle_ms"])

    gpu_share = round(100.0 * gpu_ms / wall_ms, 1)
    host_share = round(100.0 * host_ms / wall_ms, 1)
    idle_share = round(100.0 * idle_ms / wall_ms, 1)
    non_gpu_share = round(100.0 * (host_ms + idle_ms) / wall_ms, 1)

    kern_rows = []
    for k in sorted(parsed["kernels"], key=lambda x: -float(x["gpu_ms"])):
        kms = float(k["gpu_ms"])
        share = kms / wall_ms
        amdahl_max_pct = round(((1.0 / max(1e-6, 1.0 - share)) - 1.0) * 100.0, 1) if share < 0.99 else 9900.0
        roof = classify_kernel(kms, float(k.get("bytes") or 0.0), float(k.get("flops") or 0.0), int(k.get("launches") or 1), peak_bw, peak_tf)
        kern_rows.append({
            "name": k["name"],
            "gpu_ms": round(kms, 3),
            "wall_share_pct": round(share * 100.0, 1),
            "amdahl_max_gain_pct": amdahl_max_pct,
            "launches": int(k.get("launches") or 1),
            **roof,
        })

    missing_expected: List[str] = []
    present_names = [str(k["name"]) for k in kern_rows]
    for pat in args.expect_kernel or []:
        if not any(pat in n for n in present_names):
            missing_expected.append(pat)

    top_bottleneck = "BETWEEN_PASS_HOST_OR_IDLE" if non_gpu_share >= 20.0 else (
        kern_rows[0]["name"] if kern_rows else "GPU_EXECUTION"
    )
    amdahl_between_pass = round(((1.0 / max(1e-6, 1.0 - (non_gpu_share / 100.0))) - 1.0) * 100.0, 1) if non_gpu_share < 99.0 else 9900.0

    report = {
        "format": "critical-path-xray-v1",
        "trace": str(args.trace),
        "source_format": parsed["source_format"],
        "chip": args.chip or "custom",
        "peak_bw_gbs": peak_bw,
        "peak_tflops": peak_tf,
        "wall_ms": round(wall_ms, 3),
        "gpu_ms": round(gpu_ms, 3),
        "host_ms": round(host_ms, 3),
        "idle_ms": round(idle_ms, 3),
        "gpu_busy_pct": gpu_share,
        "host_share_pct": host_share,
        "idle_share_pct": idle_share,
        "between_pass_amdahl_gain_pct": amdahl_between_pass,
        "top_bottleneck": top_bottleneck,
        "expected_kernels_missing": missing_expected,
        "kernels": kern_rows,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(
        f"critical path ({parsed['source_format']}): wall={wall_ms:.2f} ms | "
        f"GPU={gpu_ms:.2f} ms ({gpu_share}%) | host={host_ms:.2f} ms ({host_share}%) | idle={idle_ms:.2f} ms ({idle_share}%)"
    )
    print(f"top bottleneck: {top_bottleneck} (between-pass elimination ceiling: +{amdahl_between_pass:.1f}%)")
    if kern_rows:
        print(f"{'KERNEL':<32} {'MS':>7} {'SHARE':>6} {'MAX+':>7} {'BW_GB/s':>8} {'REGIME'}")
        for k in kern_rows[:12]:
            print(
                f"{str(k['name'])[:32]:<32} {k['gpu_ms']:>7.3f} {k['wall_share_pct']:>5.1f}% "
                f"{k['amdahl_max_gain_pct']:>+6.1f}% {k['achieved_bw_gbs']:>8.1f} {k['regime']}"
            )
    if missing_expected:
        print(f"XRAY: EXPECTED_KERNEL_MISSING ({', '.join(missing_expected)} never executed in trace)")
        return 1
    print("XRAY: AUDITED")
    return 0


def cmd_roofline(args: argparse.Namespace) -> int:
    if args.time_ms <= 0 or args.bytes_gib < 0 or args.gflops < 0:
        raise Fail("--time-ms must be positive and bytes/flops nonnegative")
    peak_bw, peak_tf = resolve_peaks(args.chip, args.peak_bw_gbs, args.peak_tflops)
    if peak_bw <= 0 or peak_tf <= 0:
        raise Fail("pass --chip or both --peak-bw-gbs and --peak-tflops")
    info = classify_kernel(
        args.time_ms,
        args.bytes_gib * (1024**3),
        args.gflops * 1e9,
        args.launches,
        peak_bw,
        peak_tf,
    )
    ridge = round((peak_tf * 1e12) / (peak_bw * 1e9), 2)
    out = {"chip": args.chip or "custom", "ridge_flop_per_byte": ridge, **info}
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(
        f"roofline ({out['chip']}, ridge={ridge} FLOP/B): AI={info['arithmetic_intensity']} FLOP/B | "
        f"BW={info['achieved_bw_gbs']} GB/s ({info['bw_util_pct']}%) | "
        f"Compute={info['achieved_tflops']} TFLOP/s ({info['compute_util_pct']}%) | regime={info['regime']}"
    )
    return 0


def cmd_bound(args: argparse.Namespace) -> int:
    doc = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    wall_ms = float(doc.get("wall_ms") or 0.0)
    items = doc.get("candidates")
    if wall_ms <= 0 or not isinstance(items, list) or not items:
        raise Fail("candidates JSON requires positive 'wall_ms' and non-empty 'candidates' list")

    ranked = []
    group_max_saved: Dict[str, float] = {}
    for c in items:
        name = str(c["name"])
        target_ms = float(c["target_ms"])
        red_pct = float(c["expected_reduction_pct"])
        if target_ms < 0 or not (0.0 <= red_pct <= 100.0):
            raise Fail(f"candidate {name!r}: invalid target_ms or expected_reduction_pct")
        saved_ms = target_ms * (red_pct / 100.0)
        new_wall = max(1e-6, wall_ms - saved_ms)
        gain_pct = round(((wall_ms / new_wall) - 1.0) * 100.0, 2)
        enabler = bool(c.get("enabler", False))
        passes = gain_pct >= args.min_standalone_pct or enabler
        group = str(c.get("overlap_group") or name)
        if passes:
            group_max_saved[group] = max(group_max_saved.get(group, 0.0), saved_ms)
        ranked.append({
            "name": name,
            "overlap_group": group,
            "saved_ms": round(saved_ms, 3),
            "standalone_gain_pct": gain_pct,
            "enabler": enabler,
            "passes_screen": passes,
        })

    ranked.sort(key=lambda x: -float(x["standalone_gain_pct"]))
    total_non_overlap_saved = min(wall_ms * 0.95, sum(group_max_saved.values()))
    combined_gain_pct = round(((wall_ms / max(1e-6, wall_ms - total_non_overlap_saved)) - 1.0) * 100.0, 2)
    passing_count = sum(1 for r in ranked if r["passes_screen"])
    out = {
        "wall_ms": wall_ms,
        "min_standalone_pct": args.min_standalone_pct,
        "passing_candidates": passing_count,
        "combined_non_overlapping_gain_pct": combined_gain_pct,
        "ranked": ranked,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"Amdahl screen (wall={wall_ms:.2f} ms, floor={args.min_standalone_pct}%):")
    for r in ranked:
        tag = "KEEP" if r["passes_screen"] else "SKIP"
        print(f"  [{tag}] {r['name']:<28} saved={r['saved_ms']:>6.2f} ms  bound=+{r['standalone_gain_pct']:>5.1f}%  group={r['overlap_group']}")
    print(f"combined non-overlapping bound: +{combined_gain_pct:.1f}%")
    return 0 if passing_count > 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_aud = sub.add_parser("audit", help="parse a served trace CSV/JSON/log and compute critical path + rooflines")
    p_aud.add_argument("trace", help="path to nsys CSV, TensorFold JSON/JSONL, or FZ_ROUND_TIMES log")
    p_aud.add_argument("--chip", choices=tuple(CHIP_PROFILES), help="hardware roofline preset")
    p_aud.add_argument("--peak-bw-gbs", type=float, help="override memory bandwidth peak in GB/s")
    p_aud.add_argument("--peak-tflops", type=float, help="override FP16/BF16 compute peak in TFLOP/s")
    p_aud.add_argument("--expect-kernel", action="append", help="fail (exit 1) if this kernel substring never appears in trace")
    p_aud.add_argument("--json", dest="json_out", help="write JSON audit report")

    p_roof = sub.add_parser("roofline", help="classify one kernel shape against chip memory and compute rooflines")
    p_roof.add_argument("--bytes-gib", type=float, required=True)
    p_roof.add_argument("--gflops", type=float, required=True)
    p_roof.add_argument("--time-ms", type=float, required=True)
    p_roof.add_argument("--launches", type=int, default=1)
    p_roof.add_argument("--chip", choices=tuple(CHIP_PROFILES))
    p_roof.add_argument("--peak-bw-gbs", type=float)
    p_roof.add_argument("--peak-tflops", type=float)
    p_roof.add_argument("--json", dest="json_out")

    p_bnd = sub.add_parser("bound", help="screen and rank candidate optimizations by non-overlapping Amdahl bounds")
    p_bnd.add_argument("candidates", help="JSON with wall_ms and candidates list")
    p_bnd.add_argument("--min-standalone-pct", type=float, default=3.0)
    p_bnd.add_argument("--json", dest="json_out")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "audit":
            return cmd_audit(args)
        if args.cmd == "roofline":
            return cmd_roofline(args)
        if args.cmd == "bound":
            return cmd_bound(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"xray_trace.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
