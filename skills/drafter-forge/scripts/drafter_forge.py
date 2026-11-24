#!/usr/bin/env python3
"""Verify, harvest, analyze and gate companion drafters for TensorFold.

Checks byte-for-byte tokenizer and special-token alignment between a target
checkpoint and candidate drafter, harvests on-policy workload completions from
a live `tensorfold serve` endpoint, computes optimal draft depth from measured
acceptance and dense/MoE verify row costs, and gates `--drafter` exactness
against `--no-drafts` receipts. Uses only the Python 3.9+ standard library.

Subcommands:
  check-vocab         Verify tokenizer, vocab prefix and special-token identity
                      between a target checkpoint and a candidate drafter.
  harvest             Collect on-policy target completions (and optional top-1
                      logprobs) over a TensorFold OpenAI-compatible endpoint.
  analyze-acceptance  Compute per-position acceptance curves, optimal draft
                      depth K* and projected net speedup over serial decode.
  gate-parity         Verify 64-hex token_sha256 exactness and positive speed
                      gain between `--no-drafts` and `--drafter` receipts.

Exit codes: 0 compatible/viable/pass, 1 mismatch/slowdown/gate fail, 2 error.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

USER_AGENT = "tensorfold-drafter-forge/1"
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


class Fail(Exception):
    """Usage, missing file, schema or endpoint error -> exit 2."""


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


def load_json_opt(path: Path) -> Optional[Dict[str, object]]:
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise Fail(f"{path}: expected JSON object")
    return doc


def normalize_merges(entries: object) -> List[Tuple[str, str]]:
    if not isinstance(entries, list):
        raise Fail("BPE merges must be an ordered list of token pairs")
    pairs = []
    for entry in entries:
        parts = entry.split() if isinstance(entry, str) else entry
        if not isinstance(parts, list) or len(parts) != 2 or not all(isinstance(token, str) for token in parts):
            raise Fail("each BPE merge must name exactly two tokens")
        pairs.append((parts[0], parts[1]))
    return pairs


def load_bpe_merges(folder: Path, embedded: object) -> Optional[List[Tuple[str, str]]]:
    pairs = normalize_merges(embedded) if embedded is not None else None
    path = folder / "merges.txt"
    if not path.is_file():
        return pairs
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if lines and lines[0].startswith("#version:"):
        lines = lines[1:]
    sidecar = normalize_merges(lines)
    if pairs is not None and pairs != sidecar:
        raise Fail(f"{folder.name}: tokenizer.json and merges.txt disagree on BPE merge order")
    return sidecar


def extract_vocab_map(folder: Path) -> Tuple[Dict[int, str], Dict[int, str], Optional[str], Optional[List[Tuple[str, str]]]]:
    """Read the token IDs, added tokens, tokenizer hash, and ranked BPE merge pairs."""
    tok_json = folder / "tokenizer.json"
    id_to_tok: Dict[int, str] = {}
    added: Dict[int, str] = {}
    digest: Optional[str] = None
    if tok_json.is_file():
        digest = sha256_file(tok_json)
        doc = json.loads(tok_json.read_text(encoding="utf-8"))
        model = doc.get("model") or {}
        vocab = model.get("vocab")
        if isinstance(vocab, dict):
            for tok, tid in vocab.items():
                if isinstance(tid, int):
                    id_to_tok[tid] = str(tok)
        elif isinstance(vocab, list):
            for tid, entry in enumerate(vocab):
                tok = entry[0] if isinstance(entry, list) and entry else entry
                id_to_tok[tid] = str(tok)
        for item in doc.get("added_tokens") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                tid = int(item["id"])
                content = str(item.get("content", ""))
                added[tid] = content
                id_to_tok[tid] = content
        return id_to_tok, added, digest, load_bpe_merges(folder, model.get("merges"))

    vocab_file = folder / "vocab.json"
    if vocab_file.is_file():
        vdoc = json.loads(vocab_file.read_text(encoding="utf-8"))
        if isinstance(vdoc, dict):
            for tok, tid in vdoc.items():
                if isinstance(tid, int):
                    id_to_tok[tid] = str(tok)
    return id_to_tok, added, digest, load_bpe_merges(folder, None)


def check_vocab_dirs(target_dir: Path, drafter_dir: Path) -> Dict[str, object]:
    if not target_dir.is_dir() or not drafter_dir.is_dir():
        raise Fail("both target and drafter paths must be existing checkpoint directories")

    t_cfg = load_json_opt(target_dir / "config.json") or {}
    d_cfg = load_json_opt(drafter_dir / "config.json") or {}
    t_tcfg = load_json_opt(target_dir / "tokenizer_config.json") or {}
    d_tcfg = load_json_opt(drafter_dir / "tokenizer_config.json") or {}

    t_sub = t_cfg.get("text_config") if isinstance(t_cfg.get("text_config"), dict) else t_cfg
    d_sub = d_cfg.get("text_config") if isinstance(d_cfg.get("text_config"), dict) else d_cfg
    t_vsize = int(t_sub.get("vocab_size") or 0)
    d_vsize = int(d_sub.get("vocab_size") or 0)

    t_map, t_added, t_sha, t_merges = extract_vocab_map(target_dir)
    d_map, d_added, d_sha, d_merges = extract_vocab_map(drafter_dir)
    if not t_map or not d_map:
        raise Fail("could not locate tokenizer.json or vocab.json in both directories")

    issues: List[str] = []
    notes: List[str] = []

    target_ids, drafter_ids = set(t_map), set(d_map)
    shared_ids = sorted(target_ids & drafter_ids)
    if target_ids != drafter_ids:
        issues.append(
            f"token ID coverage differs ({len(target_ids - drafter_ids)} target-only, "
            f"{len(drafter_ids - target_ids)} drafter-only)"
        )
    mismatches = []
    for tid in shared_ids:
        if t_map[tid] != d_map[tid]:
            mismatches.append({"id": tid, "target": t_map[tid], "drafter": d_map[tid]})
            if len(mismatches) >= 10:
                break
    if mismatches:
        issues.append(f"token ID mismatch on {len(mismatches)}+ shared IDs (first: {mismatches[0]})")

    if t_vsize and d_vsize and t_vsize != d_vsize:
        diff = abs(t_vsize - d_vsize)
        if target_ids == drafter_ids and not mismatches and diff <= 256:
            notes.append(f"vocab_size differs only by padding ({t_vsize} vs {d_vsize}); shared token IDs match")
        else:
            issues.append(f"vocab_size mismatch ({t_vsize} vs {d_vsize})")

    for field in ("bos_token_id", "eos_token_id", "pad_token_id"):
        tv = t_sub.get(field)
        dv = d_sub.get(field)
        if tv is not None and dv is not None and tv != dv:
            issues.append(f"config.{field} mismatch (target={tv}, drafter={dv})")

    for field in ("bos_token", "eos_token"):
        tv = t_tcfg.get(field)
        dv = d_tcfg.get(field)
        if tv is not None and dv is not None and tv != dv:
            issues.append(f"tokenizer_config.{field} mismatch (target={tv!r}, drafter={dv!r})")

    added_diff = [
        tid for tid in (set(t_added) | set(d_added)) if t_added.get(tid) != d_added.get(tid)
    ]
    if added_diff:
        issues.append(f"added/special token mismatch on {len(added_diff)} ID(s), first ID={min(added_diff)}")

    if t_merges != d_merges:
        issues.append("BPE merge order or coverage differs between target and drafter")

    return {
        "compatible": len(issues) == 0,
        "target_vocab_size": t_vsize or len(t_map),
        "drafter_vocab_size": d_vsize or len(d_map),
        "shared_tokens_checked": len(shared_ids),
        "tokenizer_json_exact_match": bool(t_sha and d_sha and t_sha == d_sha),
        "issues": issues,
        "notes": notes,
    }


def cmd_check_vocab(args: argparse.Namespace) -> int:
    res = check_vocab_dirs(Path(args.target), Path(args.drafter))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(
        f"vocab: target={res['target_vocab_size']} drafter={res['drafter_vocab_size']} "
        f"shared_checked={res['shared_tokens_checked']} exact_tokenizer_sha={res['tokenizer_json_exact_match']}"
    )
    for n in res["notes"]:
        print(f"  [NOTE]  {n}")
    for iss in res["issues"]:
        print(f"  [ISSUE] {iss}")
    if res["compatible"]:
        print("VOCAB: COMPATIBLE")
        return 0
    print(f"VOCAB: INCOMPATIBLE ({len(res['issues'])} issues)")
    return 1


def cmd_harvest(args: argparse.Namespace) -> int:
    src = Path(args.prompts)
    if not src.is_file():
        raise Fail(f"prompts file not found: {src}")
    rows = []
    for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise Fail(f"{src}:{lineno}: expected JSON object")
        rows.append(obj)
    if not rows:
        raise Fail(f"{src}: no prompts found")

    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    url = args.url.rstrip("/") + "/chat/completions"
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    harvested: List[Dict[str, object]] = []
    total_tokens = 0
    for idx, row in enumerate(rows, 1):
        pid = str(row.get("id") or f"p_{idx:04d}")
        messages = row.get("messages") or [{"role": "user", "content": str(row.get("prompt") or "")}]
        payload: Dict[str, object] = {
            "model": args.model,
            "messages": messages,
            "temperature": args.temperature,
            "seed": args.seed,
            "max_tokens": args.max_tokens,
            "stream": False,
        }
        if args.logprobs:
            payload["logprobs"] = True
            payload["top_logprobs"] = 1
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise Fail(f"harvest request {pid} failed: {exc}") from exc
        choices = body.get("choices") or []
        if not choices:
            raise Fail(f"harvest request {pid} returned no choices")
        msg = choices[0].get("message") or {}
        usage = body.get("usage") or {}
        comp_toks = int(usage.get("completion_tokens") or 0)
        total_tokens += comp_toks
        rec: Dict[str, object] = {
            "id": pid,
            "domain": row.get("domain", "general"),
            "target_model": args.model,
            "messages": list(messages) + [{"role": "assistant", "content": msg.get("content") or ""}],
            "finish_reason": choices[0].get("finish_reason"),
            "completion_tokens": comp_toks,
        }
        if args.logprobs and isinstance(choices[0].get("logprobs"), dict):
            rec["logprobs"] = choices[0]["logprobs"].get("content") or []
        harvested.append(rec)

    with out_path.open("w", encoding="utf-8") as fh:
        for rec in harvested:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    digest = sha256_file(out_path)
    print(f"HARVESTED: {len(harvested)} traces ({total_tokens} completion tokens) -> {out_path} (sha256={digest[:16]}...)")
    return 0


def analyze_acceptance_doc(doc: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(doc, dict):
        raise Fail("acceptance receipt must be a JSON object")

    draft_step_ms = float(doc.get("draft_step_ms") or 0.0)
    verify_ms_by_width = doc.get("verify_ms_by_width") or {}
    base_step_ms = float(doc.get("base_step_ms") or 0.0)
    if not math.isfinite(draft_step_ms) or draft_step_ms <= 0:
        raise Fail("draft_step_ms must be finite and positive")

    pos_probs: List[float] = []
    if isinstance(doc.get("position_probs"), list) and doc["position_probs"]:
        for i, v in enumerate(doc["position_probs"], 1):
            p = float(v)
            if not math.isfinite(p) or not (0.0 <= p <= 1.0):
                raise Fail(f"position_probs[{i}] must be in [0, 1]")
            pos_probs.append(p)
    elif isinstance(doc.get("steps"), list) and doc["steps"]:
        max_k = max(int(s.get("drafted") or 0) for s in doc["steps"])
        if max_k < 1:
            raise Fail("steps contain no positive 'drafted' counts")
        # Conditional acceptance rate alpha_k = P(pos k accepted | pos 1..k-1 accepted)
        for k in range(1, max_k + 1):
            reached = sum(1 for s in doc["steps"] if int(s.get("drafted") or 0) >= k and int(s.get("accepted") or 0) >= k - 1)
            passed = sum(1 for s in doc["steps"] if int(s.get("drafted") or 0) >= k and int(s.get("accepted") or 0) >= k)
            pos_probs.append((passed / reached) if reached > 0 else 0.0)
    else:
        raise Fail("provide either 'position_probs' list or 'steps' list in acceptance JSON")

    if base_step_ms <= 0:
        base_step_ms = float(verify_ms_by_width.get("1") or 0.0)
    if not math.isfinite(base_step_ms) or base_step_ms <= 0:
        raise Fail("base_step_ms (or verify_ms_by_width['1']) must be finite and positive")
    base_tps = 1000.0 / base_step_ms

    # Compute survival and expected landed tokens for each draft depth K = 1..len(pos_probs)
    widths: List[Dict[str, object]] = []
    cum_survival = 1.0
    expected_extra = 0.0
    for k, alpha in enumerate(pos_probs, 1):
        cum_survival *= alpha
        expected_extra += cum_survival
        expected_landed = 1.0 + expected_extra
        width_rows = k + 1
        # Use measured verify cost at K+1 rows, or linear interpolation if absent.
        if str(width_rows) in verify_ms_by_width:
            ver_ms = float(verify_ms_by_width[str(width_rows)])
        else:
            slope = float(doc.get("verify_ms_per_extra_row") or 0.0)
            ver_ms = base_step_ms + slope * k
        if not math.isfinite(ver_ms) or ver_ms <= 0:
            raise Fail(f"invalid verify_ms at width {width_rows}")
        pass_ms = k * draft_step_ms + ver_ms
        net_tps = expected_landed * (1000.0 / pass_ms)
        speedup = net_tps / base_tps
        widths.append({
            "draft_depth": k,
            "verify_rows": width_rows,
            "cond_accept": round(alpha, 4),
            "cum_survival": round(cum_survival, 4),
            "expected_landed": round(expected_landed, 3),
            "pass_ms": round(pass_ms, 3),
            "passes_per_s": round(1000.0 / pass_ms, 2),
            "net_tps": round(net_tps, 2),
            "speedup_vs_serial": round(speedup, 3),
        })

    best = max(widths, key=lambda w: float(w["net_tps"]))
    viable = float(best["speedup_vs_serial"]) > 1.03
    return {
        "serial_base_tps": round(base_tps, 2),
        "draft_step_ms": draft_step_ms,
        "best_depth": best["draft_depth"],
        "best_net_tps": best["net_tps"],
        "best_speedup": best["speedup_vs_serial"],
        "viable": viable,
        "widths": widths,
    }


def cmd_analyze_acceptance(args: argparse.Namespace) -> int:
    p = Path(args.receipt)
    if not p.is_file():
        raise Fail(f"receipt not found: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    res = analyze_acceptance_doc(doc)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")

    print(f"serial baseline: {res['serial_base_tps']:.2f} tok/s | draft_step={res['draft_step_ms']} ms")
    print(f"{'K':>3} {'ROWS':>5} {'ALPHA':>7} {'LANDED':>7} {'PASS_MS':>8} {'NET_TPS':>9} {'SPEEDUP':>8}")
    for w in res["widths"]:
        print(
            f"{w['draft_depth']:>3} {w['verify_rows']:>5} {w['cond_accept']:>7.3f} "
            f"{w['expected_landed']:>7.2f} {w['pass_ms']:>8.2f} {w['net_tps']:>9.2f} {w['speedup_vs_serial']:>7.2f}x"
        )
    if res["viable"]:
        print(f"DRAFTER: VIABLE (best K={res['best_depth']}, {res['best_net_tps']:.2f} tok/s, {res['best_speedup']:.2f}x vs serial)")
        return 0
    print(f"DRAFTER: SLOWER_THAN_SERIAL (best speedup {res['best_speedup']:.2f}x; improve acceptance or reduce draft/verify row cost)")
    return 1


def _require_sha(obj: dict, label: str) -> str:
    val = obj.get("token_sha256")
    if not isinstance(val, str) or SHA256_RE.fullmatch(val) is None:
        raise Fail(f"{label}: missing or invalid 64-hex token_sha256")
    return val.lower()


def cmd_gate_parity(args: argparse.Namespace) -> int:
    base_doc = json.loads(Path(args.serial).read_text(encoding="utf-8"))
    cand_doc = json.loads(Path(args.drafted).read_text(encoding="utf-8"))
    b_cells = base_doc.get("cells") if isinstance(base_doc, dict) else None
    c_cells = cand_doc.get("cells") if isinstance(cand_doc, dict) else None
    if not isinstance(b_cells, dict) or not isinstance(c_cells, dict) or not b_cells:
        raise Fail("both receipts must contain a non-empty 'cells' mapping")
    if set(b_cells) != set(c_cells):
        raise Fail("serial and drafted receipts do not cover the exact same cell names")

    mismatches: List[str] = []
    slowdowns: List[str] = []
    speedups: List[float] = []
    for name in sorted(b_cells):
        b, c = b_cells[name], c_cells[name]
        if _require_sha(b, f"serial[{name}]") != _require_sha(c, f"drafted[{name}]"):
            mismatches.append(name)
        b_tps = float(b.get("decode_tps") or 0.0)
        c_tps = float(c.get("decode_tps") or 0.0)
        if not math.isfinite(b_tps) or not math.isfinite(c_tps) or b_tps <= 0 or c_tps <= 0:
            raise Fail(f"cell {name!r}: decode_tps must be finite and positive on both arms")
        ratio = c_tps / b_tps
        speedups.append(ratio)
        if ratio < 1.0 - args.noise_floor:
            slowdowns.append(f"{name} ({b_tps:.1f} -> {c_tps:.1f} tok/s)")

    geo = math.exp(sum(math.log(s) for s in speedups) / len(speedups))
    passed = (not mismatches) and (not slowdowns) and (geo > 1.0 + args.noise_floor)
    report = {
        "cells": len(b_cells),
        "exact_mismatches": mismatches,
        "regressions": slowdowns,
        "geomean_speedup": round(geo, 3),
        "passed": passed,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"cells: {len(b_cells)} | exact_mismatches={len(mismatches)} | regressions={len(slowdowns)} | geomean={geo:.2f}x")
    if mismatches:
        print(f"DRAFTER GATE: REJECTED_EXACTNESS ({', '.join(mismatches[:5])})")
        return 1
    if slowdowns or not passed:
        print(f"DRAFTER GATE: REJECTED_SPEED ({', '.join(slowdowns) if slowdowns else 'gain inside noise floor'})")
        return 1
    print("DRAFTER GATE: PASS")
    if args.model and args.drafter:
        print(f"SERVE: tensorfold serve {args.model} --drafter {args.drafter} --drafter-bits {args.drafter_bits}")
        print(f"BENCH: tensorfold benchmark {args.model} --drafter {args.drafter} --tokens 256 --reps 5 --temperatures 1.0,0")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_voc = sub.add_parser("check-vocab", help="verify exact tokenizer and special-token alignment")
    p_voc.add_argument("target", help="path to target checkpoint directory")
    p_voc.add_argument("drafter", help="path to candidate drafter checkpoint directory")
    p_voc.add_argument("--json", dest="json_out", help="write JSON compatibility report")

    p_harv = sub.add_parser("harvest", help="harvest on-policy completions from a running tensorfold serve endpoint")
    p_harv.add_argument("--url", required=True, help="TensorFold OpenAI-compatible base URL (e.g. http://127.0.0.1:8080/v1)")
    p_harv.add_argument("--model", required=True, help="target model name")
    p_harv.add_argument("--prompts", required=True, help="input prompts JSONL file")
    p_harv.add_argument("--out", required=True, help="output on-policy JSONL path")
    p_harv.add_argument("--temperature", type=float, default=0.0)
    p_harv.add_argument("--seed", type=int, default=0)
    p_harv.add_argument("--max-tokens", type=int, default=512)
    p_harv.add_argument("--timeout", type=float, default=180.0)
    p_harv.add_argument("--api-key-env", help="env var holding Bearer token")
    p_harv.add_argument("--logprobs", action="store_true", help="record top-1 token logprobs")

    p_acc = sub.add_parser("analyze-acceptance", help="solve optimal draft depth K* and net speedup from acceptance receipt")
    p_acc.add_argument("receipt", help="JSON file with position_probs or steps + draft_step_ms + verify_ms_by_width")
    p_acc.add_argument("--json", dest="json_out", help="write JSON analysis")

    p_gate = sub.add_parser("gate-parity", help="verify exact token_sha256 parity and speed gain between serial and drafted receipts")
    p_gate.add_argument("serial", help="receipt JSON from --no-drafts / --serial run")
    p_gate.add_argument("drafted", help="receipt JSON from --drafter run")
    p_gate.add_argument("--noise-floor", type=float, default=0.03, help="relative base-vs-base noise band (default: 0.03)")
    p_gate.add_argument("--model", help="target model ID for emitted serve/benchmark commands")
    p_gate.add_argument("--drafter", help="drafter path or repo ID for emitted commands")
    p_gate.add_argument("--drafter-bits", choices=("0", "4"), default="4", help="TensorFold --drafter-bits setting (default: 4)")
    p_gate.add_argument("--json", dest="json_out", help="write JSON verdict")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "check-vocab":
            return cmd_check_vocab(args)
        if args.cmd == "harvest":
            return cmd_harvest(args)
        if args.cmd == "analyze-acceptance":
            return cmd_analyze_acceptance(args)
        if args.cmd == "gate-parity":
            return cmd_gate_parity(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"drafter_forge.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
