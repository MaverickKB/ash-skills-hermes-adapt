#!/usr/bin/env python3
"""Maintain a TensorFold optimization ledger, block dead-end repeats and score mirrored A/B runs.

Prevents fresh sessions from re-testing documented losses, enforces TensorFold's
exact mirrored `B, C, C, B` benchmark protocol with base-vs-base noise floors,
clock/thermal throttling drops and 64-hex token-hash checks, and tracks
cross-backend/cross-family `OWED_PORT` debt for every kept win. Uses only the
Python 3.9+ standard library.

Subcommands:
  preflight    Check mechanism against recorded losses and built-in dead-end
               archetypes before writing code; require `--changed-assumption`
               to retry a recorded loss.
  score-ab     Verify a mirrored `B, C, C, B` benchmark receipt (noise floor,
               thermal drops, token hashes, zero cell regressions).
  record       Append a verified KEPT, REVERTED or FLAT entry to ledger.jsonl.
  owed         List kept speedups still owed to sibling backends or families.
  mark-ported  Mark an owed `backend:family` target as ported for an entry ID.

Exit codes: 0 clean/confirmed, 1 blocked repeat or A/B rejection, 2 usage/schema error.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")

BUILTIN_LOSSES: Dict[str, str] = {
    "dispatch_count_only": "cutting dispatch count without removing critical-path time",
    "secondary_queue_prefetch": "prefetching next-step weights from a second queue or host CPU contends on bandwidth",
    "persistent_device_barrier": "persistent kernels spinning on device-wide barriers stall concurrent waves",
    "single_gpu_tp": "tensor parallelism on a single GPU adds sync overhead with zero extra silicon",
}


class Fail(Exception):
    """Usage, missing file or schema error -> exit 2."""


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_mech(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def read_ledger(path: Path) -> List[Dict[str, object]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, object]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Fail(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def write_ledger(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def cmd_preflight(args: argparse.Namespace) -> int:
    mech = norm_mech(args.mechanism)
    if not mech:
        raise Fail("--mechanism cannot be empty")
    rows = read_ledger(Path(args.ledger))
    hits: List[str] = []

    if mech in BUILTIN_LOSSES:
        hits.append(f"built-in known loss '{mech}': {BUILTIN_LOSSES[mech]}")

    for r in rows:
        status = str(r.get("status") or "").upper()
        if status not in ("REVERTED", "FLAT", "LOSS"):
            continue
        rmech = norm_mech(str(r.get("mechanism") or ""))
        if rmech != mech:
            continue
        same_backend = (not args.backend) or (str(r.get("backend") or "").lower() == args.backend.lower())
        same_family = (not args.family) or (str(r.get("family") or "").lower() == args.family.lower())
        if same_backend and same_family:
            hits.append(
                f"prior {status} id={r.get('id')} ({r.get('backend')}/{r.get('family')}, "
                f"measured={r.get('measured_gain_pct')}%): {r.get('notes', '')}"
            )

    out = {
        "mechanism": mech,
        "backend": args.backend,
        "family": args.family,
        "prior_losses": hits,
        "changed_assumption": args.changed_assumption or "",
        "allowed": (not hits) or bool(args.changed_assumption),
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    if hits and not args.changed_assumption:
        for h in hits:
            print(f"  [PRIOR LOSS] {h}")
        print("LEDGER: BLOCKED_REPEAT (pass --changed-assumption naming what changed since the prior loss)")
        return 1
    if hits and args.changed_assumption:
        print(f"LEDGER: PREFLIGHT_OK (retrying prior loss with new assumption: {args.changed_assumption})")
        return 0
    print("LEDGER: PREFLIGHT_OK (no prior loss recorded for this mechanism)")
    return 0


def _require_sha(val: object, label: str) -> str:
    if not isinstance(val, str) or SHA256_RE.fullmatch(val) is None:
        raise Fail(f"{label}: expected full 64-hex token_sha256")
    return val.lower()


def cmd_score_ab(args: argparse.Namespace) -> int:
    p = Path(args.receipt)
    if not p.is_file():
        raise Fail(f"A/B receipt not found: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("cells"), dict) or not doc["cells"]:
        raise Fail("receipt must contain a non-empty 'cells' mapping")

    target_cell = str(doc.get("target_cell") or next(iter(doc["cells"])))
    if target_cell not in doc["cells"]:
        raise Fail(f"target_cell {target_cell!r} not in cells")

    cell_reports: Dict[str, Dict[str, object]] = {}
    exact_failures: List[str] = []
    regressions: List[str] = []
    target_confirmed = False

    for cname, cell in sorted(doc["cells"].items()):
        order = [str(x).upper() for x in (cell.get("order") or [])]
        if order != ["B", "C", "C", "B"]:
            raise Fail(f"cell {cname!r}: order must be mirrored ['B','C','C','B'], got {order}")
        bvb = cell.get("base_vs_base") or []
        if len(bvb) < 2:
            raise Fail(f"cell {cname!r}: base_vs_base requires at least 2 identical-arm measurements")
        bvb_vals = [float(v) for v in bvb]
        if any(not math.isfinite(v) or v <= 0 for v in bvb_vals):
            raise Fail(f"cell {cname!r}: base_vs_base values must be finite and positive")
        noise_pct = round(((max(bvb_vals) - min(bvb_vals)) / statistics.mean(bvb_vals)) * 100.0, 2)

        rounds = cell.get("rounds") or []
        if not rounds:
            raise Fail(f"cell {cname!r}: missing 'rounds' list")

        clean_deltas: List[float] = []
        base_rates: List[float] = []
        cand_rates: List[float] = []
        dropped_throttled = 0

        for idx, rd in enumerate(rounds, 1):
            b_sha = _require_sha(rd.get("base_token_sha256"), f"{cname} round {idx} base")
            c_sha = _require_sha(rd.get("cand_token_sha256"), f"{cname} round {idx} cand")
            if b_sha != c_sha:
                exact_failures.append(f"{cname} round {idx}")
            if rd.get("throttled"):
                dropped_throttled += 1
                continue
            max_temp = float(args.max_temp_c)
            if rd.get("temp_c") is not None and float(rd["temp_c"]) > max_temp:
                dropped_throttled += 1
                continue
            b_tps = float(rd["base_tps"])
            c_tps = float(rd["cand_tps"])
            if not math.isfinite(b_tps) or not math.isfinite(c_tps) or b_tps <= 0 or c_tps <= 0:
                raise Fail(f"{cname} round {idx}: base_tps and cand_tps must be finite and positive")
            delta_pct = ((c_tps - b_tps) / b_tps) * 100.0
            clean_deltas.append(delta_pct)
            base_rates.append(b_tps)
            cand_rates.append(c_tps)

        if len(clean_deltas) < 2:
            raise Fail(f"cell {cname!r}: fewer than 2 unthrottled rounds remain ({len(clean_deltas)} clean, {dropped_throttled} dropped)")

        med_base = round(statistics.median(base_rates), 2)
        med_cand = round(statistics.median(cand_rates), 2)
        med_delta = round(statistics.median(clean_deltas), 2)
        # TensorFold rule: cell is slower when candidate is below base by > noise floor in every round.
        all_rounds_regressed = all(d < -noise_pct for d in clean_deltas)
        all_rounds_won = all(d > noise_pct for d in clean_deltas)
        if all_rounds_regressed:
            regressions.append(f"{cname} ({med_base} -> {med_cand} tok/s, {med_delta:+.2f}% vs noise {noise_pct:.2f}%)")
        if cname == target_cell and all_rounds_won:
            target_confirmed = True

        cell_reports[cname] = {
            "clean_rounds": len(clean_deltas),
            "dropped_throttled": dropped_throttled,
            "noise_floor_pct": noise_pct,
            "median_base_tps": med_base,
            "median_cand_tps": med_cand,
            "median_delta_pct": med_delta,
            "all_rounds_won": all_rounds_won,
            "all_rounds_regressed": all_rounds_regressed,
        }

    verdict = "AB: CONFIRMED_GAIN"
    exit_code = 0
    if exact_failures:
        verdict, exit_code = f"AB: REJECTED_EXACTNESS ({', '.join(exact_failures)})", 1
    elif regressions:
        verdict, exit_code = f"AB: REJECTED_CELL_REGRESSION ({'; '.join(regressions)})", 1
    elif not target_confirmed:
        verdict, exit_code = "AB: INCONCLUSIVE_NOISE (target cell did not clear noise floor in every clean round)", 1

    out = {
        "format": "optimization-ledger-ab-v1",
        "target_cell": target_cell,
        "cells": cell_reports,
        "exact_failures": exact_failures,
        "regressions": regressions,
        "passed": exit_code == 0,
        "verdict": verdict,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    for cname, cr in cell_reports.items():
        mark = "*" if cname == target_cell else " "
        print(
            f"{mark} {cname:<22} {cr['median_base_tps']:>7.2f} -> {cr['median_cand_tps']:>7.2f} tok/s "
            f"({cr['median_delta_pct']:>+6.2f}%, noise={cr['noise_floor_pct']:.2f}%, clean={cr['clean_rounds']})"
        )
    print(verdict)
    return exit_code


def cmd_record(args: argparse.Namespace) -> int:
    path = Path(args.ledger)
    rows = read_ledger(path)
    owed = [x.strip() for x in (args.owed_ports or "").split(",") if x.strip()]
    entry = {
        "id": args.id or f"exp-{len(rows) + 1:04d}",
        "timestamp": now_utc(),
        "mechanism": norm_mech(args.mechanism),
        "backend": args.backend,
        "family": args.family,
        "hardware": args.hardware,
        "predicted_gain_pct": args.predicted_gain_pct,
        "measured_gain_pct": args.measured_gain_pct,
        "noise_pct": args.noise_pct,
        "status": args.status,
        "commit": args.commit or "",
        "binary_sha256": args.binary_sha256 or "",
        "owed_ports": owed if args.status == "KEPT" else [],
        "ported": [],
        "notes": args.notes or "",
    }
    rows.append(entry)
    write_ledger(path, rows)
    print(f"recorded {entry['id']} [{entry['status']}] {entry['mechanism']} ({entry['measured_gain_pct']:+.2f}%) -> {path}")
    return 0


def cmd_owed(args: argparse.Namespace) -> int:
    rows = read_ledger(Path(args.ledger))
    debt = []
    for r in rows:
        if str(r.get("status") or "").upper() != "KEPT":
            continue
        remaining = [t for t in (r.get("owed_ports") or []) if t not in set(r.get("ported") or [])]
        if remaining:
            debt.append({
                "id": r.get("id"),
                "mechanism": r.get("mechanism"),
                "origin": f"{r.get('backend')}:{r.get('family')}",
                "measured_gain_pct": float(r.get("measured_gain_pct") or 0.0),
                "remaining_ports": remaining,
            })
    debt.sort(key=lambda x: -float(x["measured_gain_pct"]))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"owed_count": len(debt), "items": debt}, indent=2) + "\n", encoding="utf-8")
    if not debt:
        print("OWED PORTS: NONE")
        return 0
    print(f"owed ports ({len(debt)} kept wins waiting on sibling targets):")
    for item in debt:
        print(f"  {item['id']}  +{item['measured_gain_pct']:.1f}%  {item['mechanism']} (from {item['origin']}) -> {', '.join(item['remaining_ports'])}")
    return 0


def cmd_mark_ported(args: argparse.Namespace) -> int:
    path = Path(args.ledger)
    rows = read_ledger(path)
    found = False
    for r in rows:
        if r.get("id") == args.id:
            found = True
            ported = list(r.get("ported") or [])
            if args.target not in ported:
                ported.append(args.target)
            r["ported"] = ported
            break
    if not found:
        raise Fail(f"entry id {args.id!r} not found in {path}")
    write_ledger(path, rows)
    print(f"marked {args.target} ported for {args.id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("preflight", help="check mechanism against recorded losses before building")
    p_pre.add_argument("--ledger", required=True, help="path to optimization-ledger.jsonl")
    p_pre.add_argument("--mechanism", required=True, help="short mechanism identifier")
    p_pre.add_argument("--backend", default="", help="target backend (cuda, metal, rocm)")
    p_pre.add_argument("--family", default="", help="target model family")
    p_pre.add_argument("--changed-assumption", help="required explanation if retrying a recorded loss")
    p_pre.add_argument("--json", dest="json_out")

    p_ab = sub.add_parser("score-ab", help="score a mirrored B,C,C,B benchmark receipt with thermal and exactness gates")
    p_ab.add_argument("receipt", help="path to mirrored A/B receipt JSON")
    p_ab.add_argument("--max-temp-c", type=float, default=85.0, help="drop rounds above this GPU temperature C (default: 85)")
    p_ab.add_argument("--json", dest="json_out")

    p_rec = sub.add_parser("record", help="append an experiment outcome to ledger.jsonl")
    p_rec.add_argument("--ledger", required=True)
    p_rec.add_argument("--id")
    p_rec.add_argument("--mechanism", required=True)
    p_rec.add_argument("--backend", required=True)
    p_rec.add_argument("--family", required=True)
    p_rec.add_argument("--hardware", required=True)
    p_rec.add_argument("--predicted-gain-pct", type=float, required=True)
    p_rec.add_argument("--measured-gain-pct", type=float, required=True)
    p_rec.add_argument("--noise-pct", type=float, required=True)
    p_rec.add_argument("--status", required=True, choices=("KEPT", "REVERTED", "FLAT"))
    p_rec.add_argument("--owed-ports", help="comma-separated backend:family targets where a KEPT win still needs porting")
    p_rec.add_argument("--commit")
    p_rec.add_argument("--binary-sha256")
    p_rec.add_argument("--notes")

    p_ow = sub.add_parser("owed", help="list KEPT speedups still owed to sibling backends or families")
    p_ow.add_argument("--ledger", required=True)
    p_ow.add_argument("--json", dest="json_out")

    p_mp = sub.add_parser("mark-ported", help="mark one owed backend:family port complete")
    p_mp.add_argument("--ledger", required=True)
    p_mp.add_argument("--id", required=True)
    p_mp.add_argument("--target", required=True, help="backend:family target marked complete")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "preflight":
            return cmd_preflight(args)
        if args.cmd == "score-ab":
            return cmd_score_ab(args)
        if args.cmd == "record":
            return cmd_record(args)
        if args.cmd == "owed":
            return cmd_owed(args)
        if args.cmd == "mark-ported":
            return cmd_mark_ported(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"ledger.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
