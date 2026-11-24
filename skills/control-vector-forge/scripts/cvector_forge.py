#!/usr/bin/env python3
"""Build, lint, inspect and gate native GGUF control vectors for local LLMs.

Provides balanced contrastive presets (including Mythos engineering rigor and
Slopchop anti-slop packs), a dataset bias/leakage linter, layer-band planners
for `llama-cvector-generator`, a pure-stdlib binary `.gguf` control-vector
inspector, and a perplexity + task preservation sweep gate.

Subcommands:
  presets       List or export contrastive datasets (positive.txt, negative.txt,
                pairs.json) for llama-cvector-generator or repeng workflows.
  lint-pairs    Audit contrastive pairs for length bias, count mismatch,
                duplicates, empty entries and topic leakage.
  layer-plan    Compute active layer bands and a control-verified scale sweep
                grid (`0.0` identity + `-0.5` inverted negative control).
  inspect-gguf  Parse a compiled `.gguf` control vector binary and check hidden
                dims, NaN/Inf/dead layers, L2 norm spikes and cosine stability.
  score-sweep   Enforce perplexity and task-retention gates across a recorded
                sweep JSON and emit the winning `--control-vector-scaled` flags.

Exit codes: 0 clean/winner, 1 lint/health/gate failure, 2 usage or parse error.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

PRESETS_PATH = Path(__file__).resolve().parents[1] / "assets" / "presets.json"
SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
STOPWORDS = set(
    """the and for that with this from have been into your they them their when what where which while will would
    should could about after before each every more most must need other over same some such than then there these
    those through under very were without any all are but can has its not one our out was who why you into onto
    just only also even make made keep give take tell say says said work works working code file line run runs""".split()
)
LAYER_DIR_RE = re.compile(r"direction\.(\d+)\Z")


class Fail(Exception):
    """Usage, file or schema error -> exit 2."""


def load_presets() -> Dict[str, object]:
    if not PRESETS_PATH.is_file():
        raise Fail(f"presets asset missing: {PRESETS_PATH}")
    return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))


# -------------------------------------------------------------------- presets

def cmd_presets(args: argparse.Namespace) -> int:
    data = load_presets()
    presets = data["presets"]
    if args.list_all or not args.export:
        print(f"available presets ({len(presets)}):")
        for name, info in presets.items():
            print(f"  {name:<20} ({len(info['pairs']):>2} pairs, type={info['behavior_type']:<9}) {info['description']}")
        if not args.export:
            return 0

    name = args.export
    if name not in presets:
        raise Fail(f"unknown preset {name!r}; choose from {', '.join(sorted(presets))}")
    if not args.out_dir:
        raise Fail("--out-dir is required with --export")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    info = presets[name]
    pairs = info["pairs"]
    pos_lines = [p["pos"].strip() for p in pairs]
    neg_lines = [p["neg"].strip() for p in pairs]
    stems = data.get("neutral_stems") or []

    (out_dir / "positive.txt").write_text("\n".join(pos_lines) + "\n", encoding="utf-8")
    (out_dir / "negative.txt").write_text("\n".join(neg_lines) + "\n", encoding="utf-8")
    (out_dir / "prompts_neutral.txt").write_text("\n".join(stems) + "\n", encoding="utf-8")
    (out_dir / "pairs.json").write_text(
        json.dumps({"preset": name, "behavior_type": info["behavior_type"], "pairs": pairs}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"exported preset '{name}' ({len(pairs)} pairs) to {out_dir}")
    print(f"  positive.txt, negative.txt, pairs.json, prompts_neutral.txt")
    return 0


# ----------------------------------------------------------------- lint-pairs

def load_pairs(args: argparse.Namespace) -> Tuple[List[str], List[str]]:
    if args.pairs_json:
        p = Path(args.pairs_json)
        if not p.is_file():
            raise Fail(f"file not found: {p}")
        doc = json.loads(p.read_text(encoding="utf-8"))
        rows = doc.get("pairs") if isinstance(doc, dict) else doc
        if not isinstance(rows, list) or not rows:
            raise Fail(f"{p}: expected non-empty list of {{pos, neg}} objects")
        pos = [str(r.get("pos", "")).strip() for r in rows]
        neg = [str(r.get("neg", "")).strip() for r in rows]
        return pos, neg
    if args.pos and args.neg:
        p_pos, p_neg = Path(args.pos), Path(args.neg)
        if not p_pos.is_file() or not p_neg.is_file():
            raise Fail("positive or negative file not found")
        pos = [line.strip() for line in p_pos.read_text(encoding="utf-8").splitlines() if line.strip() or True]
        neg = [line.strip() for line in p_neg.read_text(encoding="utf-8").splitlines() if line.strip() or True]
        # Strip trailing blank line only.
        while pos and pos[-1] == "":
            pos.pop()
        while neg and neg[-1] == "":
            neg.pop()
        return pos, neg
    raise Fail("pass --pairs-json FILE or both --pos FILE and --neg FILE")


def lint_contrastive_pairs(pos: Sequence[str], neg: Sequence[str], max_length_skew: float = 0.25) -> Dict[str, object]:
    findings: List[Dict[str, str]] = []

    def add(sev: str, code: str, msg: str) -> None:
        findings.append({"severity": sev, "code": code, "message": msg})

    if len(pos) != len(neg):
        add("ERROR", "COUNT_MISMATCH", f"positive has {len(pos)} rows while negative has {len(neg)} rows; pairs must align 1:1")
    n = min(len(pos), len(neg))
    if n < 10:
        add("ERROR", "TOO_FEW_PAIRS", f"only {n} pair(s); need at least 10 for smoke extraction and 48+ for clean PCA")
    elif n < 48:
        add("NOTE", "SMALL_DATASET", f"{n} pairs is enough for smoke runs; expand to 48-256 pairs for production stability")

    empty_pos = [i + 1 for i, s in enumerate(pos) if not s.strip()]
    empty_neg = [i + 1 for i, s in enumerate(neg) if not s.strip()]
    if empty_pos or empty_neg:
        add("ERROR", "EMPTY_ENTRY", f"blank entries found (pos lines {empty_pos[:5]}, neg lines {empty_neg[:5]})")

    same_pairs = [i + 1 for i in range(n) if pos[i].strip().lower() == neg[i].strip().lower()]
    if same_pairs:
        add("ERROR", "IDENTICAL_PAIR", f"positive and negative are identical on pair(s) {same_pairs[:5]}")

    pos_dups = len(pos) - len({s.strip().lower() for s in pos if s.strip()})
    neg_dups = len(neg) - len({s.strip().lower() for s in neg if s.strip()})
    if pos_dups or neg_dups:
        add("ERROR", "DUPLICATES", f"duplicate prompts distort PCA variance (pos dups={pos_dups}, neg dups={neg_dups})")

    pos_lens = [len(s) for s in pos if s.strip()]
    neg_lens = [len(s) for s in neg if s.strip()]
    mean_pos = statistics.mean(pos_lens) if pos_lens else 0.0
    mean_neg = statistics.mean(neg_lens) if neg_lens else 0.0
    ratio = (mean_pos / mean_neg) if mean_neg > 0 else 0.0
    if ratio > 0 and abs(ratio - 1.0) > max_length_skew:
        add(
            "ERROR",
            "LENGTH_BIAS",
            f"mean char length ratio pos/neg is {ratio:.2f} ({mean_pos:.1f} vs {mean_neg:.1f}); "
            "unbalanced pairs extract a verbosity/length vector instead of the target behavior",
        )

    # Check if one side overwhelmingly concentrates on one domain word.
    def tok_counts(items: Sequence[str]) -> Counter:
        c: Counter = Counter()
        for text in items:
            words = set(re.findall(r"[a-z]{4,}", text.lower())) - STOPWORDS
            for w in words:
                c[w] += 1
        return c

    c_pos, c_neg = tok_counts(pos), tok_counts(neg)
    skewed = []
    min_occ = max(4, int(n * 0.4))
    for w in set(c_pos) | set(c_neg):
        a, b = c_pos[w], c_neg[w]
        if max(a, b) >= min_occ and min(a, b) == 0:
            skewed.append(f"{w} ({a} vs {b})")
    if len(skewed) >= 4:
        add(
            "WARN",
            "TOPIC_LEAKAGE",
            f"one-sided high-frequency words detected ({', '.join(sorted(skewed)[:6])}); verify they reflect target behavior rather than an unrelated topic",
        )

    errors = sum(1 for f in findings if f["severity"] == "ERROR")
    warns = sum(1 for f in findings if f["severity"] == "WARN")
    return {
        "pairs": n,
        "mean_pos_chars": round(mean_pos, 1),
        "mean_neg_chars": round(mean_neg, 1),
        "length_ratio": round(ratio, 3),
        "errors": errors,
        "warnings": warns,
        "clean": errors == 0 and warns == 0,
        "findings": findings,
    }


def cmd_lint_pairs(args: argparse.Namespace) -> int:
    pos, neg = load_pairs(args)
    res = lint_contrastive_pairs(pos, neg, max_length_skew=args.max_length_skew)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(f"pairs: {res['pairs']} | mean chars pos={res['mean_pos_chars']} neg={res['mean_neg_chars']} (ratio={res['length_ratio']})")
    for f in res["findings"]:
        print(f"  [{f['severity']}] {f['code']}: {f['message']}")
    if res["clean"]:
        print("PAIRS: CLEAN")
        return 0
    print(f"PAIRS: ISSUES ({res['errors']} errors, {res['warnings']} warnings)")
    return 1


# ------------------------------------------------------------------ layer-plan

BEHAVIOR_BANDS: Dict[str, Tuple[float, float]] = {
    "style": (0.38, 0.82),
    "coding": (0.32, 0.78),
    "reasoning": (0.28, 0.72),
}


def cmd_layer_plan(args: argparse.Namespace) -> int:
    if args.layers < 6:
        raise Fail("--layers must be >= 6")
    lo_f, hi_f = BEHAVIOR_BANDS[args.behavior]
    L = args.layers
    default_lo = max(1, int(round(L * lo_f)))
    default_hi = min(L - 2, max(default_lo + 1, int(round(L * hi_f))))
    tight_lo = min(default_hi - 1, default_lo + max(1, L // 12))
    tight_hi = max(tight_lo + 1, default_hi - max(1, L // 12))
    broad_lo = max(1, default_lo - max(1, L // 12))
    broad_hi = min(L - 2, default_hi + max(1, L // 12))

    bands = [
        {"name": "tight_core", "lo": tight_lo, "hi": tight_hi},
        {"name": "balanced_default", "lo": default_lo, "hi": default_hi},
        {"name": "broad_upper", "lo": broad_lo, "hi": broad_hi},
    ]
    scales = [0.0, -0.5, 0.25, 0.5, 0.75, 1.0, 1.25]
    gen_cmd = (
        f"llama-cvector-generator -m {args.model_gguf} "
        f"--positive-file {args.pos_file} --negative-file {args.neg_file} "
        f"--pca-iter 1000 --pca-batch 100 -o {args.out_gguf}"
    )
    serve_cmd = (
        f"llama-server -m {args.model_gguf} "
        f"--control-vector-scaled {args.out_gguf} 0.50 "
        f"--control-vector-layer-range {default_lo} {default_hi}"
    )
    plan = {
        "layers": L,
        "behavior": args.behavior,
        "bands": bands,
        "scales": scales,
        "controls": {"identity_scale": 0.0, "inverted_negative_scale": -0.5},
        "generator_cmd": gen_cmd,
        "example_serve_cmd": serve_cmd,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

    print(f"layer plan: total_layers={L} behavior={args.behavior}")
    for b in bands:
        print(f"  {b['name']:<18} layers {b['lo']}..{b['hi']} (skip 0..{b['lo']-1} early, {b['hi']+1}..{L-1} late)")
    print(f"sweep scales: {scales} (0.0 = identity baseline, -0.5 = inverted negative control)")
    print(f"generate: {gen_cmd}")
    print(f"serve:    {serve_cmd}")
    return 0


# -------------------------------------------------------- binary GGUF inspector

def _skip_gguf_val(fh, vtype: int) -> None:
    if vtype in SCALAR_SIZES:
        fh.seek(SCALAR_SIZES[vtype], os.SEEK_CUR)
        return
    if vtype == 8:
        n = struct.unpack("<Q", fh.read(8))[0]
        fh.seek(n, os.SEEK_CUR)
        return
    if vtype == 9:
        et = struct.unpack("<I", fh.read(4))[0]
        cnt = struct.unpack("<Q", fh.read(8))[0]
        if et in SCALAR_SIZES:
            fh.seek(cnt * SCALAR_SIZES[et], os.SEEK_CUR)
        else:
            for _ in range(cnt):
                _skip_gguf_val(fh, et)
        return
    raise Fail(f"unsupported GGUF metadata type {vtype}")


def inspect_cvector_gguf(path: Path) -> Dict[str, object]:
    if not path.is_file():
        raise Fail(f"file not found: {path}")
    with path.open("rb") as fh:
        if fh.read(4) != b"GGUF":
            raise Fail(f"{path}: not a GGUF file")
        ver = struct.unpack("<I", fh.read(4))[0]
        if ver not in (2, 3):
            raise Fail(f"{path}: unsupported GGUF version {ver}")
        tensor_count = struct.unpack("<Q", fh.read(8))[0]
        kv_count = struct.unpack("<Q", fh.read(8))[0]
        alignment = 32
        arch = "unknown"

        for _ in range(kv_count):
            klen = struct.unpack("<Q", fh.read(8))[0]
            key = fh.read(klen).decode("utf-8", errors="replace")
            vtype = struct.unpack("<I", fh.read(4))[0]
            if key == "general.alignment" and vtype == 4:
                alignment = struct.unpack("<I", fh.read(4))[0]
            elif key == "general.architecture" and vtype == 8:
                vlen = struct.unpack("<Q", fh.read(8))[0]
                arch = fh.read(vlen).decode("utf-8", errors="replace")
            else:
                _skip_gguf_val(fh, vtype)

        tensors = []
        for _ in range(tensor_count):
            nlen = struct.unpack("<Q", fh.read(8))[0]
            tname = fh.read(nlen).decode("utf-8", errors="replace")
            ndims = struct.unpack("<I", fh.read(4))[0]
            dims = [struct.unpack("<Q", fh.read(8))[0] for _ in range(ndims)]
            dtype_id = struct.unpack("<I", fh.read(4))[0]
            offset = struct.unpack("<Q", fh.read(8))[0]
            tensors.append((tname, dims, dtype_id, offset))

        data_start = ((fh.tell() + alignment - 1) // alignment) * alignment
        file_size = os.fstat(fh.fileno()).st_size

        layer_vecs: Dict[int, List[float]] = {}
        layer_stats: List[Dict[str, object]] = []
        issues: List[str] = []
        hidden_dims = set()

        for tname, dims, dtype_id, offset in tensors:
            m = LAYER_DIR_RE.match(tname)
            if not m:
                continue
            layer_idx = int(m.group(1))
            elems = 1
            for d in dims:
                elems *= d
            hidden_dims.add(elems)
            if dtype_id == 0:
                fmt, bpe, dlabel = f"<{elems}f", 4, "F32"
            elif dtype_id == 1:
                fmt, bpe, dlabel = f"<{elems}e", 2, "F16"
            else:
                issues.append(f"{tname}: unsupported direction dtype id {dtype_id} (expected F32 or F16)")
                continue
            byte_start = data_start + offset
            byte_len = elems * bpe
            if byte_start + byte_len > file_size:
                raise Fail(f"{path}: truncated tensor data for {tname}")
            fh.seek(byte_start)
            vals = list(struct.unpack(fmt, fh.read(byte_len)))
            nonfinite = sum(1 for v in vals if not math.isfinite(v))
            l2 = math.sqrt(sum(v * v for v in vals if math.isfinite(v))) if nonfinite == 0 else float("nan")
            if nonfinite > 0:
                issues.append(f"layer {layer_idx}: contains {nonfinite} NaN/Inf values")
            elif l2 == 0.0:
                issues.append(f"layer {layer_idx}: all-zero dead direction vector")
            layer_vecs[layer_idx] = vals
            layer_stats.append({
                "layer": layer_idx,
                "dtype": dlabel,
                "dim": elems,
                "l2_norm": round(l2, 6) if math.isfinite(l2) else None,
                "nonfinite": nonfinite,
            })

    if not layer_stats:
        raise Fail(f"{path}: no `direction.<layer>` tensors found (not a llama.cpp control vector)")
    if len(hidden_dims) > 1:
        issues.append(f"inconsistent hidden dims across layers: {sorted(hidden_dims)}")

    layer_stats.sort(key=lambda x: int(x["layer"]))
    norms = [float(s["l2_norm"]) for s in layer_stats if s["l2_norm"] is not None and float(s["l2_norm"]) > 0]
    med_norm = statistics.median(norms) if norms else 0.0
    if med_norm > 0:
        for s in layer_stats:
            ln = s["l2_norm"]
            if ln is not None and float(ln) > med_norm * 3.0:
                issues.append(f"layer {s['layer']}: L2 norm spike ({ln:.4f} > 3x median {med_norm:.4f})")

    # Compute adjacent-layer cosine similarity to spot noisy layers.
    ordered_layers = sorted(layer_vecs)
    adj_cos: List[float] = []
    for a, b in zip(ordered_layers, ordered_layers[1:]):
        if b != a + 1:
            continue
        va, vb = layer_vecs[a], layer_vecs[b]
        if len(va) != len(vb) or any(not math.isfinite(x) for x in va) or any(not math.isfinite(x) for x in vb):
            continue
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(x * x for x in vb))
        if na > 0 and nb > 0:
            cos = sum(x * y for x, y in zip(va, vb)) / (na * nb)
            adj_cos.append(round(cos, 4))

    return {
        "file": str(path),
        "architecture": arch,
        "direction_layers": len(layer_stats),
        "layer_min": int(layer_stats[0]["layer"]),
        "layer_max": int(layer_stats[-1]["layer"]),
        "hidden_dim": next(iter(hidden_dims)) if len(hidden_dims) == 1 else sorted(hidden_dims),
        "median_l2_norm": round(med_norm, 6),
        "mean_adjacent_cosine": round(statistics.mean(adj_cos), 4) if adj_cos else None,
        "healthy": len(issues) == 0,
        "issues": issues,
        "layers": layer_stats,
    }


def cmd_inspect_gguf(args: argparse.Namespace) -> int:
    res = inspect_cvector_gguf(Path(args.path))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(
        f"cvector: {res['file']} | arch={res['architecture']} dim={res['hidden_dim']} "
        f"layers={res['layer_min']}..{res['layer_max']} ({res['direction_layers']} tensors)"
    )
    print(f"stats:   median_l2={res['median_l2_norm']} mean_adj_cosine={res['mean_adjacent_cosine']}")
    for issue in res["issues"]:
        print(f"  [ISSUE] {issue}")
    if res["healthy"]:
        print("CVECTOR: HEALTHY")
        return 0
    print(f"CVECTOR: CORRUPT ({len(res['issues'])} issues)")
    return 1


# ----------------------------------------------------------------- score-sweep

def cmd_score_sweep(args: argparse.Namespace) -> int:
    p = Path(args.sweep)
    if not p.is_file():
        raise Fail(f"sweep JSON not found: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    rows = doc.get("runs") if isinstance(doc, dict) else doc
    if not isinstance(rows, list) or not rows:
        raise Fail("sweep JSON must contain a non-empty 'runs' list")

    base_rows = [r for r in rows if abs(float(r.get("scale", 999))) < 1e-9]
    if not base_rows:
        raise Fail("sweep must include a scale=0.0 identity baseline row")
    base = base_rows[0]
    base_ppl = float(base["ppl"])
    base_task = float(base["task_score"])
    base_beh = float(base["behavior_score"])
    if not all(math.isfinite(x) for x in (base_ppl, base_task, base_beh)) or base_ppl <= 0 or base_task <= 0:
        raise Fail("baseline ppl and task_score must be finite and positive")

    neg_rows = [r for r in rows if float(r.get("scale", 0.0)) < 0]
    neg_control_status = "MISSING"
    if neg_rows:
        moved_down = any(float(r["behavior_score"]) < base_beh for r in neg_rows)
        neg_control_status = "PASS" if moved_down else "FAILED_DIRECTION"

    evaluated: List[Dict[str, object]] = []
    passing: List[Dict[str, object]] = []
    for r in rows:
        scale = float(r["scale"])
        ppl = float(r["ppl"])
        task = float(r["task_score"])
        beh = float(r["behavior_score"])
        lrange = str(r.get("layer_range") or "")
        ppl_inc_pct = round(100.0 * (ppl - base_ppl) / base_ppl, 2)
        task_ret = round(task / base_task, 4)
        beh_gain = round(beh - base_beh, 3)
        safe = (
            scale > 0
            and ppl_inc_pct <= args.max_ppl_increase_pct
            and task_ret >= args.min_task_retention
            and beh_gain > 0
        )
        rec = {
            "layer_range": lrange,
            "scale": scale,
            "ppl": ppl,
            "ppl_increase_pct": ppl_inc_pct,
            "task_score": task,
            "task_retention": task_ret,
            "behavior_score": beh,
            "behavior_gain": beh_gain,
            "safe": safe,
        }
        evaluated.append(rec)
        if safe:
            passing.append(rec)

    winner = max(passing, key=lambda x: (float(x["behavior_gain"]), -float(x["ppl_increase_pct"]))) if passing else None
    if neg_control_status == "FAILED_DIRECTION":
        winner = None

    vector_file = str(doc.get("vector_file") or "vector.gguf") if isinstance(doc, dict) else "vector.gguf"
    serve_flags = ""
    if winner:
        lo_hi = re.findall(r"\d+", winner["layer_range"])
        lr_flag = f" --control-vector-layer-range {lo_hi[0]} {lo_hi[1]}" if len(lo_hi) >= 2 else ""
        serve_flags = f"--control-vector-scaled {vector_file} {winner['scale']}{lr_flag}"

    out = {
        "baseline": {"ppl": base_ppl, "task_score": base_task, "behavior_score": base_beh},
        "gates": {"max_ppl_increase_pct": args.max_ppl_increase_pct, "min_task_retention": args.min_task_retention},
        "inverted_negative_control": neg_control_status,
        "runs": evaluated,
        "winner": winner,
        "serve_flags": serve_flags,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"baseline (scale=0.0): ppl={base_ppl:.3f} task={base_task:.3f} behavior={base_beh:.3f}")
    print(f"negative control (scale<0): {neg_control_status}")
    print(f"{'LAYERS':<12} {'SCALE':>6} {'PPL+':>7} {'TASK%':>7} {'BEH+':>7}  GATE")
    for r in evaluated:
        gate = "PASS" if r["safe"] else ("BASE" if r["scale"] == 0 else ("NEG" if r["scale"] < 0 else "REJECT"))
        print(
            f"{r['layer_range']:<12} {r['scale']:>+6.2f} {r['ppl_increase_pct']:>+6.2f}% "
            f"{r['task_retention']*100:>6.1f}% {r['behavior_gain']:>+7.3f}  {gate}"
        )

    if winner:
        print(f"\nSWEEP: WINNER FOUND ({serve_flags})")
        return 0
    if neg_control_status == "FAILED_DIRECTION":
        print("\nSWEEP: FAILED_DIRECTION (inverted negative scale did not reverse behavior; rebuild contrastive pairs)")
        return 1
    print("\nSWEEP: NO_SAFE_SCALE (every positive scale breached perplexity or task-retention gates)")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("presets", help="list or export built-in contrastive presets")
    p_pre.add_argument("--list", dest="list_all", action="store_true", help="list all presets")
    p_pre.add_argument("--export", help="preset name to export (e.g. mythos-engineering, slopchop)")
    p_pre.add_argument("--out-dir", help="destination folder for positive.txt, negative.txt and pairs.json")

    p_lint = sub.add_parser("lint-pairs", help="audit contrastive pairs for length bias, duplicates and topic leakage")
    p_lint.add_argument("--pairs-json", help="path to pairs.json")
    p_lint.add_argument("--pos", help="path to positive.txt")
    p_lint.add_argument("--neg", help="path to negative.txt")
    p_lint.add_argument("--max-length-skew", type=float, default=0.25, help="max allowed |pos/neg - 1| length skew (default: 0.25)")
    p_lint.add_argument("--json", dest="json_out", help="write JSON audit")

    p_plan = sub.add_parser("layer-plan", help="compute active layer bands and sweep schedule")
    p_plan.add_argument("--layers", type=int, required=True, help="total transformer block count of target model")
    p_plan.add_argument("--behavior", choices=tuple(BEHAVIOR_BANDS), default="coding")
    p_plan.add_argument("--model-gguf", default="model.gguf")
    p_plan.add_argument("--pos-file", default="positive.txt")
    p_plan.add_argument("--neg-file", default="negative.txt")
    p_plan.add_argument("--out-gguf", default="control-vector.gguf")
    p_plan.add_argument("--json", dest="json_out", help="write JSON plan")

    p_insp = sub.add_parser("inspect-gguf", help="inspect a binary .gguf control vector for corruption or norm spikes")
    p_insp.add_argument("path", help="path to compiled control vector .gguf")
    p_insp.add_argument("--json", dest="json_out", help="write JSON inspection report")

    p_sw = sub.add_parser("score-sweep", help="gate a layer/scale sweep against perplexity and task retention")
    p_sw.add_argument("sweep", help="path to sweep results JSON")
    p_sw.add_argument("--max-ppl-increase-pct", type=float, default=3.0, help="max allowed perplexity increase %% (default: 3.0)")
    p_sw.add_argument("--min-task-retention", type=float, default=0.97, help="min task score ratio vs baseline (default: 0.97)")
    p_sw.add_argument("--json", dest="json_out", help="write JSON verdict")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "presets":
            return cmd_presets(args)
        if args.cmd == "lint-pairs":
            return cmd_lint_pairs(args)
        if args.cmd == "layer-plan":
            return cmd_layer_plan(args)
        if args.cmd == "inspect-gguf":
            return cmd_inspect_gguf(args)
        if args.cmd == "score-sweep":
            return cmd_score_sweep(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"cvector_forge.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
