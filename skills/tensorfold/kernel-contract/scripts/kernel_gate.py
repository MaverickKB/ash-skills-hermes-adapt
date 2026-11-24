#!/usr/bin/env python3
"""Verify native kernel outputs against a reference with IEEE-754 ULP and planted-fault controls.

Subcommands:
  compare   Compare reference vs candidate JSON or raw binary tensors under
            --bit-exact or explicit --max-ulp / --atol / --rtol budgets, then
            run an automatic planted-fault control to prove the gate can fail.
  shapes    Print the canonical inference kernel hazard shape matrix (M=1 decode,
            draft windows, prime/odd tails, unaligned K/N, wrapped KV ring offsets).

Exit codes: 0 KERNEL PASS (control failed as expected), 1 KERNEL FAIL or weak control, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DTYPE_FMT = {"f16": "e", "f32": "f", "f64": "d", "i32": "i"}
I32_MIN = -(2**31)
I32_MAX = 2**31 - 1
FLOAT_INT_FMT = {"f16": ("<e", "<H", 16), "f32": ("<f", "<I", 32), "f64": ("<d", "<Q", 64)}

HAZARD_SHAPES = {
    "decode_and_draft_M": [1, 2, 3, 4, 7, 8, 13, 16, 31, 32, 65],
    "prefill_M": [1, 17, 64, 127, 128, 257, 512, 1025],
    "unaligned_K_or_N": [1, 3, 15, 31, 33, 63, 127, 129, 255, 513],
    "kv_ring_cases": ["empty(0)", "single(1)", "exact_page_boundary", "wrapped_head_gt_tail", "full_minus_1"],
    "numeric_hazards": ["all_zeros", "signed_zero(-0.0)", "subnormals", "large_magnitude", "ties_in_argmax", "NaN_and_Inf_guard"],
}


def float_to_ordered_int(val: float, dtype: str) -> int:
    pf, uf, bits = FLOAT_INT_FMT[dtype]
    raw = struct.unpack(uf, struct.pack(pf, val))[0]
    sign_mask = 1 << (bits - 1)
    if raw & sign_mask:
        return (~raw) & ((1 << bits) - 1)
    return raw + sign_mask


def ulp_distance(a: float, b: float, dtype: str) -> int:
    return abs(float_to_ordered_int(a, dtype) - float_to_ordered_int(b, dtype))


def bump_one_ulp(val: float, dtype: str, steps: int = 1) -> float:
    pf, uf, bits = FLOAT_INT_FMT[dtype]
    raw = struct.unpack(uf, struct.pack(pf, val))[0]
    mask = (1 << bits) - 1
    if math.isnan(val) or math.isinf(val):
        return 0.0
    bumped = (raw + steps) & mask
    candidate = struct.unpack(pf, struct.pack(uf, bumped))[0]
    if math.isnan(candidate) or candidate == val:
        bumped = (raw ^ 1) & mask
        candidate = struct.unpack(pf, struct.pack(uf, bumped))[0]
    return candidate


@dataclass
class Tensor:
    dtype: str
    shape: List[int]
    values: Sequence[float]
    raw: Optional[bytes] = None

    def __post_init__(self) -> None:
        if not isinstance(self.shape, list) or any(type(dim) is not int or dim < 0 for dim in self.shape):
            raise ValueError("shape must be an array of nonnegative integers")
        count = math.prod(self.shape)
        if count != len(self.values):
            raise ValueError(f"shape {self.shape} requires {count} values, received {len(self.values)}")

    def float_bits(self, index: int) -> int:
        float_fmt, word_fmt, width = FLOAT_INT_FMT[self.dtype]
        if self.raw is not None:
            return struct.unpack_from(word_fmt, self.raw, index * (width // 8))[0]
        return struct.unpack(word_fmt, struct.pack(float_fmt, self.values[index]))[0]

    def with_value(self, index: int, value: float) -> Tensor:
        values = list(self.values)
        values[index] = value
        raw = None
        if self.raw is not None and self.dtype in FLOAT_INT_FMT:
            float_fmt, _, width = FLOAT_INT_FMT[self.dtype]
            offset = index * (width // 8)
            raw = self.raw[:offset] + struct.pack(float_fmt, value) + self.raw[offset + width // 8:]
        return Tensor(self.dtype, self.shape, values, raw)


def parse_i32_values(values: Sequence[object]) -> List[int]:
    integers = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"values[{index}] must be a signed 32-bit integer")
        if not I32_MIN <= value <= I32_MAX or (isinstance(value, float) and not value.is_integer()):
            raise ValueError(f"values[{index}] must be a signed 32-bit integer")
        integers.append(int(value))
    return integers


def load_tensor(json_path: Optional[str], bin_path: Optional[str], dtype_arg: Optional[str]) -> Tensor:
    if json_path:
        doc = json.loads(Path(json_path).read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("values"), list):
            raise ValueError(f"{json_path}: expected a tensor object with a flat values array")
        dtype = str(doc.get("dtype") or dtype_arg or "f32")
        if dtype not in DTYPE_FMT:
            raise ValueError(f"unsupported dtype {dtype!r}")
        values = doc["values"]
        shape = doc.get("shape", [len(values)])
        try:
            parsed = parse_i32_values(values) if dtype == "i32" else [float(value) for value in values]
            return Tensor(dtype, shape, parsed)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{json_path}: {exc}") from exc
    if bin_path:
        dtype = dtype_arg or "f32"
        if dtype not in DTYPE_FMT:
            raise ValueError(f"unsupported dtype {dtype!r}")
        raw = Path(bin_path).read_bytes()
        fmt = "<" + DTYPE_FMT[dtype]
        item_size = struct.calcsize(fmt)
        if len(raw) % item_size != 0:
            raise ValueError(f"{bin_path} byte length {len(raw)} not divisible by {dtype} size {item_size}")
        count = len(raw) // item_size
        vals = list(struct.unpack(f"<{count}{DTYPE_FMT[dtype]}", raw))
        return Tensor(dtype, [count], vals, raw)
    raise ValueError("pass either JSON or binary tensor inputs")


def evaluate_tensors(
    reference: Tensor,
    candidate: Tensor,
    bit_exact: bool,
    max_ulp: Optional[int],
    atol: float,
    rtol: float,
) -> Dict[str, object]:
    dtype = reference.dtype
    if reference.shape != candidate.shape or len(reference.values) != len(candidate.values):
        return {"passed": False, "reason": f"shape mismatch: ref {reference.shape} vs cand {candidate.shape}"}
    if not reference.values:
        return {"passed": False, "reason": "empty tensor cannot verify kernel behavior"}

    is_float = dtype in FLOAT_INT_FMT
    worst_abs = 0.0
    worst_rel = 0.0
    worst_ulp = 0
    bit_mismatches = 0
    first_bad: Optional[Dict[str, object]] = None

    for idx, (r, c) in enumerate(zip(reference.values, candidate.values)):
        if is_float:
            r_nan, c_nan = math.isnan(r), math.isnan(c)
            if r_nan or c_nan:
                r_bits, c_bits = reference.float_bits(idx), candidate.float_bits(idx)
                if r_bits != c_bits:
                    bit_mismatches += 1
                    if bit_exact or not (r_nan and c_nan):
                        first_bad = first_bad or {"index": idx, "ref": str(r), "cand": str(c),
                                                  "ref_bits": hex(r_bits), "cand_bits": hex(c_bits),
                                                  "reason": "NaN mismatch"}
                continue
            r_inf, c_inf = math.isinf(r), math.isinf(c)
            if r_inf or c_inf:
                if r != c:
                    bit_mismatches += 1
                    first_bad = first_bad or {"index": idx, "ref": str(r), "cand": str(c), "reason": "Inf mismatch"}
                continue
            r_bits, c_bits = reference.float_bits(idx), candidate.float_bits(idx)
            if r_bits != c_bits:
                bit_mismatches += 1
            diff = abs(r - c)
            rel = diff / max(abs(r), 1e-30)
            ulp = ulp_distance(r, c, dtype)
            worst_abs = max(worst_abs, diff)
            worst_rel = max(worst_rel, rel)
            worst_ulp = max(worst_ulp, ulp)

            if bit_exact and r_bits != c_bits:
                first_bad = first_bad or {"index": idx, "ref": r, "cand": c, "ulp": ulp, "reason": "bit mismatch"}
            elif not bit_exact:
                ulp_bad = max_ulp is not None and ulp > max_ulp
                tol_bad = diff > (atol + rtol * abs(r))
                if ulp_bad or (max_ulp is None and tol_bad) or (max_ulp is not None and ulp_bad and tol_bad):
                    first_bad = first_bad or {"index": idx, "ref": r, "cand": c, "abs": diff, "ulp": ulp, "reason": "exceeded budget"}
        else:
            if r != c:
                bit_mismatches += 1
                diff = abs(int(r) - int(c))
                worst_abs = max(worst_abs, float(diff))
                first_bad = first_bad or {"index": idx, "ref": int(r), "cand": int(c), "reason": "integer mismatch"}

    passed = first_bad is None
    return {
        "passed": passed,
        "elements": len(reference.values),
        "dtype": dtype,
        "bit_mismatches": bit_mismatches,
        "max_abs": worst_abs,
        "max_rel": worst_rel,
        "max_ulp": worst_ulp,
        "first_mismatch": first_bad,
        "reason": "within contract" if passed else str(first_bad),
    }


def planted_fault_control(
    reference: Tensor,
    bit_exact: bool,
    max_ulp: Optional[int],
    atol: float,
    rtol: float,
) -> Tuple[bool, str]:
    """Perturb one element past the allowed budget and confirm the gate rejects it."""
    dtype = reference.dtype
    target_idx = next((i for i, v in enumerate(reference.values) if math.isfinite(v)), 0)
    orig = reference.values[target_idx]
    if dtype in FLOAT_INT_FMT:
        if bit_exact:
            fault = bump_one_ulp(orig, dtype, 1)
        elif max_ulp is not None:
            fault = bump_one_ulp(orig, dtype, max_ulp + 2)
        else:
            budget = atol + abs(orig) * rtol
            step = max(budget * 2.0, 1e-5)
            candidate_val = orig + step
            if math.isinf(candidate_val) or math.isnan(candidate_val):
                return False, f"budget ({budget}) overflows float range; tolerance is uncheckably loose"
            fault = candidate_val
    else:
        integer = int(orig)
        fault = integer - 1 if integer == I32_MAX else integer + 1
    perturbed = reference.with_value(target_idx, fault)
    check = evaluate_tensors(reference, perturbed, bit_exact, max_ulp, atol, rtol)
    if check["passed"]:
        return False, f"planted fault at index {target_idx} ({orig} -> {fault}) still passed; budget is too loose"
    return True, f"planted fault at index {target_idx} ({orig} -> {fault}) rejected"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_cmp = sub.add_parser("compare", help="compare reference and candidate tensors with planted-fault control")
    p_cmp.add_argument("--ref", help="reference JSON tensor file")
    p_cmp.add_argument("--cand", help="candidate JSON tensor file")
    p_cmp.add_argument("--ref-bin", help="reference raw little-endian binary file")
    p_cmp.add_argument("--cand-bin", help="candidate raw little-endian binary file")
    p_cmp.add_argument("--dtype", choices=sorted(DTYPE_FMT), help="tensor dtype (required for raw binary)")
    p_cmp.add_argument("--bit-exact", action="store_true", help="require exact bit identity (0 ULP)")
    p_cmp.add_argument("--max-ulp", type=int, help="maximum allowed IEEE-754 ULP distance")
    p_cmp.add_argument("--atol", type=float, default=1e-5, help="absolute tolerance (default: 1e-5)")
    p_cmp.add_argument("--rtol", type=float, default=1e-5, help="relative tolerance (default: 1e-5)")
    p_cmp.add_argument("--json", dest="json_out")

    sub.add_parser("shapes", help="print canonical inference kernel hazard shape matrix")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "shapes":
        for k, vals in HAZARD_SHAPES.items():
            print(f"{k}: {', '.join(str(v) for v in vals)}")
        return 0

    if args.command == "compare":
        try:
            reference = load_tensor(args.ref, args.ref_bin, args.dtype)
            candidate = load_tensor(args.cand, args.cand_bin, args.dtype)
        except (OSError, ValueError, KeyError) as exc:
            print(f"kernel_gate.py: error: {exc}", file=sys.stderr)
            return 2
        if reference.dtype != candidate.dtype:
            print(f"kernel_gate.py: error: dtype mismatch ({reference.dtype} vs {candidate.dtype})", file=sys.stderr)
            return 2

        res = evaluate_tensors(reference, candidate, args.bit_exact, args.max_ulp, args.atol, args.rtol)
        ctrl_ok, ctrl_detail = (planted_fault_control(reference, args.bit_exact, args.max_ulp, args.atol, args.rtol)
                                if reference.values else (False, "empty tensor"))
        res["control_passed"] = ctrl_ok
        res["control_detail"] = ctrl_detail

        print(
            f"tensor: dtype={reference.dtype} shape={reference.shape} bit_mismatches={res.get('bit_mismatches', '-')} "
            f"max_ulp={res.get('max_ulp', '-')} max_abs={res.get('max_abs', '-')}"
        )
        print(f"control: {'ok' if ctrl_ok else 'FAIL'} ({ctrl_detail})")
        if not ctrl_ok:
            verdict = "CONTROL FAILED"
            code = 1
        elif res["passed"]:
            verdict = "PASS"
            code = 0
        else:
            verdict = f"FAIL ({res['reason']})"
            code = 1
        res["verdict"] = verdict
        print(f"KERNEL: {verdict}")
        if args.json_out:
            json.dump(res, open(args.json_out, "w", encoding="utf-8"), indent=2)
        return code

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
