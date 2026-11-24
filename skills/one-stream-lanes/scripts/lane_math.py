#!/usr/bin/env python3
"""Analyze single-stream vs N-stream inference lanes, share and bottlenecks.

Measures a single stream in three numbers:
  rows a pass, landed a pass, passes a second (tok/s = landed * passes/s)
and the scoreboard ratio:
  share = single_stream_tps / multi_stream_total_tps

Subcommands:
  share       Compute single-stream share and check landed * passes/s consistency.
  diagnose    Analyze a lane-sweep JSON/CSV (rows, landed, single_pps, multi_pps)
              and classify the bottleneck: PROPOSALS_FLAT, ROW_COST_HEAVY, or
              BETWEEN_PASS_OVERHEAD.
  plan-width  Price marginal row cost against expected landed tokens across
              widths (dense or MoE) to find the optimal draft window width.
  compare     Compare base vs candidate lane receipts cell by cell, flagging
              token hash mismatches or N-stream throughput regressions.

Exit codes: 0 success, 1 gate/comparison failure, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from typing import Dict, List, Optional, Sequence


def finite_measurement(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def load_sweep(path: str) -> List[Dict[str, float]]:
    if not os.path.isfile(path):
        raise RuntimeError(f"file not found: {path}")
    if path.endswith(".csv"):
        rows: List[Dict[str, float]] = []
        with open(path, encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                item: Dict[str, float] = {}
                for k, v in raw.items():
                    if v is not None and v.strip() != "":
                        item[k.strip()] = finite_measurement(v, k.strip())
                if item:
                    rows.append(item)
        return rows
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict) and "points" in data:
        data = data["points"]
    if not isinstance(data, list) or not data:
        raise RuntimeError("expected a non-empty JSON list (or object with 'points')")
    return [{str(k): finite_measurement(v, k) for k, v in entry.items() if isinstance(v, (int, float))} for entry in data]


def diagnose_sweep(points: List[Dict[str, float]]) -> Dict[str, object]:
    for idx, pt in enumerate(points, 1):
        for key in ("rows", "landed", "single_pps"):
            if key not in pt or pt[key] <= 0:
                raise RuntimeError(f"point {idx} missing positive '{key}'")
    pts = sorted(points, key=lambda p: p["rows"])
    enriched: List[Dict[str, float]] = []
    for pt in pts:
        single_tps = pt["landed"] * pt["single_pps"]
        rec = {
            "rows": pt["rows"],
            "landed": pt["landed"],
            "accept_rate": round(pt["landed"] / pt["rows"], 4),
            "single_pps": pt["single_pps"],
            "single_tps": round(single_tps, 2),
        }
        if "multi_pps" in pt and pt["multi_pps"] > 0:
            rec["multi_pps"] = pt["multi_pps"]
            multi_tps = pt.get("multi_tps", pt["rows"] * pt["multi_pps"])
            rec["multi_tps"] = round(multi_tps, 2)
            rec["share"] = round(single_tps / multi_tps, 4) if multi_tps > 0 else 0.0
        elif "multi_tps" in pt and pt["multi_tps"] > 0:
            rec["multi_tps"] = round(pt["multi_tps"], 2)
            rec["share"] = round(single_tps / pt["multi_tps"], 4)
        enriched.append(rec)

    if len(enriched) < 2:
        return {
            "bottleneck": "INSUFFICIENT_POINTS",
            "reason": "need at least 2 row widths to draw landing and width curves",
            "points": enriched,
        }

    first, last = enriched[0], enriched[-1]
    row_gain = last["rows"] - first["rows"]
    landed_gain = last["landed"] - first["landed"]
    marginal_land = (landed_gain / row_gain) if row_gain > 0 else 0.0
    single_pps_ratio = last["single_pps"] / first["single_pps"]

    width_drop_worse_than_multi = False
    if "multi_pps" in first and "multi_pps" in last and first["multi_pps"] > 0:
        multi_pps_ratio = last["multi_pps"] / first["multi_pps"]
        if single_pps_ratio < multi_pps_ratio * 0.85:
            width_drop_worse_than_multi = True

    if width_drop_worse_than_multi or single_pps_ratio < 0.65:
        bottleneck = "ROW_COST_HEAVY"
        reason = (
            f"passes/s fell from {first['single_pps']:.2f} to {last['single_pps']:.2f} "
            f"(ratio {single_pps_ratio:.2f}) as width grew {first['rows']:.0f} -> {last['rows']:.0f}; "
            "fix wide-window kernel/KV/expert row cost first"
        )
    elif marginal_land < 0.15:
        bottleneck = "PROPOSALS_FLAT"
        reason = (
            f"landing curve flattened ({first['landed']:.2f} -> {last['landed']:.2f} landed across "
            f"{first['rows']:.0f} -> {last['rows']:.0f} rows, marginal {marginal_land:.2f}/row); "
            "add higher-odds token sources, context/recall copies, grammar forcing, or hedged trees"
        )
    else:
        bottleneck = "BETWEEN_PASS_OVERHEAD"
        reason = (
            f"landing still rises ({marginal_land:.2f}/row) and passes/s holds ({single_pps_ratio:.2f}x); "
            "audit host round trips, proposer serialization, acceptance sync, and GPU idle gaps between passes"
        )

    best = max(enriched, key=lambda p: p["single_tps"])
    return {
        "bottleneck": bottleneck,
        "reason": reason,
        "marginal_land_per_row": round(marginal_land, 4),
        "single_pps_ratio": round(single_pps_ratio, 4),
        "best_point": best,
        "points": enriched,
    }


def token_digest(cell: object, label: str) -> str:
    digest = cell.get("token_sha256") if isinstance(cell, dict) else None
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
        raise ValueError(f"{label}: token_sha256 must be a full 64-hex SHA-256 digest")
    return digest.lower()


def compare_receipts(base_path: str, cand_path: str, min_gain_pct: float) -> Dict[str, object]:
    if not math.isfinite(min_gain_pct) or min_gain_pct < 0:
        raise ValueError("--min-gain-pct must be finite and nonnegative")
    with open(base_path, encoding="utf-8") as handle:
        base = json.load(handle)
    with open(cand_path, encoding="utf-8") as handle:
        cand = json.load(handle)
    b_cells = base.get("cells") if isinstance(base, dict) else None
    c_cells = cand.get("cells") if isinstance(cand, dict) else None
    if not isinstance(b_cells, dict) or not isinstance(c_cells, dict) or not b_cells:
        raise RuntimeError("expected JSON objects with a non-empty 'cells' mapping")

    missing = sorted(set(b_cells) ^ set(c_cells))
    if missing:
        raise RuntimeError(f"cell mismatch between base and candidate: {missing}")

    rows: List[Dict[str, object]] = []
    exact_fail = False
    multi_regressed = False
    single_regressed = False

    for name in sorted(b_cells):
        b, c = b_cells[name], c_cells[name]
        exact = token_digest(b, f"base cell {name!r}") == token_digest(c, f"candidate cell {name!r}")
        if not exact:
            exact_fail = True

        b_s, b_m = measured_rates(b, f"base cell {name!r}")
        c_s, c_m = measured_rates(c, f"candidate cell {name!r}")
        b_share, c_share = b_s / b_m, c_s / c_m
        s_delta_pct = (c_s - b_s) / b_s * 100.0
        m_delta_pct = (c_m - b_m) / b_m * 100.0
        if not all(math.isfinite(value) for value in (b_share, c_share, s_delta_pct, m_delta_pct)):
            raise ValueError(f"cell {name!r}: throughput ratios exceed the numeric range")

        if m_delta_pct < -1.0:
            multi_regressed = True
        if s_delta_pct < -min_gain_pct and s_delta_pct < 0:
            single_regressed = True

        rows.append({
            "cell": name,
            "single_tps_before": b_s,
            "single_tps_after": c_s,
            "single_delta_pct": round(s_delta_pct, 2),
            "multi_tps_before": b_m,
            "multi_tps_after": c_m,
            "multi_delta_pct": round(m_delta_pct, 2),
            "share_before": round(b_share, 4),
            "share_after": round(c_share, 4),
            "exact": exact,
        })

    if exact_fail:
        verdict = "REJECTED_EXACTNESS"
    elif multi_regressed:
        verdict = "REJECTED_MULTI_REGRESSION"
    elif single_regressed:
        verdict = "REGRESSED"
    else:
        verdict = "VALID"

    return {"verdict": verdict, "cells": rows}


def measured_rates(cell: dict, label: str) -> tuple[float, float]:
    single = finite_measurement(cell.get("single_tps"), f"{label} single_tps")
    multi = finite_measurement(cell.get("multi_tps"), f"{label} multi_tps")
    if single <= 0 or multi <= 0:
        field = "single_tps" if single <= 0 else "multi_tps"
        raise ValueError(f"{label} {field} must be positive")
    return single, multi


def plan_width(points: List[Dict[str, float]], accept_probs: Optional[List[float]]) -> Dict[str, object]:
    if not points:
        raise RuntimeError("empty width table")
    pts = sorted(points, key=lambda p: p["rows"])
    out: List[Dict[str, object]] = []
    prev_tps: Optional[float] = None
    prev_pps: Optional[float] = None
    prev_landed: Optional[float] = None

    for pt in pts:
        rows = pt["rows"]
        if rows < 1:
            raise RuntimeError(f"invalid rows value {rows}")
        if "single_pps" in pt and pt["single_pps"] > 0:
            pps = pt["single_pps"]
            pass_ms = 1000.0 / pps
        elif "pass_ms" in pt and pt["pass_ms"] > 0:
            pass_ms = pt["pass_ms"]
            pps = 1000.0 / pass_ms
        else:
            raise RuntimeError(f"width {rows} needs positive 'single_pps' or 'pass_ms'")

        if "landed" in pt and pt["landed"] > 0:
            landed = pt["landed"]
        elif accept_probs is not None:
            # Expected kept tokens for a linear draft chain of length (rows - 1) plus 1 guaranteed root token.
            depth = max(0, int(round(rows)) - 1)
            landed = 1.0
            surv = 1.0
            for idx in range(depth):
                p_i = accept_probs[idx] if idx < len(accept_probs) else accept_probs[-1]
                surv *= p_i
                landed += surv
        else:
            raise RuntimeError(f"width {rows} needs positive 'landed' or --accept-probs")

        tps = landed * pps
        breakeven_landed = round(prev_tps / pps, 3) if prev_tps is not None else round(landed, 3)
        delta_tps = round(tps - prev_tps, 2) if prev_tps is not None else 0.0
        cost_ratio = round(prev_pps / pps, 3) if prev_pps is not None else 1.0
        rec: Dict[str, object] = {
            "rows": rows,
            "pass_ms": round(pass_ms, 3),
            "single_pps": round(pps, 2),
            "landed": round(landed, 3),
            "single_tps": round(tps, 2),
            "breakeven_landed": breakeven_landed,
            "delta_tps": delta_tps,
            "step_cost_ratio": cost_ratio,
        }
        if "active_experts" in pt:
            rec["active_experts"] = pt["active_experts"]
        out.append(rec)
        prev_tps = tps
        prev_pps = pps
        prev_landed = landed

    best = max(out, key=lambda r: float(r["single_tps"]))
    widest = out[-1]
    moe_cliff = float(widest["single_tps"]) < float(best["single_tps"]) * 0.95
    return {
        "optimal_rows": best["rows"],
        "optimal_tps": best["single_tps"],
        "widest_regresses": moe_cliff,
        "widths": out,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_share = sub.add_parser("share", help="compute single-stream share of N-stream throughput")
    p_share.add_argument("--single-tps", type=float, required=True, help="single-stream tokens/sec")
    p_share.add_argument("--multi-tps", type=float, required=True, help="N-stream total tokens/sec")
    p_share.add_argument("--streams", type=int, required=True, help="N (concurrent streams in multi-tps)")
    p_share.add_argument("--rows", type=float, help="rows dispatched per pass")
    p_share.add_argument("--landed", type=float, help="tokens landed per pass")
    p_share.add_argument("--passes-per-sec", type=float, help="passes per second")
    p_share.add_argument("--json", dest="json_out")

    p_diag = sub.add_parser("diagnose", help="diagnose landing curve and width curve from a sweep JSON/CSV")
    p_diag.add_argument("sweep", help="JSON or CSV file with rows, landed, single_pps (optional multi_pps/multi_tps)")
    p_diag.add_argument("--json", dest="json_out")

    p_plan = sub.add_parser("plan-width", help="compute optimal window width by pricing row cost against landed tokens")
    p_plan.add_argument("table", help="JSON or CSV with rows, single_pps (or pass_ms), and landed (or pass --accept-probs)")
    p_plan.add_argument("--accept-probs", help="comma-separated per-step draft probabilities when table omits landed")
    p_plan.add_argument("--json", dest="json_out")

    p_cmp = sub.add_parser("compare", help="compare base vs candidate lane receipts cell by cell")
    p_cmp.add_argument("base", help="base receipt JSON")
    p_cmp.add_argument("candidate", help="candidate receipt JSON")
    p_cmp.add_argument("--min-gain-pct", type=float, default=1.0, help="regression guard tolerance in percent (default: 1.0)")
    p_cmp.add_argument("--json", dest="json_out")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "share":
        if not all(math.isfinite(v) and v > 0 for v in (args.single_tps, args.multi_tps)) or args.streams < 2:
            print("lane_math.py: error: single-tps and multi-tps must be finite and positive and streams >= 2", file=sys.stderr)
            return 2
        share = args.single_tps / args.multi_tps
        per_stream_multi = args.multi_tps / args.streams
        out: Dict[str, object] = {
            "single_tps": args.single_tps,
            "multi_tps": args.multi_tps,
            "streams": args.streams,
            "share": round(share, 4),
            "share_pct": round(share * 100.0, 2),
            "headroom_x": round(args.multi_tps / args.single_tps, 2),
            "per_stream_at_n": round(per_stream_multi, 2),
        }
        if args.landed and args.passes_per_sec:
            implied = args.landed * args.passes_per_sec
            out["implied_tps"] = round(implied, 2)
            out["consistency_diff_pct"] = round(abs(implied - args.single_tps) / args.single_tps * 100.0, 2)
        if args.rows and args.landed:
            out["accept_rate"] = round(args.landed / args.rows, 4)
        print(
            f"share: {out['share_pct']}% ({args.single_tps:.2f} / {args.multi_tps:.2f} tok/s at N={args.streams}, "
            f"headroom {out['headroom_x']}x)"
        )
        if "implied_tps" in out:
            print(f"lanes: {args.landed:.2f} landed/pass * {args.passes_per_sec:.2f} passes/s = {out['implied_tps']} tok/s (diff {out['consistency_diff_pct']}%)")
        if args.json_out:
            json.dump(out, open(args.json_out, "w", encoding="utf-8"), indent=2)
        return 0

    if args.command == "diagnose":
        try:
            pts = load_sweep(args.sweep)
            res = diagnose_sweep(pts)
        except (RuntimeError, OSError, ValueError) as exc:
            print(f"lane_math.py: error: {exc}", file=sys.stderr)
            return 2
        for p in res["points"]:
            share_s = f"  share={p['share']*100:.1f}%" if "share" in p else ""
            print(f"  rows={p['rows']:>4.0f}  landed={p['landed']:>5.2f} ({p['accept_rate']*100:>5.1f}%)  pps={p['single_pps']:>6.2f}  tps={p['single_tps']:>7.2f}{share_s}")
        print(f"BOTTLENECK: {res['bottleneck']} ({res['reason']})")
        if args.json_out:
            json.dump(res, open(args.json_out, "w", encoding="utf-8"), indent=2)
        return 0

    if args.command == "plan-width":
        try:
            pts = load_sweep(args.table)
            probs = None
            if args.accept_probs:
                probs = [float(x.strip()) for x in args.accept_probs.split(",") if x.strip()]
                if not probs or not all(0.0 <= p <= 1.0 for p in probs):
                    raise ValueError("acceptance probabilities must lie in [0, 1]")
            res = plan_width(pts, probs)
        except (RuntimeError, OSError, ValueError) as exc:
            print(f"lane_math.py: error: {exc}", file=sys.stderr)
            return 2
        for w in res["widths"]:
            exp_s = f"  experts={w['active_experts']:.0f}" if "active_experts" in w else ""
            print(
                f"  rows={w['rows']:>4.0f}  pps={w['single_pps']:>6.2f}  landed={w['landed']:>5.2f} "
                f"(need {w['breakeven_landed']:>5.2f})  tps={w['single_tps']:>7.2f} ({w['delta_tps']:+.2f}){exp_s}"
            )
        cliff = " (widest window regresses due to row cost)" if res["widest_regresses"] else ""
        print(f"PLAN: optimal rows={res['optimal_rows']:.0f} ({res['optimal_tps']} tok/s){cliff}")
        if args.json_out:
            json.dump(res, open(args.json_out, "w", encoding="utf-8"), indent=2)
        return 0

    if args.command == "compare":
        try:
            res = compare_receipts(args.base, args.candidate, args.min_gain_pct)
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            print(f"lane_math.py: error: {exc}", file=sys.stderr)
            return 2
        for row in res["cells"]:
            ex = "exact=ok" if row["exact"] else "exact=MISMATCH"
            print(
                f"  {row['cell']:<18} single {row['single_tps_before']:.1f}->{row['single_tps_after']:.1f} ({row['single_delta_pct']:+.1f}%)  "
                f"multi {row['multi_tps_before']:.1f}->{row['multi_tps_after']:.1f} ({row['multi_delta_pct']:+.1f}%)  "
                f"share {row['share_before']*100:.1f}%->{row['share_after']*100:.1f}%  {ex}"
            )
        print(f"LANES: {res['verdict']}")
        if args.json_out:
            json.dump(res, open(args.json_out, "w", encoding="utf-8"), indent=2)
        return 0 if res["verdict"] == "VALID" else 1

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
