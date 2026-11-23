#!/usr/bin/env python3
"""Plan and verify cold prefill equal-work scaling, KV pool budgets and state parity.

Subcommands:
  equal-work    Separate quadratic attention work from linear token count when
                comparing 1 long prompt against N short prompts of equal total tokens.
  kv-budget     Compute paged KV capacity, max resident concurrent contexts, and
                host/SSD spill requirements from model shape and memory budget.
  verify-state  Compare cold vs chunked vs resumed save-point state hashes
                (`kv_sha256`, optional window/recurrent/draft/logits/token hashes).

Exit codes: 0 clean/valid, 1 mismatch or insufficient VRAM, 2 usage or invalid input.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
OPTIONAL_HASH_FIELDS = ("window_sha256", "recurrent_sha256", "draft_sha256", "logits_sha256", "token_sha256")


def cmd_equal_work(args: argparse.Namespace) -> int:
    if args.long_len < 1 or args.short_len < 1 or args.streams < 1 or args.layers < 1 or args.heads < 1 or args.head_dim < 1:
        print("prefill_plan.py: error: all dimensions and lengths must be positive", file=sys.stderr)
        return 2

    long_tokens = args.long_len
    short_tokens = args.short_len * args.streams
    long_pairs = args.long_len * (args.long_len + 1) // 2
    short_pairs = args.streams * (args.short_len * (args.short_len + 1) // 2)
    attn_ratio = long_pairs / short_pairs if short_pairs > 0 else 0.0
    # 4 * layers * heads * head_dim FLOPs per causal (q, k) pair (QK^T + AV)
    flops_per_pair = 4 * args.layers * args.heads * args.head_dim
    long_attn_gflops = round((long_pairs * flops_per_pair) / 1e9, 3)
    short_attn_gflops = round((short_pairs * flops_per_pair) / 1e9, 3)

    report: Dict[str, object] = {
        "long_tokens": long_tokens,
        "short_total_tokens": short_tokens,
        "token_ratio": round(long_tokens / short_tokens, 4),
        "long_attn_pairs": long_pairs,
        "short_attn_pairs": short_pairs,
        "attn_work_ratio": round(attn_ratio, 3),
        "long_attn_gflops": long_attn_gflops,
        "short_attn_gflops": short_attn_gflops,
    }
    print(f"tokens: 1x{args.long_len} ({long_tokens}) vs {args.streams}x{args.short_len} ({short_tokens})")
    print(f"attention work: {long_attn_gflops} GFLOPs vs {short_attn_gflops} GFLOPs ({attn_ratio:.2f}x more attention work on the long prompt)")
    if args.long_s and args.short_s:
        if args.long_s <= 0 or args.short_s <= 0:
            print("prefill_plan.py: error: --long-s and --short-s must be positive", file=sys.stderr)
            return 2
        long_tps = long_tokens / args.long_s
        short_tps = short_tokens / args.short_s
        raw_share = long_tps / short_tps
        report["long_tps"] = round(long_tps, 2)
        report["short_tps"] = round(short_tps, 2)
        report["raw_share"] = round(raw_share, 4)
        report["long_attn_gflops_per_s"] = round(long_attn_gflops / args.long_s, 2)
        report["short_attn_gflops_per_s"] = round(short_attn_gflops / args.short_s, 2)
        print(
            f"measured: long {long_tps:.1f} tok/s vs multi {short_tps:.1f} tok/s (raw share {raw_share*100:.1f}%); "
            f"attn rate {report['long_attn_gflops_per_s']} vs {report['short_attn_gflops_per_s']} GFLOP/s"
        )
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 0


def cmd_kv_budget(args: argparse.Namespace) -> int:
    if args.layers < 1 or args.kv_bytes < 1 or args.context < 1 or args.page_tokens < 1 or args.streams < 1:
        print("prefill_plan.py: error: counts and sizes must be positive", file=sys.stderr)
        return 2
    if args.mla_rank > 0:
        bpt = args.layers * (args.mla_rank + args.rope_dim) * args.kv_bytes
        layout = "MLA"
    else:
        if args.kv_heads < 1 or args.head_dim < 1:
            print("prefill_plan.py: error: pass --kv-heads and --head-dim, or --mla-rank", file=sys.stderr)
            return 2
        bpt = args.layers * 2 * args.kv_heads * args.head_dim * args.kv_bytes
        layout = "GQA/MHA"

    if not all(math.isfinite(v) and v >= 0 for v in (args.vram_gib, args.weights_gib, args.overhead_gib)):
        print("prefill_plan.py: error: memory budgets must be finite and nonnegative", file=sys.stderr)
        return 2
    if args.mla_rank > 0 and args.rope_dim < 0:
        print("prefill_plan.py: error: --rope-dim must be nonnegative", file=sys.stderr)
        return 2
    usable_gib = args.vram_gib - args.weights_gib - args.overhead_gib
    if usable_gib <= 0:
        print(f"KV BUDGET: NO HEADROOM (usable {usable_gib:.3f} GiB after {args.weights_gib} GiB weights + {args.overhead_gib} GiB overhead)")
        return 1

    numerator, denominator = usable_gib.as_integer_ratio()
    usable_bytes = numerator * (1024**3) // denominator
    page_bytes = bpt * args.page_tokens
    max_pages = usable_bytes // page_bytes
    max_tokens = max_pages * args.page_tokens
    pages_per_ctx = (args.context + args.page_tokens - 1) // args.page_tokens
    ctx_gib = (pages_per_ctx * page_bytes) / (1024**3)
    max_resident_streams = max_pages // pages_per_ctx
    requested_pages = pages_per_ctx * args.streams
    needed_gib = requested_pages * page_bytes / (1024**3)
    spill_pages = max(0, requested_pages - max_pages)
    spill_bytes = spill_pages * page_bytes
    spill_gib = spill_bytes / (1024**3)
    if not math.isfinite(args.target_restore_s) or args.target_restore_s <= 0:
        print("prefill_plan.py: error: --target-restore-s must be finite and positive", file=sys.stderr)
        return 2
    spill_bw = spill_gib / args.target_restore_s

    report = {
        "layout": layout,
        "kv_bytes_per_token": bpt,
        "page_tokens": args.page_tokens,
        "page_bytes": page_bytes,
        "page_kib": round(page_bytes / 1024.0, 2),
        "usable_kv_gib": round(usable_gib, 3),
        "max_resident_pages": max_pages,
        "max_resident_tokens": max_tokens,
        "context_tokens": args.context,
        "gib_per_context": round(ctx_gib, 3),
        "max_resident_streams": max_resident_streams,
        "requested_streams": args.streams,
        "requested_pages": requested_pages,
        "spill_pages": spill_pages,
        "spill_bytes": spill_bytes,
        "requested_kv_gib": round(needed_gib, 3),
        "spill_gib": spill_gib,
        "spill_bw_gib_per_s": spill_bw,
        "fits_in_vram": spill_pages == 0,
    }
    print(f"layout: {layout}, {bpt:,} bytes/token, page={report['page_kib']} KiB ({args.page_tokens} tok/page)")
    print(f"headroom: {usable_gib:.3f} GiB -> {max_pages:,} pages ({max_tokens:,} tokens), max {max_resident_streams} resident stream(s) at ctx={args.context}")
    if spill_gib > 0:
        print(f"KV BUDGET: SPILL NEEDED ({needed_gib:.3f} GiB for {args.streams} streams; {spill_pages} pages to spill, {spill_gib:.3f} GiB, {spill_bw:.2f} GiB/s to restore in {args.target_restore_s}s)")
    else:
        print(f"KV BUDGET: FITS ({needed_gib:.3f} / {usable_gib:.3f} GiB for {args.streams} streams)")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 0


def require_sha(row: dict, key: str, label: str) -> str:
    val = row.get(key)
    if not isinstance(val, str) or SHA256_RE.fullmatch(val) is None:
        raise ValueError(f"{label}: missing or invalid {key} (expected full 64-hex SHA-256)")
    return val.lower()


def cmd_verify_state(args: argparse.Namespace) -> int:
    try:
        ctrl = json.loads(Path(args.control).read_text(encoding="utf-8"))
        cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        if not isinstance(ctrl, dict) or not isinstance(cand, dict) or not ctrl:
            raise ValueError("expected non-empty JSON mappings of save_point -> receipt")
        if ctrl.keys() != cand.keys():
            raise ValueError(f"save-point keys differ: {sorted(set(ctrl) ^ set(cand))}")
        mismatches: Dict[str, List[str]] = {}
        for mark in sorted(ctrl):
            left, right = ctrl[mark], cand[mark]
            if not isinstance(left, dict) or not isinstance(right, dict):
                raise ValueError(f"{mark}: expected object")
            diffs: List[str] = []
            if require_sha(left, "kv_sha256", mark) != require_sha(right, "kv_sha256", mark):
                diffs.append("kv_sha256")
            for field in OPTIONAL_HASH_FIELDS:
                if field in left or field in right:
                    if require_sha(left, field, mark) != require_sha(right, field, mark):
                        diffs.append(field)
            if "restored_tokens" in left and "restored_tokens" in right and left["restored_tokens"] != right["restored_tokens"]:
                diffs.append("restored_tokens")
            if diffs:
                mismatches[mark] = diffs
    except (OSError, ValueError, KeyError) as exc:
        print(f"prefill_plan.py: invalid state receipt: {exc}", file=sys.stderr)
        return 2

    res = {
        "compared": len(ctrl),
        "matched": len(ctrl) - len(mismatches),
        "mismatches": mismatches,
        "verdict": "MATCH" if not mismatches else "MISMATCH",
    }
    print(json.dumps(res, indent=2))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(res, handle, indent=2)
    return 0 if not mismatches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_eq = sub.add_parser("equal-work", help="separate quadratic attention work in 1-long vs N-short prefill")
    p_eq.add_argument("--long-len", type=int, required=True)
    p_eq.add_argument("--short-len", type=int, required=True)
    p_eq.add_argument("--streams", type=int, required=True)
    p_eq.add_argument("--layers", type=int, required=True)
    p_eq.add_argument("--heads", type=int, required=True)
    p_eq.add_argument("--head-dim", type=int, required=True)
    p_eq.add_argument("--long-s", type=float)
    p_eq.add_argument("--short-s", type=float)
    p_eq.add_argument("--json", dest="json_out")

    p_kv = sub.add_parser("kv-budget", help="compute paged KV capacity and spill requirements")
    p_kv.add_argument("--layers", type=int, required=True)
    p_kv.add_argument("--kv-heads", type=int, default=0)
    p_kv.add_argument("--head-dim", type=int, default=0)
    p_kv.add_argument("--mla-rank", type=int, default=0)
    p_kv.add_argument("--rope-dim", type=int, default=0)
    p_kv.add_argument("--kv-bytes", type=int, default=2)
    p_kv.add_argument("--vram-gib", type=float, required=True)
    p_kv.add_argument("--weights-gib", type=float, required=True)
    p_kv.add_argument("--overhead-gib", type=float, default=2.0)
    p_kv.add_argument("--page-tokens", type=int, default=16)
    p_kv.add_argument("--context", type=int, required=True)
    p_kv.add_argument("--streams", type=int, default=1)
    p_kv.add_argument("--target-restore-s", type=float, default=1.0)
    p_kv.add_argument("--json", dest="json_out")

    p_vs = sub.add_parser("verify-state", help="compare serialized KV/window/recurrent/draft/logits save-point hashes")
    p_vs.add_argument("control")
    p_vs.add_argument("candidate")
    p_vs.add_argument("--json", dest="json_out")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "equal-work":
        return cmd_equal_work(args)
    if args.command == "kv-budget":
        return cmd_kv_budget(args)
    if args.command == "verify-state":
        return cmd_verify_state(args)
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
