#!/usr/bin/env python3
"""Execution-verified synthetic dataset, DPO pair and split-integrity foundry.

Turns multi-sample teacher completions into clean, verified, deduplicated and
contamination-free SFT and DPO training splits for distilling large models into
fast local checkpoints. Runs real programmatic verifiers (isolated Python tests,
strict JSON schemas, exact-answer oracles and regex contracts) with built-in
negative controls before keeping any trace. Uses only Python 3.9+ stdlib.

Subcommands:
  init            Scaffold a distillation foundry run directory and config.
  verify          Execute programmatic verifiers on raw teacher traces, keep
                  only verified SFT traces, and pair pass/fail samples into DPO.
  curate          Filter truncated/slop traces, remove near-duplicates via
                  5-gram Jaccard shingling, and strip eval-contaminated items.
  split           Group strictly by prompt_id into train/val/sealed_test splits
                  (plus aligned DPO splits) and write a SHA-256 manifest.
  audit-manifest  Recompute split SHA-256 hashes and prove zero prompt_id or
                  8-gram leakage between train and sealed_test.

Exit codes: 0 clean/valid, 1 control/audit failure or zero surviving traces, 2 error.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from contextlib import ExitStack
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

if __name__ == "__main__":
    sys.dont_write_bytecode = True

from foundry_runtime.sandbox import PythonSandbox, SandboxError, local_python_sandbox

SLOP_WORDS = (
    "delve",
    "tapestry",
    "testament",
    "pivotal",
    "seamless",
    "seamlessly",
    "leverage",
    "utilize",
    "robust",
    "comprehensive",
    "game-changer",
    "furthermore",
    "moreover",
)
CHATBOT_OPENERS = (
    "great question",
    "certainly",
    "absolutely",
    "you're absolutely right",
    "of course",
    "here's a breakdown",
    "let me explain",
    "i'd be happy to",
)
CODE_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S | re.I)


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


def read_jsonl(path: Path) -> List[Dict[str, object]]:
    if not path.is_file():
        raise Fail(f"JSONL file not found: {path}")
    rows: List[Dict[str, object]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Fail(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise Fail(f"{path}:{lineno}: expected JSON object")
        rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def word_ngrams(text: str, n: int) -> Set[Tuple[str, ...]]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: Set[Tuple[str, ...]], b: Set[Tuple[str, ...]]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def extract_prompt_and_response(rec: Dict[str, object]) -> Tuple[str, str]:
    if "prompt" in rec and "response" in rec:
        return str(rec["prompt"]), str(rec["response"])
    msgs = rec.get("messages")
    if isinstance(msgs, list) and msgs:
        user_parts = [str(m.get("content") or "") for m in msgs if isinstance(m, dict) and m.get("role") == "user"]
        asst_parts = [str(m.get("content") or "") for m in msgs if isinstance(m, dict) and m.get("role") == "assistant"]
        if user_parts and asst_parts:
            return "\n".join(user_parts), asst_parts[-1]
    raise Fail(f"record {rec.get('prompt_id') or rec.get('id')!r} missing prompt/response or messages")


# ------------------------------------------------------- programmatic verifiers

def extract_python_code(text: str) -> str:
    blocks = CODE_FENCE_RE.findall(text)
    if blocks:
        return "\n\n".join(b.strip() for b in blocks if b.strip())
    return text.strip()


def run_python_tests(code: str, test_code: str, timeout_s: float, sandbox: PythonSandbox) -> Tuple[bool, str]:
    if not code.strip():
        return False, "empty python submission"
    return sandbox.run(code + "\n\n" + test_code + "\n", timeout_s)


def verify_record(
    rec: Dict[str, object], exec_timeout: float, sandbox: Optional[PythonSandbox]
) -> Tuple[bool, str]:
    _, response = extract_prompt_and_response(rec)
    verifier = rec.get("verifier")
    if not isinstance(verifier, dict) or not verifier.get("type"):
        raise Fail(f"record {rec.get('prompt_id')!r}: missing verifier object with 'type'")
    vtype = str(verifier["type"])

    if vtype == "python_tests":
        tests = str(verifier.get("tests") or "")
        if not tests.strip():
            raise Fail(f"record {rec.get('prompt_id')!r}: python_tests verifier missing 'tests'")
        code = extract_python_code(response)
        if sandbox is None:
            raise Fail("Python verification requires the local sandbox")
        return run_python_tests(code, tests, exec_timeout, sandbox)

    if vtype == "json_schema":
        stripped = response.strip()
        if verifier.get("forbid_fences", True) and stripped.startswith("```"):
            return False, "markdown fence leaked around JSON"
        if stripped.startswith("```"):
            m = re.search(r"```(?:json)?\s*\n(.*?)```", stripped, re.S | re.I)
            stripped = m.group(1).strip() if m else stripped
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            return False, f"invalid JSON: {exc}"
        if not isinstance(obj, dict):
            return False, "top-level JSON must be an object"
        req_keys = verifier.get("required_keys") or []
        missing = [k for k in req_keys if k not in obj]
        if missing:
            return False, f"missing required keys: {missing}"
        if "expected" in verifier and obj != verifier["expected"]:
            return False, "JSON does not match expected ground truth"
        return True, "ok"

    if vtype == "exact_answer":
        expected = str(verifier.get("expected", "")).strip()
        matches = re.findall(r"FINAL_ANSWER\s*:\s*([^\n]+)", response, re.I)
        got = matches[-1].strip() if matches else response.strip()
        if got == expected:
            return True, "ok"
        return False, f"got {got!r}, expected {expected!r}"

    if vtype == "regex_rules":
        for pat in verifier.get("must_match") or []:
            if not re.search(str(pat), response, re.M):
                return False, f"missing required pattern {pat!r}"
        for pat in verifier.get("must_not_match") or []:
            if re.search(str(pat), response, re.M):
                return False, f"matched forbidden pattern {pat!r}"
        return True, "ok"

    raise Fail(f"unsupported verifier type {vtype!r}")


def run_verifier_negative_controls(exec_timeout: float, sandbox: Optional[PythonSandbox]) -> List[str]:
    """Prove verifiers reject broken code, invalid JSON and wrong answers."""
    failures: List[str] = []
    controls = [
        {
            "prompt_id": "ctrl_py",
            "prompt": "write add",
            "response": "```python\ndef add(a, b): return a - b\n```",
            "verifier": {"type": "python_tests", "tests": "assert add(2, 3) == 5"},
        },
        {
            "prompt_id": "ctrl_json",
            "prompt": "return json",
            "response": "```json\n{\"ok\": true}\n```",
            "verifier": {"type": "json_schema", "required_keys": ["ok"], "forbid_fences": True},
        },
        {
            "prompt_id": "ctrl_exact",
            "prompt": "2+2",
            "response": "FINAL_ANSWER: 5",
            "verifier": {"type": "exact_answer", "expected": "4"},
        },
    ]
    for c in controls:
        if c["verifier"]["type"] == "python_tests" and sandbox is None:
            continue
        ok, _ = verify_record(c, exec_timeout, sandbox)
        if ok:
            failures.append(f"negative control {c['prompt_id']} falsely passed")
    return failures


# ----------------------------------------------------------------- subcommands

def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.out_dir)
    for sub in ("prompts", "raw", "verified", "splits", "reports"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = {
        "format": "distill-foundry-run-v1",
        "created_at": now_utc(),
        "teacher": args.teacher,
        "student": args.student,
        "domain": args.domain,
    }
    (root / "foundry.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"initialized distill-foundry run at {root} (teacher={args.teacher} -> student={args.student}, domain={args.domain})")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if not math.isfinite(args.exec_timeout) or args.exec_timeout <= 0:
        raise Fail("--exec-timeout must be finite and positive")
    raw_rows = read_jsonl(Path(args.inp))
    if not raw_rows:
        raise Fail(f"{args.inp}: no traces to verify")
    uses_python = any(
        isinstance(row.get("verifier"), dict) and row["verifier"].get("type") == "python_tests"
        for row in raw_rows
    )
    with ExitStack() as stack:
        sandbox = stack.enter_context(local_python_sandbox()) if uses_python else None
        if sandbox is not None:
            working, reason = sandbox.run("assert 2 + 3 == 5\n", args.exec_timeout)
            if not working:
                raise Fail(f"Python sandbox positive control failed: {reason}")
        return verify_traces(args, raw_rows, sandbox)


def verify_traces(
    args: argparse.Namespace, raw_rows: List[Dict[str, object]], sandbox: Optional[PythonSandbox]
) -> int:
    ctrl_errs = run_verifier_negative_controls(args.exec_timeout, sandbox)
    if ctrl_errs:
        print("FOUNDRY: CONTROL_FAILED " + "; ".join(ctrl_errs), file=sys.stderr)
        return 1

    passed_rows: List[Dict[str, object]] = []
    failed_rows: List[Dict[str, object]] = []
    by_prompt: Dict[str, Dict[str, List[Dict[str, object]]]] = defaultdict(lambda: {"pass": [], "fail": []})
    reason_counts: Counter = Counter()

    for idx, rec in enumerate(raw_rows, 1):
        pid = str(rec.get("prompt_id") or rec.get("id") or f"p_{idx:05d}")
        prompt, response = extract_prompt_and_response(rec)
        ok, reason = verify_record(rec, args.exec_timeout, sandbox)
        item = {
            "prompt_id": pid,
            "category": str(rec.get("category") or "general"),
            "teacher": str(rec.get("teacher") or ""),
            "finish_reason": str(rec.get("finish_reason") or "stop"),
            "prompt": prompt,
            "response": response,
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response},
            ],
            "verified": ok,
            "verify_reason": reason,
        }
        if ok:
            passed_rows.append(item)
            by_prompt[pid]["pass"].append(item)
        else:
            failed_rows.append(item)
            by_prompt[pid]["fail"].append(item)
            reason_counts[reason] += 1

    # Build automatic DPO preference pairs from prompts having both pass and fail samples.
    dpo_rows: List[Dict[str, object]] = []
    for pid, buckets in sorted(by_prompt.items()):
        if buckets["pass"] and buckets["fail"]:
            # Prefer concise passing trace as chosen, longest/most plausible failing trace as rejected.
            chosen = min(buckets["pass"], key=lambda x: len(str(x["response"])))
            for rej in buckets["fail"][: args.max_dpo_per_prompt]:
                dpo_rows.append({
                    "prompt_id": pid,
                    "category": chosen["category"],
                    "prompt": chosen["prompt"],
                    "chosen": chosen["response"],
                    "rejected": rej["response"],
                    "rejected_reason": rej["verify_reason"],
                })

    write_jsonl(Path(args.out_sft), passed_rows)
    if args.out_dpo:
        write_jsonl(Path(args.out_dpo), dpo_rows)

    summary = {
        "negative_controls_passed": True,
        "raw_traces": len(raw_rows),
        "verified_pass": len(passed_rows),
        "verified_fail": len(failed_rows),
        "pass_rate_pct": round(100.0 * len(passed_rows) / len(raw_rows), 1),
        "unique_prompts_passed": sum(1 for b in by_prompt.values() if b["pass"]),
        "dpo_pairs_built": len(dpo_rows),
        "top_failure_reasons": reason_counts.most_common(10),
    }
    if args.report:
        Path(args.report).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(
        f"verified: {len(passed_rows)}/{len(raw_rows)} traces passed ({summary['pass_rate_pct']}%) | "
        f"{summary['unique_prompts_passed']} unique prompts | {len(dpo_rows)} DPO pairs"
    )
    if not passed_rows:
        print("FOUNDRY: ZERO_VERIFIED_TRACES")
        return 1
    return 0


def slop_score(text: str) -> Tuple[int, List[str]]:
    lower = text.lower()
    reasons: List[str] = []
    em = text.count("\u2014")
    if em:
        reasons.append(f"em_dash({em})")
    openers = [o for o in CHATBOT_OPENERS if lower.strip().startswith(o)]
    if openers:
        reasons.append(f"opener({openers[0]})")
    words = [w for w in SLOP_WORDS if re.search(rf"\b{re.escape(w)}\b", lower)]
    if words:
        reasons.append(f"stock({','.join(words[:3])})")
    return em + len(openers) + len(words), reasons


def cmd_curate(args: argparse.Namespace) -> int:
    rows = read_jsonl(Path(args.inp))
    if not rows:
        raise Fail(f"{args.inp}: no traces to curate")

    eval_8grams: Set[Tuple[str, ...]] = set()
    if args.eval_prompts:
        for erow in read_jsonl(Path(args.eval_prompts)):
            text = str(erow.get("prompt") or "")
            if not text and isinstance(erow.get("messages"), list):
                text = " ".join(str(m.get("content") or "") for m in erow["messages"] if isinstance(m, dict))
            eval_8grams.update(word_ngrams(text, 8))

    kept: List[Dict[str, object]] = []
    kept_shingles: List[Set[Tuple[str, ...]]] = []
    drops: Counter = Counter()
    cats: Counter = Counter()
    tok_lens: List[int] = []

    for rec in rows:
        prompt, response = extract_prompt_and_response(rec)
        fin = str(rec.get("finish_reason") or "stop")
        if fin not in ("stop", "eos", "end_turn", ""):
            drops["truncated_finish"] += 1
            continue
        if len(response.strip()) < args.min_chars or len(response.strip()) > args.max_chars:
            drops["length_outlier"] += 1
            continue
        s_hits, _ = slop_score(response)
        if s_hits > args.max_slop_hits:
            drops["slop_filtered"] += 1
            continue
        if eval_8grams:
            p_grams = word_ngrams(prompt, 8)
            if p_grams and (p_grams & eval_8grams):
                drops["eval_contaminated"] += 1
                continue
        shingles = word_ngrams(prompt + "\n" + response, 5)
        if any(jaccard(shingles, prev) >= args.dedup_threshold for prev in kept_shingles):
            drops["near_duplicate"] += 1
            continue
        kept_shingles.append(shingles)
        kept.append(rec)
        cats[str(rec.get("category") or "general")] += 1
        tok_lens.append(max(1, int(round((len(prompt) + len(response)) / 4.0))))

    write_jsonl(Path(args.out), kept)
    tok_lens.sort()

    def pct(vals: Sequence[int], p: float) -> int:
        if not vals:
            return 0
        return vals[min(len(vals) - 1, int(round((len(vals) - 1) * p)))]

    rep = {
        "input_traces": len(rows),
        "kept_traces": len(kept),
        "dropped_traces": len(rows) - len(kept),
        "drop_reasons": dict(drops),
        "categories": dict(cats),
        "token_length_est": {"p10": pct(tok_lens, 0.1), "p50": pct(tok_lens, 0.5), "p90": pct(tok_lens, 0.9)},
    }
    if args.report:
        Path(args.report).write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")

    print(
        f"curated: kept {len(kept)}/{len(rows)} traces | dropped={dict(drops)} | "
        f"est tokens p50={rep['token_length_est']['p50']} p90={rep['token_length_est']['p90']}"
    )
    return 0 if kept else 1


def cmd_split(args: argparse.Namespace) -> int:
    if not (0.0 <= args.val_ratio < 0.5) or not (0.0 <= args.test_ratio < 0.5) or (args.val_ratio + args.test_ratio >= 0.9):
        raise Fail("invalid --val-ratio / --test-ratio")
    rows = read_jsonl(Path(args.inp))
    if not rows:
        raise Fail(f"{args.inp}: no traces to split")

    groups: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for idx, r in enumerate(rows, 1):
        pid = str(r.get("prompt_id") or r.get("id") or f"p_{idx:05d}")
        groups[pid].append(r)

    pids = sorted(groups)
    rng = random.Random(args.seed)
    rng.shuffle(pids)

    n = len(pids)
    n_test = max(1, int(round(n * args.test_ratio))) if (args.test_ratio > 0 and n >= 3) else 0
    n_val = max(1, int(round(n * args.val_ratio))) if (args.val_ratio > 0 and n - n_test >= 2) else 0
    test_ids = set(pids[:n_test])
    val_ids = set(pids[n_test : n_test + n_val])
    train_ids = set(pids[n_test + n_val :])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    split_rows = {
        "train": [r for pid in sorted(train_ids) for r in groups[pid]],
        "val": [r for pid in sorted(val_ids) for r in groups[pid]],
        "sealed_test": [r for pid in sorted(test_ids) for r in groups[pid]],
    }
    files_meta: Dict[str, Dict[str, object]] = {}
    for name, srows in split_rows.items():
        fpath = out_dir / f"{name}.jsonl"
        write_jsonl(fpath, srows)
        files_meta[name] = {
            "file": fpath.name,
            "rows": len(srows),
            "unique_prompts": len({str(r.get("prompt_id") or "") for r in srows}),
            "sha256": sha256_file(fpath),
        }

    if args.dpo:
        dpo_all = read_jsonl(Path(args.dpo))
        for name, id_set in (("dpo_train", train_ids), ("dpo_val", val_ids)):
            drows = [r for r in dpo_all if str(r.get("prompt_id") or "") in id_set]
            fpath = out_dir / f"{name}.jsonl"
            write_jsonl(fpath, drows)
            files_meta[name] = {"file": fpath.name, "rows": len(drows), "sha256": sha256_file(fpath)}

    manifest = {
        "format": "distill-foundry-manifest-v1",
        "created_at": now_utc(),
        "seed": args.seed,
        "splits": files_meta,
    }
    mpath = out_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"split complete ({mpath}): train={files_meta['train']['rows']} val={files_meta['val']['rows']} "
        f"sealed_test={files_meta['sealed_test']['rows']} (grouped strictly by prompt_id)"
    )
    return 0


def cmd_audit_manifest(args: argparse.Namespace) -> int:
    mpath = Path(args.manifest)
    if not mpath.is_file():
        raise Fail(f"manifest not found: {mpath}")
    doc = json.loads(mpath.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("format") != "distill-foundry-manifest-v1":
        raise Fail(f"{mpath}: invalid manifest format")
    base = mpath.parent
    issues: List[str] = []

    splits = doc.get("splits")
    if not isinstance(splits, dict):
        raise Fail("manifest must contain a splits object")
    for name in ("train", "val", "sealed_test"):
        if name not in splits:
            issues.append(f"missing required split {name}")
    loaded: Dict[str, List[Dict[str, object]]] = {}
    for name, info in splits.items():
        fpath = base / str(info["file"])
        if not fpath.is_file():
            issues.append(f"missing split file {info['file']}")
            continue
        actual_sha = sha256_file(fpath)
        if actual_sha != info["sha256"]:
            issues.append(f"{info['file']}: SHA-256 drift (expected {info['sha256'][:12]}..., got {actual_sha[:12]}...)")
        loaded[name] = read_jsonl(fpath)

    for name in ("train", "sealed_test"):
        if name in loaded and not loaded[name]:
            issues.append(f"required split {name} has no rows")
    if loaded.get("train") and loaded.get("sealed_test"):
        training = [*loaded["train"], *loaded.get("dpo_train", [])]
        train_pids = {str(r["prompt_id"]) for r in training if r.get("prompt_id")}
        test_pids = {str(r["prompt_id"]) for r in loaded["sealed_test"] if r.get("prompt_id")}
        overlap_ids = sorted(train_pids & test_pids)
        if overlap_ids:
            issues.append(f"prompt_id leak between train and sealed_test: {overlap_ids[:5]}")

        train_8 = set()
        for r in training:
            prompt = str(r["prompt"]) if "prompt" in r else extract_prompt_and_response(r)[0]
            train_8.update(word_ngrams(prompt, 8))
        leaked_ngrams = 0
        for r in loaded["sealed_test"]:
            p, _ = extract_prompt_and_response(r)
            g = word_ngrams(p, 8)
            if g and (g & train_8):
                leaked_ngrams += 1
        if leaked_ngrams:
            issues.append(f"8-gram prompt contamination between train and sealed_test on {leaked_ngrams} test item(s)")

    for iss in issues:
        print(f"  [ISSUE] {iss}")
    if issues:
        print(f"FOUNDRY: LEAK_OR_DRIFT ({len(issues)} issues)")
        return 1
    print(f"FOUNDRY: VALID ({len(splits)} splits verified, zero train/sealed_test leakage)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="scaffold a distillation foundry workspace")
    p_init.add_argument("--out-dir", required=True)
    p_init.add_argument("--teacher", required=True)
    p_init.add_argument("--student", required=True)
    p_init.add_argument("--domain", default="coding")

    p_ver = sub.add_parser("verify", help="execute programmatic verifiers on raw teacher traces and build SFT + DPO sets")
    p_ver.add_argument("--in", dest="inp", required=True, help="raw teacher traces JSONL")
    p_ver.add_argument("--out-sft", required=True, help="verified SFT JSONL output")
    p_ver.add_argument("--out-dpo", help="optional automatic DPO preference pairs JSONL output")
    p_ver.add_argument("--exec-timeout", type=float, default=5.0, help="per-trace Python test timeout seconds (default: 5)")
    p_ver.add_argument("--max-dpo-per-prompt", type=int, default=2, help="max DPO pairs per prompt_id (default: 2)")
    p_ver.add_argument("--report", help="write verification report JSON")

    p_cur = sub.add_parser("curate", help="filter slop, truncated finishes, near-duplicates and eval contamination")
    p_cur.add_argument("--in", dest="inp", required=True, help="verified SFT JSONL")
    p_cur.add_argument("--out", required=True, help="curated SFT JSONL")
    p_cur.add_argument("--eval-prompts", help="optional held-out/sealed eval JSONL for 8-gram decontamination")
    p_cur.add_argument("--dedup-threshold", type=float, default=0.80, help="5-gram Jaccard dedup threshold (default: 0.80)")
    p_cur.add_argument("--max-slop-hits", type=int, default=1, help="max allowed slop hits per trace (default: 1)")
    p_cur.add_argument("--min-chars", type=int, default=8)
    p_cur.add_argument("--max-chars", type=int, default=32000)
    p_cur.add_argument("--report", help="write curation report JSON")

    p_spl = sub.add_parser("split", help="split curated traces strictly by prompt_id into train/val/sealed_test")
    p_spl.add_argument("--in", dest="inp", required=True, help="curated SFT JSONL")
    p_spl.add_argument("--dpo", help="optional DPO JSONL aligned to the same prompt_id split")
    p_spl.add_argument("--out-dir", required=True, help="output splits directory")
    p_spl.add_argument("--val-ratio", type=float, default=0.1)
    p_spl.add_argument("--test-ratio", type=float, default=0.1)
    p_spl.add_argument("--seed", type=int, default=42)

    p_aud = sub.add_parser("audit-manifest", help="verify split SHA-256 hashes and zero train/sealed_test leakage")
    p_aud.add_argument("manifest", help="path to splits/manifest.json")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "verify":
            return cmd_verify(args)
        if args.cmd == "curate":
            return cmd_curate(args)
        if args.cmd == "split":
            return cmd_split(args)
        if args.cmd == "audit-manifest":
            return cmd_audit_manifest(args)
        raise Fail(f"unknown command {args.cmd}")
    except (Fail, SandboxError) as exc:
        print(f"foundry.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
