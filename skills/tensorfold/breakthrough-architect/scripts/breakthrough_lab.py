#!/usr/bin/env python3
"""First-principles physics gap auditor, kill-probe gate and on-silicon autotuner.

Designed for engineering 3x-10x step-function breakthroughs inside TensorFold on
local silicon (Apple Silicon Metal and NVIDIA/AMD desktop GPUs) rather than
+2% incremental tweaks. Uses only the Python 3.9+ standard library.

Subcommands:
  physics-gap  Compute the four fundamental headroom ratios (bandwidth
               efficiency, one-stream share, DRAM traffic tax and dispatch tax)
               and rank which breakthrough class holds the largest multiplier.
  probe-card   Validate a structured assumption-inversion + 10-minute kill-probe
               card, and judge a measured probe result (GRADUATE vs KILL).
  autotune     Run grid, coordinate or random search over parameterized kernel
               or schedule configs directly on local silicon, rejecting any
               config with token_sha256 drift, register spills or throttling.
  stack-model  Combine orthogonal breakthrough multipliers across independent
               physics axes without double-counting overlapping gains.

Exit codes: 0 clean/graduate/winner, 1 rejected/killed/no valid config, 2 error.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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

ORTHOGONAL_AXES = (
    "bandwidth_and_layout",
    "dram_traffic_fusion",
    "zero_host_dispatch",
    "multi_token_lanes",
    "residency_and_prefix",
)

SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


class Fail(Exception):
    """Usage, missing file or schema error -> exit 2."""


def resolve_peaks(chip: Optional[str], peak_bw: Optional[float], peak_tf: Optional[float]) -> Tuple[float, float]:
    bw, tf = CHIP_PROFILES.get(chip or "", (0.0, 0.0))
    if peak_bw is not None:
        bw = peak_bw
    if peak_tf is not None:
        tf = peak_tf
    if bw <= 0:
        raise Fail("pass --chip or positive --peak-bw-gbs")
    return bw, tf


# ----------------------------------------------------------------- physics-gap

def cmd_physics_gap(args: argparse.Namespace) -> int:
    peak_bw, peak_tf = resolve_peaks(args.chip, args.peak_bw_gbs, args.peak_tflops)
    if args.active_weight_gib <= 0 or args.single_tps <= 0:
        raise Fail("--active-weight-gib and --single-tps must be positive")
    if args.kv_bytes_per_step_gib < 0:
        raise Fail("--kv-bytes-per-step-gib must be nonnegative")
    if not (0.0 < args.gpu_busy_pct <= 100.0):
        raise Fail("--gpu-busy-pct must be in (0, 100]")

    min_step_gb = (args.active_weight_gib + args.kv_bytes_per_step_gib) * (1024**3) / 1e9
    effective_bw_gbs = min_step_gb * args.single_tps
    bw_eff = effective_bw_gbs / peak_bw
    bw_headroom_mult = max(1.0, 0.85 / max(0.05, bw_eff))

    traffic_tax = 1.0
    if args.measured_bytes_gib is not None:
        if args.measured_bytes_gib <= 0:
            raise Fail("--measured-bytes-gib must be positive")
        measured_gb = args.measured_bytes_gib * (1024**3) / 1e9
        traffic_tax = max(1.0, measured_gb / max(1e-6, min_step_gb))

    lane_mult = 1.0
    one_stream_share_pct = None
    if args.multi_tps is not None:
        if args.multi_tps <= 0:
            raise Fail("--multi-tps must be positive")
        one_stream_share_pct = round(100.0 * args.single_tps / args.multi_tps, 1)
        lane_mult = max(1.0, args.multi_tps / args.single_tps)

    dispatch_mult = max(1.0, 0.97 / (args.gpu_busy_pct / 100.0))

    prefill_util_pct = None
    prefill_mult = 1.0
    if args.prefill_tps and args.active_params_b and peak_tf > 0:
        # Approximate 2 * active_params FLOPs per prefill token.
        achieved_tf = (2.0 * args.active_params_b * 1e9 * args.prefill_tps) / 1e12
        prefill_util_pct = round(100.0 * achieved_tf / peak_tf, 1)
        prefill_mult = max(1.0, 75.0 / max(5.0, prefill_util_pct))

    opportunities = [
        {
            "breakthrough_class": "MULTI_TOKEN_EXACT_LANES",
            "axis": "multi_token_lanes",
            "multiplier_ceiling": round(lane_mult, 2),
            "specialist_stack": "one-stream-lanes + drafter-forge + graph-and-dispatch-surgeon",
            "why": f"1-stream delivers {one_stream_share_pct}% of N-stream total ({args.single_tps} vs {args.multi_tps} tok/s)" if one_stream_share_pct is not None else "pass --multi-tps to measure exact single-stream lane headroom",
        },
        {
            "breakthrough_class": "DRAM_TRAFFIC_AND_LAYOUT_FUSION",
            "axis": "dram_traffic_fusion",
            "multiplier_ceiling": round(traffic_tax, 2),
            "specialist_stack": "kernel-contract + sass-and-air-diff",
            "why": f"measured step moves {traffic_tax:.2f}x theoretical minimum weights+KV bytes" if args.measured_bytes_gib else "pass --measured-bytes-gib from trace to measure activation/dequant traffic tax",
        },
        {
            "breakthrough_class": "KERNEL_BW_AND_AUTOTUNE",
            "axis": "bandwidth_and_layout",
            "multiplier_ceiling": round(bw_headroom_mult, 2),
            "specialist_stack": "breakthrough-architect autotune + kernel-contract + sass-and-air-diff",
            "why": f"effective decode BW is {effective_bw_gbs:.1f} GB/s ({bw_eff*100:.1f}% of {peak_bw:.0f} GB/s peak)",
        },
        {
            "breakthrough_class": "ZERO_HOST_DISPATCH",
            "axis": "zero_host_dispatch",
            "multiplier_ceiling": round(dispatch_mult, 2),
            "specialist_stack": "graph-and-dispatch-surgeon",
            "why": f"GPU busy is {args.gpu_busy_pct:.1f}% (target >= 97% via indirect graphs and device-side acceptance)",
        },
    ]
    if prefill_util_pct is not None:
        opportunities.append({
            "breakthrough_class": "PREFILL_COMPUTE_SATURATION",
            "axis": "bandwidth_and_layout",
            "multiplier_ceiling": round(prefill_mult, 2),
            "specialist_stack": "prefill-and-kv + kernel-contract",
            "why": f"cold prefill utilizes {prefill_util_pct:.1f}% of {peak_tf:.0f} TFLOP/s peak",
        })

    opportunities.sort(key=lambda x: -float(x["multiplier_ceiling"]))
    # Estimate conservative realistic combined multiplier (square-root damping on ceilings to stay grounded).
    top_stack = round(
        min(lane_mult, 1.0 + (lane_mult - 1.0) * 0.55)
        * min(traffic_tax, 1.0 + (traffic_tax - 1.0) * 0.65)
        * min(dispatch_mult, 1.0 + (dispatch_mult - 1.0) * 0.85)
        * min(bw_headroom_mult, 1.0 + (bw_headroom_mult - 1.0) * 0.50),
        2,
    )

    out = {
        "format": "breakthrough-physics-gap-v1",
        "chip": args.chip or "custom",
        "peak_bw_gbs": peak_bw,
        "peak_tflops": peak_tf,
        "effective_bw_gbs": round(effective_bw_gbs, 1),
        "bw_efficiency_pct": round(bw_eff * 100.0, 1),
        "one_stream_share_pct": one_stream_share_pct,
        "dram_traffic_tax": round(traffic_tax, 2),
        "dispatch_headroom_mult": round(dispatch_mult, 2),
        "conservative_combined_multiplier": top_stack,
        "ranked_breakthroughs": opportunities,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(
        f"physics gap ({out['chip']}, peak {peak_bw:.0f} GB/s): "
        f"eff_bw={out['effective_bw_gbs']} GB/s ({out['bw_efficiency_pct']}%) | "
        f"1-stream share={one_stream_share_pct}% | traffic_tax={traffic_tax:.2f}x | gpu_busy={args.gpu_busy_pct:.1f}%"
    )
    print(f"conservative stacked multiplier: {top_stack:.2f}x ({args.single_tps:.1f} -> {args.single_tps * top_stack:.1f} tok/s)")
    for idx, op in enumerate(opportunities, 1):
        print(f"  {idx}. {op['breakthrough_class']:<32} ceiling={op['multiplier_ceiling']:>5.2f}x | {op['why']}")
        print(f"     stack: {op['specialist_stack']}")
    return 0


# ------------------------------------------------------------------ probe-card

def cmd_probe_card(args: argparse.Namespace) -> int:
    p = Path(args.card)
    if not p.is_file():
        raise Fail(f"probe card not found: {p}")
    card = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(card, dict):
        raise Fail("probe card must be a JSON object")

    issues: List[str] = []
    for field in ("name", "assumption_inverted", "orthogonal_axis", "kill_probe_cmd"):
        if not str(card.get(field) or "").strip():
            issues.append(f"missing required field '{field}'")

    axis = str(card.get("orthogonal_axis") or "")
    if axis and axis not in ORTHOGONAL_AXES:
        issues.append(f"orthogonal_axis must be one of {ORTHOGONAL_AXES}, got {axis!r}")

    target_mult = float(card.get("target_multiplier") or 0.0)
    if not math.isfinite(target_mult) or target_mult < args.min_multiplier:
        issues.append(f"target_multiplier {target_mult} is below breakthrough bar {args.min_multiplier}x (use tensorfold-speed-hunt directly for small tweaks)")

    max_mins = float(card.get("max_probe_minutes") or 0.0)
    if not math.isfinite(max_mins) or max_mins <= 0 or max_mins > 60:
        issues.append(f"max_probe_minutes must be in (0, 60], got {max_mins} (a kill-probe must stay cheap)")

    pass_th = card.get("pass_threshold")
    kill_th = card.get("kill_threshold")
    if pass_th is None or kill_th is None:
        issues.append("both pass_threshold and kill_threshold numeric bounds are required")
    else:
        pass_f, kill_f = float(pass_th), float(kill_th)
        higher_better = bool(card.get("higher_is_better", True))
        if higher_better and pass_f <= kill_f:
            issues.append("with higher_is_better=true, pass_threshold must exceed kill_threshold")
        if not higher_better and pass_f >= kill_f:
            issues.append("with higher_is_better=false, pass_threshold must be below kill_threshold")

    if issues:
        for iss in issues:
            print(f"  [ISSUE] {iss}")
        print("PROBE CARD: REJECTED")
        return 1

    if args.measured is None:
        print(
            f"PROBE CARD: VALID ({card['name']} | axis={axis} | target={target_mult}x | "
            f"budget<={max_mins:g}m | pass={pass_th}, kill={kill_th})"
        )
        return 0

    measured = float(args.measured)
    higher_better = bool(card.get("higher_is_better", True))
    passed = (measured >= float(pass_th)) if higher_better else (measured <= float(pass_th))
    killed = (measured <= float(kill_th)) if higher_better else (measured >= float(kill_th))
    if passed:
        verdict, code = "PROBE VERDICT: GRADUATE_TO_SPEED_HUNT", 0
    elif killed:
        verdict, code = "PROBE VERDICT: KILL_IN_10_MIN", 1
    else:
        verdict, code = "PROBE VERDICT: AMBIGUOUS_REFINE_PROBE", 1

    print(f"probe '{card['name']}': measured={measured:g} (pass={pass_th}, kill={kill_th})")
    print(verdict)
    return code


# -------------------------------------------------------------------- autotune

def _eval_candidate(
    cmd_template: Sequence[str],
    params: Dict[str, object],
    timeout_s: float,
    ref_sha: Optional[str],
    forbid_spills: bool,
) -> Dict[str, object]:
    params_str = json.dumps(params, sort_keys=True)
    cmd = [tok.replace("{params_json}", params_str) for tok in cmd_template]
    env = os.environ.copy()
    env["TF_TUNE_PARAMS_JSON"] = params_str
    for k, v in params.items():
        env[f"TF_TUNE_{re.sub(r'[^A-Za-z0-9]+', '_', k).upper()}"] = str(v)

    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"params": params, "valid": False, "reason": f"timeout after {timeout_s}s", "tps": 0.0}
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        return {"params": params, "valid": False, "reason": f"eval exit {proc.returncode}: {tail[-1] if tail else ''}", "tps": 0.0}

    # Parse last JSON object printed on stdout.
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    doc = None
    for ln in reversed(lines):
        if ln.startswith("{"):
            try:
                doc = json.loads(ln)
                break
            except json.JSONDecodeError:
                continue
    if not isinstance(doc, dict):
        return {"params": params, "valid": False, "reason": "eval command did not emit JSON result on stdout", "tps": 0.0}

    tps = float(doc.get("tps") or 0.0)
    if not math.isfinite(tps) or tps <= 0:
        return {"params": params, "valid": False, "reason": f"invalid tps {tps}", "tps": 0.0}
    if doc.get("throttled"):
        return {"params": params, "valid": False, "reason": "dropped throttled measurement", "tps": tps}

    spills = doc.get("spills")
    if spills is None and not forbid_spills:
        spills = 0
    if type(spills) is not int or spills < 0:
        return {"params": params, "valid": False, "reason": "missing or invalid spill count", "tps": tps}
    if forbid_spills and spills > 0:
        return {"params": params, "valid": False, "reason": f"disqualified: {spills} register spill(s)", "tps": tps, "spills": spills}

    sha = doc.get("token_sha256")
    if ref_sha:
        if not isinstance(sha, str) or SHA256_RE.fullmatch(sha) is None:
            return {"params": params, "valid": False, "reason": "missing 64-hex token_sha256", "tps": tps}
        if sha.lower() != ref_sha.lower():
            return {"params": params, "valid": False, "reason": "token_sha256 mismatch vs reference", "tps": tps}

    return {"params": params, "valid": True, "reason": "ok", "tps": round(tps, 3), "spills": spills, "token_sha256": sha}


def cmd_autotune(args: argparse.Namespace) -> int:
    sp_path = Path(args.space)
    if not sp_path.is_file():
        raise Fail(f"search space JSON not found: {sp_path}")
    space_doc = json.loads(sp_path.read_text(encoding="utf-8"))
    if not isinstance(space_doc, dict) or not isinstance(space_doc.get("params"), dict) or not space_doc["params"]:
        raise Fail("space JSON must contain a non-empty 'params' mapping of name -> choices list")
    params_spec: Dict[str, List[object]] = {}
    total_combos = 1
    for k, vals in space_doc["params"].items():
        if not isinstance(vals, list) or not vals:
            raise Fail(f"param {k!r} must map to a non-empty list of choices")
        params_spec[k] = vals
        total_combos *= len(vals)

    keys = list(params_spec)
    cmd_template = space_doc.get("eval_cmd")
    if args.eval_cmd:
        cmd_template = json.loads(args.eval_cmd)
    if not args.dry_run and (not isinstance(cmd_template, list) or not cmd_template):
        raise Fail("provide 'eval_cmd' list in space JSON or pass --eval-cmd JSON list")

    ref_sha = args.reference_sha256 or space_doc.get("reference_sha256")
    if ref_sha and SHA256_RE.fullmatch(str(ref_sha)) is None:
        raise Fail("--reference-sha256 must be a full 64-hex SHA-256")

    if args.dry_run:
        print(f"AUTOTUNE DRY RUN: {len(keys)} params, {total_combos} total combinations, strategy={args.strategy}, budget={args.budget}")
        for k in keys:
            print(f"  {k}: {params_spec[k]}")
        return 0

    rng = random.Random(args.seed)
    evaluated: List[Dict[str, object]] = []
    seen = set()

    def run_one(cfg: Dict[str, object]) -> Dict[str, object]:
        sig = json.dumps(cfg, sort_keys=True)
        seen.add(sig)
        res = _eval_candidate(cmd_template, cfg, args.timeout, ref_sha, args.forbid_spills)
        evaluated.append(res)
        return res

    if args.strategy == "grid":
        all_cfgs = [dict(zip(keys, vals)) for vals in itertools.product(*(params_spec[k] for k in keys))]
        for cfg in all_cfgs[: args.budget]:
            run_one(cfg)
    elif args.strategy == "random":
        attempts = 0
        while len(evaluated) < min(args.budget, total_combos) and attempts < args.budget * 10:
            attempts += 1
            cfg = {k: rng.choice(params_spec[k]) for k in keys}
            if json.dumps(cfg, sort_keys=True) in seen:
                continue
            run_one(cfg)
    else:
        # Coordinate ascent from default/first point: sweeps each parameter one axis at a time.
        best_cfg = dict(space_doc.get("default") or {k: params_spec[k][0] for k in keys})
        best_res = run_one(best_cfg)
        best_tps = float(best_res["tps"]) if best_res["valid"] else -1.0
        for _round in range(args.coord_rounds):
            improved = False
            for k in keys:
                for val in params_spec[k]:
                    if len(evaluated) >= args.budget:
                        break
                    cand = dict(best_cfg)
                    cand[k] = val
                    if json.dumps(cand, sort_keys=True) in seen:
                        continue
                    res = run_one(cand)
                    if res["valid"] and float(res["tps"]) > best_tps:
                        best_tps = float(res["tps"])
                        best_cfg = cand
                        improved = True
                if len(evaluated) >= args.budget:
                    break
            if not improved or len(evaluated) >= args.budget:
                break

    valid = sorted((r for r in evaluated if r["valid"]), key=lambda x: -float(x["tps"]))
    winner = valid[0] if valid else None
    out = {
        "format": "breakthrough-autotune-v1",
        "strategy": args.strategy,
        "evaluated": len(evaluated),
        "valid_count": len(valid),
        "disqualified_count": len(evaluated) - len(valid),
        "winner": winner,
        "top_5": valid[:5],
        "all_runs": evaluated,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"autotune ({args.strategy}): evaluated {len(evaluated)} configs | valid={len(valid)} disqualified={len(evaluated) - len(valid)}")
    if winner:
        print(f"AUTOTUNE: WINNER ({winner['tps']} tok/s, params={json.dumps(winner['params'], sort_keys=True)})")
        return 0
    print("AUTOTUNE: NO_VALID_CONFIG (every tested config failed exactness, spills or runtime)")
    return 1


# ----------------------------------------------------------------- stack-model

def cmd_stack_model(args: argparse.Namespace) -> int:
    doc = json.loads(Path(args.roadmap).read_text(encoding="utf-8"))
    base_tps = float(doc.get("baseline_tps") or 0.0)
    items = doc.get("items")
    if base_tps <= 0 or not isinstance(items, list) or not items:
        raise Fail("roadmap JSON requires positive 'baseline_tps' and non-empty 'items' list")

    by_axis: Dict[str, List[Dict[str, object]]] = {}
    for item in items:
        axis = str(item.get("axis") or "")
        if axis not in ORTHOGONAL_AXES:
            raise Fail(f"invalid axis {axis!r}; choose from {ORTHOGONAL_AXES}")
        mult = float(item.get("multiplier") or 0.0)
        if not math.isfinite(mult) or mult < 1.0:
            raise Fail(f"item {item.get('name')!r}: multiplier must be >= 1.0")
        by_axis.setdefault(axis, []).append(item)

    axis_summary = {}
    total_mult = 1.0
    for axis in ORTHOGONAL_AXES:
        group = by_axis.get(axis) or []
        if not group:
            continue
        # Count only the strongest item per axis to prevent double-counting overlapping gains.
        best = max(group, key=lambda x: float(x["multiplier"]))
        m = float(best["multiplier"])
        total_mult *= m
        axis_summary[axis] = {"selected": best["name"], "multiplier": round(m, 3), "superseded_count": len(group) - 1}

    projected_tps = round(base_tps * total_mult, 2)
    out = {
        "baseline_tps": base_tps,
        "combined_multiplier": round(total_mult, 3),
        "projected_tps": projected_tps,
        "axes": axis_summary,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"orthogonal 10x stack: {base_tps:.1f} -> {projected_tps:.1f} tok/s ({total_mult:.2f}x total)")
    for axis, info in axis_summary.items():
        extra = f" ({info['superseded_count']} overlapping skipped)" if info["superseded_count"] else ""
        print(f"  {axis:<24} {info['multiplier']:.2f}x  {info['selected']}{extra}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gap = sub.add_parser("physics-gap", help="compute the 4 silicon headroom ratios and rank 2x-10x breakthrough classes")
    p_gap.add_argument("--chip", choices=tuple(CHIP_PROFILES))
    p_gap.add_argument("--peak-bw-gbs", type=float)
    p_gap.add_argument("--peak-tflops", type=float)
    p_gap.add_argument("--active-weight-gib", type=float, required=True, help="active weights read per decode step in GiB")
    p_gap.add_argument("--kv-bytes-per-step-gib", type=float, default=0.0)
    p_gap.add_argument("--measured-bytes-gib", type=float, help="actual DRAM bytes moved per step from trace")
    p_gap.add_argument("--single-tps", type=float, required=True, help="current 1-stream served tok/s")
    p_gap.add_argument("--multi-tps", type=float, help="current N-stream total served tok/s on same chip")
    p_gap.add_argument("--gpu-busy-pct", type=float, default=85.0)
    p_gap.add_argument("--prefill-tps", type=float)
    p_gap.add_argument("--active-params-b", type=float)
    p_gap.add_argument("--json", dest="json_out")

    p_card = sub.add_parser("probe-card", help="validate a 10-minute kill-probe card and optionally judge its measured value")
    p_card.add_argument("card", help="path to probe card JSON")
    p_card.add_argument("--min-multiplier", type=float, default=1.25, help="minimum target multiplier for breakthrough mode (default: 1.25)")
    p_card.add_argument("--measured", type=float, help="measured value from running the 10-minute kill-probe")

    p_auto = sub.add_parser("autotune", help="run exactness- and spill-gated parameterized search directly on local silicon")
    p_auto.add_argument("--space", required=True, help="path to autotune search-space JSON")
    p_auto.add_argument("--eval-cmd", help="JSON array command overriding space.eval_cmd")
    p_auto.add_argument("--strategy", choices=("coord", "grid", "random"), default="coord")
    p_auto.add_argument("--budget", type=int, default=40, help="max candidate evaluations (default: 40)")
    p_auto.add_argument("--coord-rounds", type=int, default=3)
    p_auto.add_argument("--seed", type=int, default=0)
    p_auto.add_argument("--timeout", type=float, default=120.0)
    p_auto.add_argument("--reference-sha256", help="required 64-hex token_sha256 every valid config must match")
    p_auto.add_argument("--forbid-spills", action="store_true", help="require a measured integer spill count of zero")
    p_auto.add_argument("--dry-run", action="store_true")
    p_auto.add_argument("--json", dest="json_out")

    p_stk = sub.add_parser("stack-model", help="multiply orthogonal breakthrough gains across independent physics axes")
    p_stk.add_argument("roadmap", help="path to roadmap JSON")
    p_stk.add_argument("--json", dest="json_out")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "physics-gap":
            return cmd_physics_gap(args)
        if args.cmd == "probe-card":
            return cmd_probe_card(args)
        if args.cmd == "autotune":
            return cmd_autotune(args)
        if args.cmd == "stack-model":
            return cmd_stack_model(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"breakthrough_lab.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
