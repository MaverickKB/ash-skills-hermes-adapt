#!/usr/bin/env python3
"""Audit multi-concurrency LLM serving bursts for throughput, TTFT knees, lockstep and exactness.

Reads a burst-sweep JSON receipt (`reference` + `bursts`) and verifies:
  1. Solo-vs-burst exactness on every stream (full 64-hex `token_sha256`).
  2. No identical-prompt lockstep contamination in concurrent (`level >= 2`) bursts.
  3. Exact aggregate burst decode rate: sum(output_tokens - 1) / (latest_last - earliest_first).
  4. TTFT p50/p95/p99 (and separate reasoning vs visible-answer TTFT when provided).
  5. Saturation knee (highest level scaling cleanly within --max-ttft-p99-s).

Exit codes: 0 VALID, 1 FAILED (exactness, lockstep, or SLA breach), 2 invalid receipt.
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


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * (pct / 100.0)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


def require_sha(obj: dict, key: str, label: str) -> str:
    val = obj.get(key)
    if not isinstance(val, str) or SHA256_RE.fullmatch(val) is None:
        raise ValueError(f"{label}: missing or invalid {key} (expected full 64-hex SHA-256)")
    return val.lower()


def audit_receipt(doc: object, max_ttft_p99: Optional[float]) -> Dict[str, object]:
    if not isinstance(doc, dict) or not isinstance(doc.get("reference"), dict) or not isinstance(doc.get("bursts"), list):
        raise ValueError("receipt must contain 'reference' object and 'bursts' list")
    ref = doc["reference"]
    if not ref:
        raise ValueError("'reference' mapping is empty")
    ref_hashes = {name: require_sha(row, "token_sha256", f"reference[{name}]") for name, row in ref.items()}

    if not doc["bursts"]:
        raise ValueError("'bursts' list is empty")

    by_level: Dict[int, List[dict]] = {}
    lane_caps: Dict[int, object] = {}
    exact_mismatches: List[str] = []
    lockstep_bursts: List[str] = []
    nonoverlapping_bursts: List[str] = []

    for burst in doc["bursts"]:
        level = int(burst["level"])
        rep = int(burst.get("rep", 0))
        streams = burst["streams"]
        if level < 1:
            raise ValueError("burst level must be positive")
        if len(streams) != level:
            raise ValueError(f"C{level} rep {rep + 1}: expected {level} streams, got {len(streams)}")
        lane_cap = burst.get("lane_cap")
        if lane_caps.setdefault(level, lane_cap) != lane_cap:
            raise ValueError(f"C{level} mixes lane caps; audit separate sweeps for different caps")

        prompt_ids: List[str] = []
        submit_times: List[float] = []
        first_times: List[float] = []
        last_times: List[float] = []
        ttfts: List[float] = []
        reasoning_ttfts: List[float] = []
        answer_ttfts: List[float] = []
        decode_tokens = 0
        per_stream_rates: List[float] = []

        for idx, st in enumerate(streams, 1):
            label = f"C{level} rep {rep + 1} stream {idx}"
            name = str(st["name"])
            if name not in ref_hashes:
                raise ValueError(f"{label}: fixture {name!r} missing from reference")
            got_sha = require_sha(st, "token_sha256", label)
            if got_sha != ref_hashes[name]:
                exact_mismatches.append(f"{label} ({name})")
            pid = str(st.get("prompt_sha256") or name)
            prompt_ids.append(pid)

            out_toks = int(st["output_tokens"])
            submit_s = float(st["submit_s"])
            first_s = float(st["first_token_s"])
            last_s = float(st["last_token_s"])
            if not all(math.isfinite(t) for t in (submit_s, first_s, last_s)):
                raise ValueError(f"{label}: request timestamps must be finite")
            if first_s < submit_s or last_s < first_s:
                raise ValueError(f"{label}: timestamps out of order (submit={submit_s}, first={first_s}, last={last_s})")

            ttfts.append(first_s - submit_s)
            if "reasoning_first_s" in st and st["reasoning_first_s"] is not None:
                reasoning_ttfts.append(float(st["reasoning_first_s"]) - submit_s)
            if "answer_first_s" in st and st["answer_first_s"] is not None:
                answer_ttfts.append(float(st["answer_first_s"]) - submit_s)

            submit_times.append(submit_s)
            first_times.append(first_s)
            last_times.append(last_s)
            stream_dec = max(out_toks - 1, 0)
            decode_tokens += stream_dec
            dur = last_s - first_s
            if dur > 0 and stream_dec > 0:
                per_stream_rates.append(stream_dec / dur)

        if level >= 2 and len(set(prompt_ids)) < level:
            lockstep_bursts.append(f"C{level} rep {rep + 1} ({len(set(prompt_ids))} unique prompts for {level} streams)")
        if level >= 2 and max(submit_times) >= min(last_times):
            nonoverlapping_bursts.append(
                f"C{level} rep {rep + 1}: latest submission {max(submit_times):g}s, earliest finish {min(last_times):g}s"
            )
            continue

        span = max(last_times) - min(first_times)
        agg_tps = (decode_tokens / span) if span > 0 else 0.0
        by_level.setdefault(level, []).append({
            "rep": rep,
            "agg_tps": agg_tps,
            "per_stream_tps": statistics.mean(per_stream_rates) if per_stream_rates else 0.0,
            "ttfts": ttfts,
            "reasoning_ttfts": reasoning_ttfts,
            "answer_ttfts": answer_ttfts,
        })

    levels_summary: List[Dict[str, object]] = []
    sla_breaches: List[int] = []
    best_level = None
    best_agg = -1.0

    for lvl in sorted(by_level):
        reps = by_level[lvl]
        all_ttft = [x for r in reps for x in r["ttfts"]]
        all_reas = [x for r in reps for x in r["reasoning_ttfts"]]
        all_ans = [x for r in reps for x in r["answer_ttfts"]]
        med_agg = statistics.median(r["agg_tps"] for r in reps)
        med_stream = statistics.median(r["per_stream_tps"] for r in reps)
        p50 = percentile(all_ttft, 50)
        p95 = percentile(all_ttft, 95)
        p99 = percentile(all_ttft, 99)

        if max_ttft_p99 is not None and p99 > max_ttft_p99:
            sla_breaches.append(lvl)
        elif med_agg > best_agg:
            best_agg = med_agg
            best_level = lvl

        entry: Dict[str, object] = {
            "level": lvl,
            "reps": len(reps),
            "lane_cap": lane_caps[lvl],
            "agg_decode_tps": round(med_agg, 2),
            "per_stream_tps": round(med_stream, 2),
            "ttft_p50_s": round(p50, 4),
            "ttft_p95_s": round(p95, 4),
            "ttft_p99_s": round(p99, 4),
        }
        if all_reas:
            entry["reasoning_ttft_p50_s"] = round(percentile(all_reas, 50), 4)
        if all_ans:
            entry["answer_ttft_p50_s"] = round(percentile(all_ans, 50), 4)
        levels_summary.append(entry)

    # Detect throughput knee: highest level before throughput drops or scales < 5% over previous level.
    knee = levels_summary[0]["level"] if levels_summary else None
    for i in range(1, len(levels_summary)):
        prev_tps = float(levels_summary[i - 1]["agg_decode_tps"])
        cur_tps = float(levels_summary[i]["agg_decode_tps"])
        cur_lvl = int(levels_summary[i]["level"])
        if cur_lvl in sla_breaches:
            break
        if prev_tps > 0 and cur_tps >= prev_tps * 1.05:
            knee = cur_lvl
        else:
            break

    reasons: List[str] = []
    if exact_mismatches:
        reasons.append(f"exactness mismatch on {len(exact_mismatches)} stream(s)")
    if lockstep_bursts:
        reasons.append(f"identical-prompt lockstep in {len(lockstep_bursts)} burst(s)")
    if nonoverlapping_bursts:
        reasons.append(f"claimed concurrency never reached in {len(nonoverlapping_bursts)} burst(s)")
    if sla_breaches:
        reasons.append(f"TTFT p99 exceeded {max_ttft_p99}s at C={sla_breaches}")

    return {
        "verdict": "VALID" if not reasons else "FAILED",
        "reasons": reasons,
        "saturation_knee": knee,
        "best_compliant_level": best_level,
        "exact_mismatches": exact_mismatches,
        "lockstep_bursts": lockstep_bursts,
        "nonoverlapping_bursts": nonoverlapping_bursts,
        "sla_breaches": sla_breaches,
        "levels": levels_summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    p_aud = sub.add_parser("audit", help="audit a burst-sweep JSON receipt")
    p_aud.add_argument("receipt", type=Path, help="burst sweep JSON file")
    p_aud.add_argument("--max-ttft-p99-s", type=float, help="fail if TTFT p99 exceeds this threshold (seconds)")
    p_aud.add_argument("--json", dest="json_out", help="write structured audit report to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "audit":
        parser.print_usage(sys.stderr)
        return 2
    try:
        doc = json.loads(args.receipt.read_text(encoding="utf-8"))
        res = audit_receipt(doc, args.max_ttft_p99_s)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"concurrency_audit.py: invalid receipt: {exc}", file=sys.stderr)
        return 2

    for row in res["levels"]:
        cap = f" cap={row['lane_cap']}" if row["lane_cap"] is not None else ""
        extra = ""
        if "answer_ttft_p50_s" in row:
            extra = f" ans_p50={row['answer_ttft_p50_s']:.3f}s"
        print(
            f"  C={row['level']:>2}{cap}  agg={row['agg_decode_tps']:>7.2f} tok/s  "
            f"per_stream={row['per_stream_tps']:>6.2f}  ttft p50={row['ttft_p50_s']:.3f}s p99={row['ttft_p99_s']:.3f}s{extra}"
        )
    for bad in res["exact_mismatches"][:5]:
        print(f"  EXACTNESS MISMATCH: {bad}")
    for lk in res["lockstep_bursts"][:5]:
        print(f"  LOCKSTEP CONTAMINATION: {lk}")
    for burst in res["nonoverlapping_bursts"][:5]:
        print(f"  INSUFFICIENT OVERLAP: {burst}")
    print(f"knee: C={res['saturation_knee']}" if res["saturation_knee"] is not None else "knee: unavailable")
    detail = "; ".join(res["reasons"]) if res["reasons"] else "exact, distinct prompts, within thresholds"
    print(f"CONCURRENCY: {res['verdict']} ({detail})")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(res, handle, indent=2)
    return 0 if res["verdict"] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
