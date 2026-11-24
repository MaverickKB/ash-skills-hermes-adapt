#!/usr/bin/env python3
"""Profile MoE router traces, plan hot-expert residency and detect router drift.

Reads TensorFold `--fsr-route-trace` or generic JSON/JSONL per-layer expert
routing logs to quantify activation skew, normalized Shannon entropy, dead/cold
experts, co-activation pairs, GPU-vs-SSD expert residency budgets (`--ssd-experts`),
safe domain-pruning manifests and cross-quant router drift (Jensen-Shannon
divergence). Uses only the Python 3.9+ standard library.

Subcommands:
  profile     Compute per-layer activation histograms, entropy, K90 hot sets,
              dead experts and top co-activated pairs from a route trace.
  residency   Rank (layer, expert) slots by route hit rate against a GPU budget
              and emit a GPU-resident vs `--ssd-experts` residency manifest.
  prune-plan  Select coldest experts per layer inside a max route-loss or drop
              budget while guarding high-entropy layers and top-k floors.
  diff        Compare two routing profiles (e.g. bf16 vs quantized, or code vs
              prose) via per-layer Jensen-Shannon divergence and hot-set overlap.

Exit codes: 0 clean/stable, 1 drift/high-entropy/budget failure, 2 input error.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

MIB = 1024**2
GIB = 1024**3


class Fail(Exception):
    """Usage, missing file or schema error -> exit 2."""


def load_route_events(path: Path) -> List[Tuple[int, List[int], str]]:
    """Parse JSONL or JSON route trace into (layer, expert_ids, domain) tuples."""
    if not path.is_file():
        raise Fail(f"trace file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise Fail(f"{path}: trace file is empty")

    raw_items: List[dict] = []
    if text.startswith("[") or (text.startswith("{") and "\n" not in text):
        parsed = json.loads(text)
        if isinstance(parsed, list):
            raw_items = parsed
        elif isinstance(parsed, dict):
            raw_items = parsed.get("events") or parsed.get("traces") or [parsed]
    else:
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise Fail(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            if isinstance(obj, dict):
                raw_items.append(obj)

    events: List[Tuple[int, List[int], str]] = []
    for item in raw_items:
        domain = str(item.get("domain") or "default")
        if "layer" in item and isinstance(item.get("experts"), list):
            layer = int(item["layer"])
            exps = [int(e) for e in item["experts"]]
            if exps:
                events.append((layer, exps, domain))
        elif isinstance(item.get("routes"), dict):
            for lkey, exps in item["routes"].items():
                layer = int(lkey)
                if isinstance(exps, list) and exps and isinstance(exps[0], list):
                    for sub in exps:
                        if sub:
                            events.append((layer, [int(e) for e in sub], domain))
                elif isinstance(exps, list) and exps:
                    events.append((layer, [int(e) for e in exps], domain))
        elif isinstance(item.get("layers"), dict):
            for lkey, exps in item["layers"].items():
                layer = int(lkey)
                if isinstance(exps, list) and exps and isinstance(exps[0], list):
                    for sub in exps:
                        if sub:
                            events.append((layer, [int(e) for e in sub], domain))
                elif isinstance(exps, list) and exps:
                    events.append((layer, [int(e) for e in exps], domain))

    if not events:
        raise Fail(f"{path}: no routing events found (expected 'layer'+'experts' or 'routes' mapping)")
    return events


def shannon_entropy_norm(counts: Sequence[int], num_experts: int) -> float:
    total = sum(counts)
    if total <= 0 or num_experts <= 1:
        return 0.0
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * math.log2(p)
    return h / math.log2(num_experts)


def compute_profile(
    events: Sequence[Tuple[int, List[int], str]],
    num_experts: Optional[int],
    dead_threshold: float,
    hot_coverage: float,
) -> Dict[str, object]:
    max_exp_seen = max(e for _, exps, _ in events for e in exps)
    n_experts = num_experts if (num_experts and num_experts > max_exp_seen) else (max_exp_seen + 1)
    if n_experts < 2:
        raise Fail("num_experts must be >= 2")

    layer_counts: Dict[int, Counter] = defaultdict(Counter)
    layer_tokens: Counter = Counter()
    layer_coact: Dict[int, Counter] = defaultdict(Counter)
    domain_counts: Dict[str, Counter] = defaultdict(Counter)
    top_k_seen: List[int] = []

    for layer, exps, domain in events:
        layer_tokens[layer] += 1
        top_k_seen.append(len(exps))
        for e in exps:
            if e < 0 or e >= n_experts:
                raise Fail(f"expert index {e} out of range 0..{n_experts - 1}")
            layer_counts[layer][e] += 1
            domain_counts[domain][e] += 1
        uniq = sorted(set(exps))
        for a, b in itertools.combinations(uniq, 2):
            layer_coact[layer][(a, b)] += 1

    top_k = int(round(statistics.median(top_k_seen))) if top_k_seen else 1
    layers_out: Dict[str, Dict[str, object]] = {}
    entropies: List[float] = []
    hot_sizes: List[int] = []
    dead_total = 0

    for layer in sorted(layer_counts):
        counts = [layer_counts[layer].get(e, 0) for e in range(n_experts)]
        total_routes = sum(counts)
        shares = [c / total_routes if total_routes > 0 else 0.0 for c in counts]
        ent = round(shannon_entropy_norm(counts, n_experts), 4)
        entropies.append(ent)

        ranked = sorted(range(n_experts), key=lambda e: (-counts[e], e))
        cum = 0.0
        hot_k = 0
        for e in ranked:
            cum += shares[e]
            hot_k += 1
            if cum >= hot_coverage:
                break
        hot_sizes.append(hot_k)

        dead = [e for e in range(n_experts) if shares[e] < dead_threshold]
        zero_hit = [e for e in range(n_experts) if counts[e] == 0]
        dead_total += len(dead)

        top_pairs = [
            {"pair": [a, b], "count": cnt}
            for (a, b), cnt in layer_coact[layer].most_common(5)
        ]
        layers_out[str(layer)] = {
            "layer": layer,
            "tokens_routed": layer_tokens[layer],
            "total_assignments": total_routes,
            "entropy_norm": ent,
            "hot_set_size": hot_k,
            "hot_experts": ranked[:hot_k],
            "dead_count": len(dead),
            "zero_hit_count": len(zero_hit),
            "dead_experts": dead,
            "top_coactivated_pairs": top_pairs,
            "counts": counts,
            "shares": [round(s, 6) for s in shares],
        }

    total_slots = len(layers_out) * n_experts
    return {
        "format": "moe-xray-profile-v1",
        "num_layers": len(layers_out),
        "num_experts": n_experts,
        "estimated_top_k": top_k,
        "events": len(events),
        "dead_threshold": dead_threshold,
        "hot_coverage": hot_coverage,
        "domains": sorted(domain_counts),
        "summary": {
            "mean_entropy_norm": round(statistics.mean(entropies), 4) if entropies else 0.0,
            "min_entropy_norm": min(entropies) if entropies else 0.0,
            "max_entropy_norm": max(entropies) if entropies else 0.0,
            "mean_hot_set_size": round(statistics.mean(hot_sizes), 2) if hot_sizes else 0.0,
            "hot_set_fraction": round((statistics.mean(hot_sizes) / n_experts), 4) if hot_sizes else 0.0,
            "total_dead_slots": dead_total,
            "total_expert_slots": total_slots,
            "dead_slot_pct": round(100.0 * dead_total / total_slots, 2) if total_slots else 0.0,
        },
        "layers": layers_out,
    }


def expert_size_mib_from_config(cfg_path: Path, bpw: float) -> float:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    sub = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
    hidden = int(sub.get("hidden_size") or 0)
    moe_inter = int(sub.get("moe_intermediate_size") or sub.get("intermediate_size") or 0)
    if hidden <= 0 or moe_inter <= 0:
        raise Fail(f"{cfg_path}: missing hidden_size or moe_intermediate_size")
    # SwiGLU / gated MLP expert has 3 projections: gate, up, down.
    params_per_expert = 3 * hidden * moe_inter
    return (params_per_expert * (bpw / 8.0)) / MIB


def cmd_profile(args: argparse.Namespace) -> int:
    if not (0.0 < args.hot_coverage <= 1.0):
        raise Fail("--hot-coverage must be in (0, 1]")
    if not (0.0 <= args.dead_threshold < 1.0):
        raise Fail("--dead-threshold must be in [0, 1)")
    events = load_route_events(Path(args.trace))
    prof = compute_profile(events, args.num_experts, args.dead_threshold, args.hot_coverage)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(prof, indent=2) + "\n", encoding="utf-8")

    s = prof["summary"]
    print(
        f"moe-xray profile: {prof['num_layers']} layers x {prof['num_experts']} experts "
        f"(top_k~{prof['estimated_top_k']}, {prof['events']} trace events)"
    )
    print(
        f"entropy: mean={s['mean_entropy_norm']:.3f} (min={s['min_entropy_norm']:.3f}, max={s['max_entropy_norm']:.3f}) | "
        f"K{int(args.hot_coverage * 100)} mean={s['mean_hot_set_size']:.1f}/{prof['num_experts']} "
        f"({s['hot_set_fraction']*100:.1f}%) | dead={s['total_dead_slots']}/{s['total_expert_slots']} ({s['dead_slot_pct']}%)"
    )
    print(f"{'LAYER':>6} {'TOKENS':>7} {'ENTROPY':>8} {'HOT_K':>6} {'DEAD':>6} {'ZERO':>6}  TOP_PAIR")
    for lkey in sorted(prof["layers"], key=int):
        lr = prof["layers"][lkey]
        pair_txt = ""
        if lr["top_coactivated_pairs"]:
            p0 = lr["top_coactivated_pairs"][0]
            pair_txt = f"{p0['pair'][0]}+{p0['pair'][1]} ({p0['count']})"
        print(
            f"{lr['layer']:>6} {lr['tokens_routed']:>7} {lr['entropy_norm']:>8.3f} "
            f"{lr['hot_set_size']:>6} {lr['dead_count']:>6} {lr['zero_hit_count']:>6}  {pair_txt}"
        )
    return 0


def cmd_residency(args: argparse.Namespace) -> int:
    prof = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    if prof.get("format") != "moe-xray-profile-v1":
        raise Fail(f"{args.profile}: not a moe-xray-profile-v1 file")
    if args.config:
        exp_mib = expert_size_mib_from_config(Path(args.config), args.bpw)
    elif args.expert_size_mib and args.expert_size_mib > 0:
        exp_mib = args.expert_size_mib
    else:
        raise Fail("pass --expert-size-mib or --config (with --bpw)")
    if not math.isfinite(args.vram_budget_gib) or args.vram_budget_gib <= 0:
        raise Fail("--vram-budget-gib must be positive")

    slots: List[Tuple[float, int, int]] = []
    total_layers = len(prof["layers"])
    for lkey, lr in prof["layers"].items():
        layer = int(lkey)
        for eid, share in enumerate(lr["shares"]):
            slots.append((float(share), layer, eid))
    slots.sort(key=lambda x: (-x[0], x[1], x[2]))

    total_mass = sum(s[0] for s in slots) or 1.0
    max_gpu_slots = int((args.vram_budget_gib * 1024.0) // exp_mib)
    pinned = slots[:max_gpu_slots]
    spilled = slots[max_gpu_slots:]
    gpu_hit_pct = round(100.0 * sum(s[0] for s in pinned) / total_mass, 2)
    ssd_miss_pct = round(100.0 - gpu_hit_pct, 2)

    # Coverage milestones curve
    curve = []
    for target in (0.50, 0.75, 0.85, 0.90, 0.95, 0.99):
        acc = 0.0
        needed = 0
        for s in slots:
            acc += s[0]
            needed += 1
            if acc / total_mass >= target:
                break
        curve.append({
            "hit_rate_target_pct": int(target * 100),
            "experts_needed": needed,
            "vram_gib_needed": round((needed * exp_mib) / 1024.0, 2),
        })

    pinned_by_layer: Dict[str, List[int]] = defaultdict(list)
    ssd_by_layer: Dict[str, List[int]] = defaultdict(list)
    for _, layer, eid in pinned:
        pinned_by_layer[str(layer)].append(eid)
    for _, layer, eid in spilled:
        ssd_by_layer[str(layer)].append(eid)
    for d in (pinned_by_layer, ssd_by_layer):
        for k in d:
            d[k].sort()

    out = {
        "expert_size_mib": round(exp_mib, 3),
        "vram_budget_gib": args.vram_budget_gib,
        "total_expert_slots": len(slots),
        "total_routed_gib": round((len(slots) * exp_mib) / 1024.0, 2),
        "gpu_pinned_slots": len(pinned),
        "ssd_streamed_slots": len(spilled),
        "gpu_hit_rate_pct": gpu_hit_pct,
        "ssd_miss_rate_pct": ssd_miss_pct,
        "coverage_curve": curve,
        "pinned_by_layer": dict(sorted(pinned_by_layer.items(), key=lambda kv: int(kv[0]))),
        "ssd_by_layer": dict(sorted(ssd_by_layer.items(), key=lambda kv: int(kv[0]))),
        "serve_hint": "tensorfold serve MODEL --ssd-experts" if spilled else "tensorfold serve MODEL",
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(
        f"residency: {len(slots)} slots x {exp_mib:.2f} MiB = {out['total_routed_gib']:.2f} GiB routed weights "
        f"| budget={args.vram_budget_gib:.2f} GiB -> pin {len(pinned)} / spill {len(spilled)} slots"
    )
    print(f"projected route hits: GPU={gpu_hit_pct:.2f}% | SSD={ssd_miss_pct:.2f}%")
    for pt in curve:
        print(f"  {pt['hit_rate_target_pct']:>2}% hit rate -> {pt['experts_needed']:>5} slots ({pt['vram_gib_needed']:>6.2f} GiB)")
    print(f"HINT: {out['serve_hint']}")
    return 0 if gpu_hit_pct >= args.min_gpu_hit_pct else 1


def cmd_prune_plan(args: argparse.Namespace) -> int:
    if args.drop_fraction is not None and not 0.0 <= args.drop_fraction <= 1.0:
        raise Fail("--drop-fraction must be between 0 and 1")
    if not 0.0 <= args.max_route_loss_pct <= 100.0:
        raise Fail("--max-route-loss-pct must be between 0 and 100")
    if args.min_keep_per_layer < 1:
        raise Fail("--min-keep-per-layer must be positive")
    prof = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    if prof.get("format") != "moe-xray-profile-v1":
        raise Fail(f"{args.profile}: not a moe-xray-profile-v1 file")
    n_experts = int(prof["num_experts"])
    min_keep = max(int(args.min_keep_per_layer), int(prof.get("estimated_top_k") or 1))
    if min_keep >= n_experts:
        raise Fail(f"--min-keep-per-layer ({min_keep}) must be < num_experts ({n_experts})")

    keep_map: Dict[str, List[int]] = {}
    drop_map: Dict[str, List[int]] = {}
    warnings: List[str] = []
    total_dropped = 0
    total_slots = 0
    mass_lost_vals: List[float] = []

    for lkey in sorted(prof["layers"], key=int):
        lr = prof["layers"][lkey]
        shares = [float(s) for s in lr["shares"]]
        ranked_asc = sorted(range(n_experts), key=lambda e: (shares[e], e))
        max_drop = n_experts - min_keep

        if args.drop_fraction is not None:
            target_drop = min(max_drop, int(round(n_experts * args.drop_fraction)))
            dropped = ranked_asc[:target_drop]
            lost = sum(shares[e] for e in dropped)
        else:
            budget = args.max_route_loss_pct / 100.0
            dropped = []
            lost = 0.0
            for e in ranked_asc:
                if len(dropped) >= max_drop:
                    break
                if lost + shares[e] <= budget:
                    dropped.append(e)
                    lost += shares[e]
                else:
                    break

        kept = sorted(set(range(n_experts)) - set(dropped))
        keep_map[lkey] = kept
        drop_map[lkey] = sorted(dropped)
        total_dropped += len(dropped)
        total_slots += n_experts
        mass_lost_vals.append(lost * 100.0)
        if float(lr["entropy_norm"]) >= 0.92 and dropped:
            warnings.append(f"layer {lkey}: high routing entropy ({lr['entropy_norm']:.3f}); dropping {len(dropped)} experts risks steep quality loss")

    dropped_pct = round(100.0 * total_dropped / total_slots, 2) if total_slots else 0.0
    mean_loss = round(statistics.mean(mass_lost_vals), 3) if mass_lost_vals else 0.0
    max_loss = round(max(mass_lost_vals), 3) if mass_lost_vals else 0.0

    plan = {
        "num_layers": len(prof["layers"]),
        "num_experts_before": n_experts,
        "dropped_slots": total_dropped,
        "total_slots": total_slots,
        "routed_weight_reduction_pct": dropped_pct,
        "mean_route_mass_affected_pct": mean_loss,
        "max_layer_route_mass_affected_pct": max_loss,
        "warnings": warnings,
        "keep_experts": keep_map,
        "drop_experts": drop_map,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

    print(
        f"prune-plan: drop {total_dropped}/{total_slots} routed expert slots ({dropped_pct:.1f}% routed weight reduction) | "
        f"route mass affected: mean={mean_loss:.2f}%, max_layer={max_loss:.2f}%"
    )
    for w in warnings[:10]:
        print(f"  [WARN] {w}")
    return 0


def js_divergence(p: Sequence[float], q: Sequence[float]) -> float:
    m = [(a + b) * 0.5 for a, b in zip(p, q)]
    kl_pm = sum(a * math.log2(a / mv) for a, mv in zip(p, m) if a > 0 and mv > 0)
    kl_qm = sum(b * math.log2(b / mv) for b, mv in zip(q, m) if b > 0 and mv > 0)
    return 0.5 * (kl_pm + kl_qm)


def cmd_diff(args: argparse.Namespace) -> int:
    pa = json.loads(Path(args.profile_a).read_text(encoding="utf-8"))
    pb = json.loads(Path(args.profile_b).read_text(encoding="utf-8"))
    if pa.get("format") != "moe-xray-profile-v1" or pb.get("format") != "moe-xray-profile-v1":
        raise Fail("both inputs must be moe-xray-profile-v1 JSON files")
    if pa["num_experts"] != pb["num_experts"]:
        raise Fail(f"expert count mismatch ({pa['num_experts']} vs {pb['num_experts']})")

    shared_layers = sorted(set(pa["layers"]) & set(pb["layers"]), key=int)
    if not shared_layers:
        raise Fail("profiles share no layer indices")

    rows = []
    for lkey in shared_layers:
        la, lb = pa["layers"][lkey], pb["layers"][lkey]
        jsd = round(js_divergence(la["shares"], lb["shares"]), 4)
        ha, hb = set(la["hot_experts"]), set(lb["hot_experts"])
        jaccard = round(len(ha & hb) / len(ha | hb), 3) if (ha | hb) else 1.0
        rows.append({"layer": int(lkey), "jsd": jsd, "hot_jaccard": jaccard})

    max_jsd = max(r["jsd"] for r in rows)
    mean_jsd = round(statistics.mean(r["jsd"] for r in rows), 4)
    mean_jac = round(statistics.mean(r["hot_jaccard"] for r in rows), 3)
    drifted = [r for r in rows if r["jsd"] > args.max_jsd]

    out = {
        "layers_compared": len(rows),
        "mean_jsd": mean_jsd,
        "max_jsd": max_jsd,
        "mean_hot_jaccard": mean_jac,
        "max_jsd_threshold": args.max_jsd,
        "drifted_layers": [r["layer"] for r in drifted],
        "layers": rows,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"router diff: {len(rows)} layers | mean_jsd={mean_jsd:.4f} max_jsd={max_jsd:.4f} mean_hot_jaccard={mean_jac:.3f}")
    if drifted:
        print(f"ROUTER DIFF: DRIFT_DETECTED ({len(drifted)} layer(s) above max_jsd={args.max_jsd}: {[r['layer'] for r in drifted[:8]]})")
        return 1
    print("ROUTER DIFF: STABLE")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prof = sub.add_parser("profile", help="profile expert activation frequency, entropy, hot sets and dead experts")
    p_prof.add_argument("trace", help="path to TensorFold --fsr-route-trace JSONL/JSON file")
    p_prof.add_argument("--num-experts", type=int, help="total experts per layer (inferred if omitted)")
    p_prof.add_argument("--dead-threshold", type=float, default=0.001, help="share threshold below which an expert is cold/dead (default: 0.001)")
    p_prof.add_argument("--hot-coverage", type=float, default=0.90, help="cumulative share defining each layer's hot set (default: 0.90)")
    p_prof.add_argument("--json", dest="json_out", help="write profile JSON")

    p_res = sub.add_parser("residency", help="plan GPU-resident hot experts vs --ssd-experts streaming")
    p_res.add_argument("profile", help="path to moe-xray profile JSON")
    p_res.add_argument("--vram-budget-gib", type=float, required=True, help="GPU/working-set GiB available for routed experts")
    p_res.add_argument("--expert-size-mib", type=float, help="single expert weight size in MiB")
    p_res.add_argument("--config", help="checkpoint config.json to compute expert size automatically")
    p_res.add_argument("--bpw", type=float, default=4.5, help="effective bpw when using --config (default: 4.5)")
    p_res.add_argument("--min-gpu-hit-pct", type=float, default=0.0, help="optional minimum GPU hit rate %% gate (default: 0)")
    p_res.add_argument("--json", dest="json_out", help="write residency manifest JSON")

    p_pr = sub.add_parser("prune-plan", help="generate a cold-expert pruning manifest under a route-loss or drop budget")
    p_pr.add_argument("profile", help="path to moe-xray profile JSON")
    mode = p_pr.add_mutually_exclusive_group()
    mode.add_argument("--drop-fraction", type=float, help="fixed fraction of experts to drop per layer (e.g. 0.25)")
    mode.add_argument("--max-route-loss-pct", type=float, default=1.5, help="max route mass %% affected per layer (default: 1.5)")
    p_pr.add_argument("--min-keep-per-layer", type=int, default=4, help="minimum kept experts per layer (default: 4)")
    p_pr.add_argument("--json", dest="json_out", help="write prune manifest JSON")

    p_diff = sub.add_parser("diff", help="measure per-layer router drift (JSD) between two routing profiles")
    p_diff.add_argument("profile_a", help="baseline routing profile JSON")
    p_diff.add_argument("profile_b", help="candidate routing profile JSON")
    p_diff.add_argument("--max-jsd", type=float, default=0.05, help="max allowed Jensen-Shannon divergence per layer (default: 0.05)")
    p_diff.add_argument("--json", dest="json_out", help="write diff report JSON")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "profile":
            return cmd_profile(args)
        if args.cmd == "residency":
            return cmd_residency(args)
        if args.cmd == "prune-plan":
            return cmd_prune_plan(args)
        if args.cmd == "diff":
            return cmd_diff(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"moe_xray.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
