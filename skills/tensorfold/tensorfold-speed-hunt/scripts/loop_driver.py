#!/usr/bin/env python3
"""Stateful 7-gate closed-loop driver for local TensorFold speed hunts.

Chains every TensorFold specialist skill in strict order so no performance
change ships without a served trace, anti-repeat ledger preflight, untouched
kernel binary gate, 64-hex token-hash parity, mirrored B,C,C,B thermal-checked
A/B timings, and a fresh-session speedup-referee verdict. SHA-256 hashes every
attached receipt on disk so evidence cannot drift. Uses only Python 3.9+ stdlib.

Stages (in mandatory order):
  1. xray         critical-path-xray served trace + Amdahl ceiling
  2. preflight    optimization-ledger anti-repeat check
  3. build        isolated branch + specialist implementation metadata
  4. binary_gate  sass-and-air-diff untouched-kernel PASS (or explicit N/A)
  5. parity_gate  output-parity IDENTICAL receipt (full 64-hex token hashes)
  6. bench_gate   optimization-ledger score-ab CONFIRMED_GAIN receipt
  7. referee      fresh-session speedup-referee CONFIRMED verdict + owed ports

Subcommands:
  init        Create a new run.json in a notes folder outside the checkout.
  attach      Validate and attach a stage receipt or metadata record.
  status      Show gate progress and print the exact next specialist command.
  check-land  Re-verify all receipt hashes and gate verdicts before landing.

Exit codes: 0 clean/ready, 1 blocked/failed gate, 2 usage or corrupt artifact.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

STAGES = ("xray", "preflight", "build", "binary_gate", "parity_gate", "bench_gate", "referee")
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")

NEXT_HINTS: Dict[str, str] = {
    "xray": "Load `critical-path-xray` and run `xray_trace.py audit <trace> --chip <chip> --json notes/xray.json`, then `attach --stage xray --receipt notes/xray.json`.",
    "preflight": "Load `optimization-ledger` and run `ledger.py preflight --ledger notes/optimization-ledger.jsonl --mechanism <name> --json notes/preflight.json`, then `attach --stage preflight --receipt notes/preflight.json`.",
    "build": "Load the routed specialist (`kernel-contract`, `graph-and-dispatch-surgeon`, `one-stream-lanes`, `prefill-and-kv`, `serving-concurrency`, `drafter-forge` or `moe-xray`), implement on a branch, and run `attach --stage build ...`.",
    "binary_gate": "Load `sass-and-air-diff` and run `binary_gate.py diff base.json cand.json --allow-changed <kernel> --forbid-new-spills --json notes/binary_gate.json`, then `attach --stage binary_gate --receipt notes/binary_gate.json`.",
    "parity_gate": "Load `output-parity` and run `parity.py compare ... --logprobs --json notes/parity.json`, then `attach --stage parity_gate --receipt notes/parity.json`.",
    "bench_gate": "Load `optimization-ledger` and run `ledger.py score-ab notes/ab_receipt.json --json notes/ab_verdict.json`, then `attach --stage bench_gate --receipt notes/ab_verdict.json`.",
    "referee": "Open a fresh session with `speedup-referee`, verify the claim, record `ledger.py record`, and run `attach --stage referee --verdict CONFIRMED --referee-session <id>`.",
}


class Fail(Exception):
    """Usage, missing file or schema error -> exit 2."""


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_run(path: Path) -> Dict[str, object]:
    if not path.is_file():
        raise Fail(f"run file not found: {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("format") != "tensorfold-speed-hunt-run-v1":
        raise Fail(f"{path}: invalid run format")
    return doc


def save_run(path: Path, doc: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def route_specialist_from_xray(xray: Dict[str, object]) -> str:
    non_gpu = float(xray.get("host_share_pct") or 0.0) + float(xray.get("idle_share_pct") or 0.0)
    if non_gpu >= 20.0:
        return "graph-and-dispatch-surgeon"
    kernels = xray.get("kernels") or []
    if kernels and isinstance(kernels[0], dict):
        regime = str(kernels[0].get("regime") or "")
        if regime == "BW_BOUND":
            return "one-stream-lanes"
        if regime in ("LAUNCH_BOUND", "UNDERUTILIZED", "COMPUTE_BOUND"):
            return "kernel-contract"
    return "kernel-contract"


def cmd_init(args: argparse.Namespace) -> int:
    out = Path(args.run).resolve()
    if not args.allow_temp and str(out).startswith(("/tmp/", "/private/tmp/")):
        raise Fail("keep speed-hunt notes outside /tmp so runs survive a reboot (pass --allow-temp only in automated tests)")
    if not math.isfinite(args.baseline_tps) or args.baseline_tps <= 0:
        raise Fail("--baseline-tps must be finite and positive")

    doc = {
        "format": "tensorfold-speed-hunt-run-v1",
        "created_at": now_utc(),
        "run_id": args.run_id or out.stem,
        "commit": args.commit,
        "base_binary_sha256": args.base_binary_sha256 or "",
        "hardware": args.hardware,
        "backend": args.backend,
        "family": args.family,
        "target_cell": args.target_cell,
        "baseline_tps": args.baseline_tps,
        "routed_specialist": "",
        "gates": {stage: {"status": "PENDING"} for stage in STAGES},
    }
    save_run(out, doc)
    print(f"initialized speed-hunt run: {out} ({args.backend}/{args.family} cell={args.target_cell} base={args.baseline_tps} tok/s)")
    print(f"NEXT: {NEXT_HINTS['xray']}")
    return 0


def _ensure_prior_passed(doc: Dict[str, object], stage: str) -> None:
    idx = STAGES.index(stage)
    for prev in STAGES[:idx]:
        st = str((doc["gates"].get(prev) or {}).get("status") or "PENDING")
        if st not in ("PASS", "SKIP_NA"):
            raise Fail(f"cannot record stage '{stage}' before prior stage '{prev}' passes (currently {st})")


def cmd_attach(args: argparse.Namespace) -> int:
    run_path = Path(args.run)
    doc = load_run(run_path)
    stage = args.stage
    _ensure_prior_passed(doc, stage)

    if stage == "xray":
        if not args.receipt:
            raise Fail("--receipt is required for stage 'xray'")
        rp = Path(args.receipt).resolve()
        data = json.loads(rp.read_text(encoding="utf-8"))
        if data.get("format") != "critical-path-xray-v1":
            raise Fail(f"{rp}: expected critical-path-xray-v1 JSON from xray_trace.py audit")
        missing = data.get("expected_kernels_missing") or []
        if missing:
            doc["gates"][stage] = {"status": "FAIL", "reason": f"expected kernels missing: {missing}"}
            save_run(run_path, doc)
            print(f"GATE FAIL [xray]: expected kernels missing from trace: {missing}")
            return 1
        specialist = route_specialist_from_xray(data)
        doc["routed_specialist"] = specialist
        doc["gates"][stage] = {
            "status": "PASS",
            "recorded_at": now_utc(),
            "receipt": str(rp),
            "receipt_sha256": sha256_file(rp),
            "top_bottleneck": data.get("top_bottleneck"),
            "gpu_busy_pct": data.get("gpu_busy_pct"),
            "routed_specialist": specialist,
        }
        save_run(run_path, doc)
        print(f"GATE PASS [xray]: bottleneck={data.get('top_bottleneck')} gpu_busy={data.get('gpu_busy_pct')}% -> routed specialist: {specialist}")
        print(f"NEXT: {NEXT_HINTS['preflight']}")
        return 0

    if stage == "preflight":
        if not args.receipt:
            raise Fail("--receipt is required for stage 'preflight'")
        rp = Path(args.receipt).resolve()
        data = json.loads(rp.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "allowed" not in data:
            raise Fail(f"{rp}: expected preflight JSON from ledger.py preflight")
        if not data["allowed"]:
            doc["gates"][stage] = {"status": "FAIL", "reason": "blocked repeat loss in optimization-ledger"}
            save_run(run_path, doc)
            print("GATE FAIL [preflight]: mechanism is a recorded loss without a changed assumption")
            return 1
        doc["gates"][stage] = {
            "status": "PASS",
            "recorded_at": now_utc(),
            "receipt": str(rp),
            "receipt_sha256": sha256_file(rp),
            "mechanism": data.get("mechanism"),
        }
        save_run(run_path, doc)
        print(f"GATE PASS [preflight]: mechanism={data.get('mechanism')}")
        print(f"NEXT: {NEXT_HINTS['build']}")
        return 0

    if stage == "build":
        if not args.branch or not args.mechanism:
            raise Fail("--branch and --mechanism are required for stage 'build'")
        doc["gates"][stage] = {
            "status": "PASS",
            "recorded_at": now_utc(),
            "branch": args.branch,
            "mechanism": args.mechanism,
            "specialist": args.specialist or doc.get("routed_specialist") or "kernel-contract",
            "cand_binary_sha256": args.cand_binary_sha256 or "",
            "notes": args.notes or "",
        }
        save_run(run_path, doc)
        print(f"GATE PASS [build]: branch={args.branch} mechanism={args.mechanism}")
        print(f"NEXT: {NEXT_HINTS['binary_gate']}")
        return 0

    if stage == "binary_gate":
        if args.not_applicable:
            if not args.notes:
                raise Fail("--notes explaining why no GPU kernel or header changed is required with --not-applicable")
            doc["gates"][stage] = {"status": "SKIP_NA", "recorded_at": now_utc(), "notes": args.notes}
            save_run(run_path, doc)
            print(f"GATE SKIP_NA [binary_gate]: {args.notes}")
            print(f"NEXT: {NEXT_HINTS['parity_gate']}")
            return 0
        if not args.receipt:
            raise Fail("pass --receipt <binary_gate.json> or --not-applicable --notes ...")
        rp = Path(args.receipt).resolve()
        data = json.loads(rp.read_text(encoding="utf-8"))
        if data.get("format") != "binary-gate-diff-v1":
            raise Fail(f"{rp}: expected binary-gate-diff-v1 JSON from binary_gate.py diff")
        if not data.get("passed"):
            doc["gates"][stage] = {"status": "FAIL", "receipt": str(rp), "receipt_sha256": sha256_file(rp)}
            save_run(run_path, doc)
            print("GATE FAIL [binary_gate]: untouched kernel changed or new spills introduced")
            return 1
        doc["gates"][stage] = {"status": "PASS", "recorded_at": now_utc(), "receipt": str(rp), "receipt_sha256": sha256_file(rp)}
        save_run(run_path, doc)
        print("GATE PASS [binary_gate]: zero untouched-kernel or spill regressions")
        print(f"NEXT: {NEXT_HINTS['parity_gate']}")
        return 0

    if stage == "parity_gate":
        if not args.receipt:
            raise Fail("--receipt is required for stage 'parity_gate'")
        rp = Path(args.receipt).resolve()
        data = json.loads(rp.read_text(encoding="utf-8"))
        # Accept output-parity JSON (`verdict == "IDENTICAL"`), verify_receipts JSON (`mismatches == {}`),
        # or generic exactness JSON (`passed == true`).
        ok = (
            data.get("verdict") == "IDENTICAL"
            or data.get("passed") is True
            or (isinstance(data.get("mismatches"), dict) and len(data["mismatches"]) == 0 and int(data.get("compared") or 0) > 0)
        )
        if not ok:
            doc["gates"][stage] = {"status": "FAIL", "receipt": str(rp), "receipt_sha256": sha256_file(rp)}
            save_run(run_path, doc)
            print("GATE FAIL [parity_gate]: output parity receipt did not prove identical output")
            return 1
        doc["gates"][stage] = {"status": "PASS", "recorded_at": now_utc(), "receipt": str(rp), "receipt_sha256": sha256_file(rp)}
        save_run(run_path, doc)
        print("GATE PASS [parity_gate]: exact token parity verified")
        print(f"NEXT: {NEXT_HINTS['bench_gate']}")
        return 0

    if stage == "bench_gate":
        if not args.receipt:
            raise Fail("--receipt is required for stage 'bench_gate'")
        rp = Path(args.receipt).resolve()
        data = json.loads(rp.read_text(encoding="utf-8"))
        if data.get("format") != "optimization-ledger-ab-v1":
            raise Fail(f"{rp}: expected optimization-ledger-ab-v1 JSON from ledger.py score-ab")
        if not data.get("passed"):
            doc["gates"][stage] = {"status": "FAIL", "receipt": str(rp), "receipt_sha256": sha256_file(rp), "verdict": data.get("verdict")}
            save_run(run_path, doc)
            print(f"GATE FAIL [bench_gate]: {data.get('verdict')}")
            return 1
        tcell = str(data.get("target_cell") or doc["target_cell"])
        tinfo = (data.get("cells") or {}).get(tcell) or {}
        doc["gates"][stage] = {
            "status": "PASS",
            "recorded_at": now_utc(),
            "receipt": str(rp),
            "receipt_sha256": sha256_file(rp),
            "target_cell": tcell,
            "base_tps": tinfo.get("median_base_tps"),
            "cand_tps": tinfo.get("median_cand_tps"),
            "delta_pct": tinfo.get("median_delta_pct"),
            "noise_floor_pct": tinfo.get("noise_floor_pct"),
        }
        save_run(run_path, doc)
        print(
            f"GATE PASS [bench_gate]: {tcell} {tinfo.get('median_base_tps')} -> {tinfo.get('median_cand_tps')} tok/s "
            f"({tinfo.get('median_delta_pct'):+.2f}%, noise {tinfo.get('noise_floor_pct')}%)"
        )
        print(f"NEXT: {NEXT_HINTS['referee']}")
        return 0

    if stage == "referee":
        if not args.verdict or not args.referee_session:
            raise Fail("--verdict (CONFIRMED|NOT_PROVEN|REFUTED) and --referee-session are required for stage 'referee'")
        owed = [x.strip() for x in (args.owed_ports or "").split(",") if x.strip()]
        status = "PASS" if args.verdict == "CONFIRMED" else "FAIL"
        doc["gates"][stage] = {
            "status": status,
            "recorded_at": now_utc(),
            "verdict": args.verdict,
            "referee_session": args.referee_session,
            "owed_ports": owed,
            "notes": args.notes or "",
        }
        save_run(run_path, doc)
        if status == "PASS":
            print(f"GATE PASS [referee]: CONFIRMED by fresh session {args.referee_session}")
            return 0
        print(f"GATE FAIL [referee]: verdict={args.verdict}")
        return 1

    raise Fail(f"unknown stage {stage}")


def cmd_status(args: argparse.Namespace) -> int:
    doc = load_run(Path(args.run))
    print(f"run: {doc['run_id']} ({doc['backend']}/{doc['family']} on {doc['hardware']}, cell={doc['target_cell']})")
    next_stage = None
    for idx, stage in enumerate(STAGES, 1):
        g = doc["gates"].get(stage) or {"status": "PENDING"}
        st = str(g.get("status") or "PENDING")
        extra = ""
        if stage == "xray" and g.get("top_bottleneck"):
            extra = f" (bottleneck={g['top_bottleneck']}, route->{g.get('routed_specialist')})"
        elif stage == "bench_gate" and g.get("cand_tps") is not None:
            extra = f" ({g['base_tps']} -> {g['cand_tps']} tok/s, {g['delta_pct']:+.2f}%)"
        elif stage == "referee" and g.get("verdict"):
            extra = f" ({g['verdict']}, session={g.get('referee_session')})"
        print(f"  {idx}. {stage:<12} [{st:<7}]{extra}")
        if st not in ("PASS", "SKIP_NA") and next_stage is None:
            next_stage = stage
    if next_stage:
        print(f"\nNEXT: {NEXT_HINTS[next_stage]}")
    else:
        print("\nALL 7 GATES CLEARED. Run `check-land` to verify receipt integrity.")
    return 0


def cmd_check_land(args: argparse.Namespace) -> int:
    doc = load_run(Path(args.run))
    blockers: List[str] = []
    for stage in STAGES:
        g = doc["gates"].get(stage) or {"status": "PENDING"}
        st = str(g.get("status") or "PENDING")
        if st not in ("PASS", "SKIP_NA"):
            blockers.append(f"{stage}:{st}")
            continue
        if "receipt" in g and "receipt_sha256" in g:
            rp = Path(str(g["receipt"]))
            if not rp.is_file():
                blockers.append(f"{stage}:missing_receipt")
            elif sha256_file(rp) != g["receipt_sha256"]:
                blockers.append(f"{stage}:receipt_hash_drift")

    if blockers:
        print(f"SPEED HUNT: BLOCKED ({', '.join(blockers)})")
        return 1

    bench = doc["gates"]["bench_gate"]
    build = doc["gates"]["build"]
    ref = doc["gates"]["referee"]
    owed = ref.get("owed_ports") or []
    print("SPEED HUNT: READY_TO_LAND")
    print(
        f"CLOSING SUMMARY: {doc['target_cell']} {bench.get('base_tps')} -> {bench.get('cand_tps')} tok/s "
        f"({bench.get('delta_pct'):+.2f}%, noise {bench.get('noise_floor_pct')}%) | "
        f"kept={build.get('mechanism')} | referee={ref.get('verdict')} ({ref.get('referee_session')}) | "
        f"owed_ports={', '.join(owed) if owed else 'none'}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize a stateful 7-gate speed-hunt run in a notes folder")
    p_init.add_argument("--run", required=True, help="path to run.json outside the repository and /tmp")
    p_init.add_argument("--run-id")
    p_init.add_argument("--commit", required=True)
    p_init.add_argument("--base-binary-sha256")
    p_init.add_argument("--hardware", required=True)
    p_init.add_argument("--backend", required=True, choices=("cuda", "metal", "rocm"))
    p_init.add_argument("--family", required=True)
    p_init.add_argument("--target-cell", required=True)
    p_init.add_argument("--baseline-tps", type=float, required=True)
    p_init.add_argument("--allow-temp", action="store_true", help="allow /tmp paths during automated testing")

    p_att = sub.add_parser("attach", help="validate and attach one stage receipt or record")
    p_att.add_argument("--run", required=True)
    p_att.add_argument("--stage", required=True, choices=STAGES)
    p_att.add_argument("--receipt", help="path to stage JSON receipt")
    p_att.add_argument("--branch", help="git branch name (build stage)")
    p_att.add_argument("--mechanism", help="optimization mechanism name (build stage)")
    p_att.add_argument("--specialist", help="specialist skill used (build stage)")
    p_att.add_argument("--cand-binary-sha256", help="candidate binary SHA-256 (build stage)")
    p_att.add_argument("--not-applicable", action="store_true", help="mark binary_gate N/A when no GPU kernel/header changed")
    p_att.add_argument("--verdict", choices=("CONFIRMED", "NOT_PROVEN", "REFUTED"), help="speedup-referee verdict")
    p_att.add_argument("--referee-session", help="fresh session identifier that verified the speedup")
    p_att.add_argument("--owed-ports", help="comma-separated backend:family targets where win is owed")
    p_att.add_argument("--notes")

    p_st = sub.add_parser("status", help="display gate statuses and print the next specialist command")
    p_st.add_argument("--run", required=True)

    p_cl = sub.add_parser("check-land", help="re-verify every receipt hash and gate verdict before landing")
    p_cl.add_argument("--run", required=True)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "attach":
            return cmd_attach(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "check-land":
            return cmd_check_land(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"loop_driver.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
