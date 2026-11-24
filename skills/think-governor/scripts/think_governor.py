#!/usr/bin/env python3
"""Calibrate reasoning budgets, detect overthinking loops and tune effort tiers.

Generates a seeded 4-tier reasoning calibration ladder (`trivial`, `medium`,
`hard`, `loop_trap`), profiles thinking tokens, empty-answer blowouts, loop
cycles and accuracy across effort arms over any OpenAI-compatible endpoint,
audits raw reasoning traces offline, and computes an effort-routing policy with
ready-to-run `tensorfold serve` flags. Uses only the Python 3.9+ standard library.

Subcommands:
  generate    Build a frozen, seeded 4-tier calibration ladder bound to SHA-256.
  profile     Run the ladder against one effort/budget arm and save a receipt.
  scan-trace  Audit a saved thinking trace for hesitation spirals and n-gram
              loops, recommending a `--thinking-budget` cut point.
  tune        Compare multiple effort/budget receipts on the same ladder and
              emit per-tier `tensorfold serve` flags plus a routing policy JSON.

Exit codes: 0 clean/healthy/policy found, 1 loop/control/gate failure, 2 error.
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
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

USER_AGENT = "think-governor/1"
LADDER_FORMAT = "think-governor-ladder-v1"
RECEIPT_FORMAT = "think-governor-receipt-v1"

HESITATION_PATTERNS = (
    re.compile(r"\bwait\s*[,!\.]", re.I),
    re.compile(r"\bhold on\b", re.I),
    re.compile(r"\blet me (?:double[- ]?check|re[- ]?verify|recheck|rethink)\b", re.I),
    re.compile(r"\bactually\s*,\s*wait\b", re.I),
    re.compile(r"\bjust to be (?:sure|certain)\b", re.I),
    re.compile(r"\bon second thought\b", re.I),
)
THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.S | re.I)


class Fail(Exception):
    """Usage, schema or endpoint error -> exit 2."""


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_sha256(obj: object) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(round(len(text) / 4.0)))


def split_reasoning_and_answer(content: str, reasoning_field: str) -> Tuple[str, str, bool]:
    """Return (reasoning_text, visible_answer, unclosed_think_truncation)."""
    if reasoning_field:
        return reasoning_field.strip(), content.strip(), (not content.strip())
    matches = THINK_BLOCK_RE.findall(content)
    if matches:
        reasoning = "\n".join(m.strip() for m in matches)
        visible = THINK_BLOCK_RE.sub("", content).strip()
        return reasoning, visible, (not visible)
    if "<think>" in content.lower() and "</think>" not in content.lower():
        after = re.split(r"<think>", content, maxsplit=1, flags=re.I)[-1].strip()
        return after, "", True
    return "", content.strip(), False


def analyze_reasoning_trace(text: str, ngram_n: int = 8, repeat_threshold: int = 3) -> Dict[str, object]:
    tokens_est = estimate_tokens(text)
    hesitation_hits = sum(len(pat.findall(text)) for pat in HESITATION_PATTERNS)

    words = re.findall(r"[a-z0-9_']+", text.lower())
    loop_detected = False
    max_ngram_repeats = 0
    repeated_phrase = ""
    loop_onset_word = None

    if len(words) >= ngram_n * repeat_threshold:
        seen_positions: Dict[Tuple[str, ...], List[int]] = {}
        for idx in range(len(words) - ngram_n + 1):
            gram = tuple(words[idx : idx + ngram_n])
            positions = seen_positions.setdefault(gram, [])
            # Count non-overlapping repetitions only.
            if not positions or idx - positions[-1] >= ngram_n:
                positions.append(idx)
                if len(positions) > max_ngram_repeats:
                    max_ngram_repeats = len(positions)
                    repeated_phrase = " ".join(gram)
                if len(positions) == repeat_threshold and loop_onset_word is None:
                    loop_onset_word = positions[1]
                    loop_detected = True

    if hesitation_hits >= 10 and tokens_est >= 400:
        loop_detected = True

    recommended_budget = None
    if loop_onset_word is not None:
        # Rough word-to-token multiplier (~1.35) with small safety margin.
        recommended_budget = max(128, int(round((loop_onset_word * 1.35) / 64.0) * 64))
    elif loop_detected:
        recommended_budget = max(256, int(round((tokens_est * 0.6) / 64.0) * 64))

    return {
        "reasoning_tokens_est": tokens_est,
        "hesitation_hits": hesitation_hits,
        "max_ngram_repeats": max_ngram_repeats,
        "repeated_phrase": repeated_phrase if max_ngram_repeats >= repeat_threshold else "",
        "loop_detected": loop_detected,
        "loop_onset_word": loop_onset_word,
        "recommended_budget": recommended_budget,
    }


# ---------------------------------------------------------- ladder generation

def generate_ladder(seed: int, per_tier: int) -> Dict[str, object]:
    rng = random.Random(seed)
    cases: List[Dict[str, object]] = []

    # 1. Trivial tier (should need 0-64 thinking tokens)
    for i in range(per_tier):
        a = rng.randint(12, 89)
        b = rng.randint(11, 79)
        ans = a + b
        cases.append({
            "id": f"trivial_{i + 1}",
            "tier": "trivial",
            "prompt": f"Compute {a} + {b}. Reply with one line: FINAL_ANSWER: <integer>",
            "expected": str(ans),
        })

    # 2. Medium tier (bounded reasoning: 256-1024 thinking tokens)
    for i in range(per_tier):
        nums = [rng.randint(4, 24) for _ in range(5)]
        k = rng.choice([2, 3, 4])
        total = sum(x * x if x % k == 0 else x for x in nums)
        cases.append({
            "id": f"medium_{i + 1}",
            "tier": "medium",
            "prompt": (
                f"Given the list {nums}, replace every element divisible by {k} with its square, "
                "leave the other elements unchanged, and sum the resulting list. "
                "End with: FINAL_ANSWER: <integer>"
            ),
            "expected": str(total),
        })

    # 3. Hard tier (multi-step state trace requiring deeper reasoning)
    for i in range(per_tier):
        r = [rng.randint(5, 31) for _ in range(4)]
        init = list(r)
        ops = []
        for step in range(9):
            a, b = rng.sample(range(4), 2)
            c = rng.randint(3, 19)
            kind = step % 3
            if kind == 0:
                ops.append(f"{step + 1}. R{a} = (R{a} + 2*R{b} + {c}) mod 89")
                r[a] = (r[a] + 2 * r[b] + c) % 89
            elif kind == 1:
                ops.append(f"{step + 1}. If R{a} >= R{b}: R{a} = R{a} - R{b}; else R{b} = (R{b} + {c}) mod 89")
                if r[a] >= r[b]:
                    r[a] -= r[b]
                else:
                    r[b] = (r[b] + c) % 89
            else:
                ops.append(f"{step + 1}. Swap R{a} and R{b}, then R{a} = (R{a} XOR {c}) mod 89")
                r[a], r[b] = r[b], r[a]
                r[a] = (r[a] ^ c) % 89
        ans = r[0] * 100 + r[1] * 10 + r[2] + r[3]
        cases.append({
            "id": f"hard_{i + 1}",
            "tier": "hard",
            "prompt": (
                f"Initial registers: R0={init[0]}, R1={init[1]}, R2={init[2]}, R3={init[3]}.\n"
                + "\n".join(ops)
                + "\nCompute R0*100 + R1*10 + R2 + R3. End with: FINAL_ANSWER: <integer>"
            ),
            "expected": str(ans),
        })

    # 4. Loop-trap tier (simple truth wrapped in classic overthinking bait)
    traps = [
        (
            "A server processes {n} requests in parallel, each taking exactly {t} seconds. "
            "Assuming enough slots for all {n} requests at once with zero queueing, how many seconds until all {n} finish? "
            "End with: FINAL_ANSWER: <integer>",
            lambda a, b: str(b),
        ),
        (
            "You have {n} identical servers. Every server has a backup key, and backup keys are NOT shared. "
            "If {b} servers are shut down and removed permanently, how many active servers remain? "
            "End with: FINAL_ANSWER: <integer>",
            lambda a, b: str(a - b),
        ),
        (
            "A list holds the integers 1 through {n} inclusive. You reverse the list twice, then count how many integers are in the list. "
            "End with: FINAL_ANSWER: <integer>",
            lambda a, b: str(a),
        ),
    ]
    for i in range(per_tier):
        tmpl, fn = traps[i % len(traps)]
        n = rng.randint(14, 48)
        t = rng.randint(3, 11)
        ans = fn(n, t)
        cases.append({
            "id": f"loop_trap_{i + 1}",
            "tier": "loop_trap",
            "prompt": tmpl.format(n=n, t=t, b=t),
            "expected": ans,
        })

    return {
        "format": LADDER_FORMAT,
        "seed": seed,
        "per_tier": per_tier,
        "cases": cases,
        "ladder_sha256": canonical_sha256(cases),
    }


def grade_answer(expected: str, visible: str) -> Tuple[bool, str]:
    if not visible.strip():
        return False, "empty visible answer"
    matches = re.findall(r"FINAL_ANSWER\s*:\s*([^\n]+)", visible, re.I)
    if not matches:
        return False, "missing FINAL_ANSWER line"
    m = re.search(r"-?\d+", matches[-1])
    if not m:
        return False, f"non-integer FINAL_ANSWER {matches[-1]!r}"
    got = m.group(0)
    if got != expected:
        return False, f"got {got}, expected {expected}"
    return True, "ok"


def verify_controls() -> None:
    ok_empty, _ = grade_answer("42", "")
    ok_wrong, _ = grade_answer("42", "FINAL_ANSWER: 41")
    loop_sample = ("Wait, let me double check this carefully before answering. " * 6)
    tr = analyze_reasoning_trace(loop_sample)
    if ok_empty or ok_wrong or not tr["loop_detected"]:
        raise RuntimeError("internal negative control failed")


# ----------------------------------------------------------- OpenAI HTTP client

def call_chat(
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    seed: int,
    timeout: float,
    api_key: Optional[str],
    extra_body: Dict[str, object],
) -> Dict[str, object]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload: Dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "seed": seed,
        "max_tokens": max_tokens,
        "stream": False,
    }
    payload.update(extra_body)
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise Fail(f"chat request failed against {url}: {exc}") from exc
    wall_s = time.perf_counter() - t0
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise Fail(f"missing choices from {url}")
    msg = choices[0].get("message") or {}
    usage = body.get("usage") or {}
    comp_details = usage.get("completion_tokens_details") or {}
    return {
        "content": str(msg.get("content") or ""),
        "reasoning_content": str(msg.get("reasoning_content") or ""),
        "finish_reason": str(choices[0].get("finish_reason") or ""),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens_reported": int(comp_details.get("reasoning_tokens") or 0),
        "wall_s": round(wall_s, 3),
    }


# ----------------------------------------------------------------- subcommands

def cmd_generate(args: argparse.Namespace) -> int:
    if args.per_tier < 1:
        raise Fail("--per-tier must be positive")
    try:
        verify_controls()
    except RuntimeError as exc:
        print(f"GOVERNOR: CONTROL_FAILED ({exc})", file=sys.stderr)
        return 1
    ladder = generate_ladder(args.seed, args.per_tier)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ladder, indent=2) + "\n", encoding="utf-8")
    print(f"ladder written: {out} ({len(ladder['cases'])} cases across 4 tiers, sha256={ladder['ladder_sha256']})")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    try:
        verify_controls()
    except RuntimeError as exc:
        print(f"GOVERNOR: CONTROL_FAILED ({exc})", file=sys.stderr)
        return 1
    ladder = json.loads(Path(args.ladder).read_text(encoding="utf-8"))
    if not isinstance(ladder, dict) or ladder.get("format") != LADDER_FORMAT:
        raise Fail(f"{args.ladder}: not a valid {LADDER_FORMAT} file")
    extra_body = json.loads(args.extra_json) if args.extra_json else {}
    if not isinstance(extra_body, dict):
        raise Fail("--extra-json must decode to a JSON object")
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None

    rows: List[Dict[str, object]] = []
    by_tier: Dict[str, List[Dict[str, object]]] = {}
    for case in ladder["cases"]:
        res = call_chat(
            base_url=args.url,
            model=args.model,
            prompt=str(case["prompt"]),
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            seed=args.seed,
            timeout=args.timeout,
            api_key=api_key,
            extra_body=extra_body,
        )
        reasoning, visible, unclosed = split_reasoning_and_answer(res["content"], res["reasoning_content"])
        trace = analyze_reasoning_trace(reasoning)
        think_toks = res["reasoning_tokens_reported"] or int(trace["reasoning_tokens_est"])
        vis_toks = estimate_tokens(visible)
        truncated_empty = unclosed or (res["finish_reason"] == "length" and not visible)
        passed, reason = grade_answer(str(case["expected"]), visible)
        row = {
            "id": case["id"],
            "tier": case["tier"],
            "passed": passed,
            "reason": reason,
            "think_tokens": think_toks,
            "visible_tokens": vis_toks,
            "wall_s": res["wall_s"],
            "finish_reason": res["finish_reason"],
            "truncated_empty": truncated_empty,
            "loop_detected": bool(trace["loop_detected"]),
            "hesitation_hits": int(trace["hesitation_hits"]),
        }
        rows.append(row)
        by_tier.setdefault(str(case["tier"]), []).append(row)

    tier_summary: Dict[str, Dict[str, object]] = {}
    for tier, trows in sorted(by_tier.items()):
        acc = round(100.0 * sum(1 for r in trows if r["passed"]) / len(trows), 1)
        med_think = int(round(statistics.median(int(r["think_tokens"]) for r in trows)))
        p95_think = int(round(max(int(r["think_tokens"]) for r in trows)))
        med_s = round(statistics.median(float(r["wall_s"]) for r in trows), 3)
        loops = sum(1 for r in trows if r["loop_detected"])
        empties = sum(1 for r in trows if r["truncated_empty"])
        tier_summary[tier] = {
            "cases": len(trows),
            "accuracy_pct": acc,
            "median_think_tokens": med_think,
            "max_think_tokens": p95_think,
            "median_wall_s": med_s,
            "loops": loops,
            "truncated_empty": empties,
        }

    receipt = {
        "format": RECEIPT_FORMAT,
        "timestamp": now_utc(),
        "ladder_sha256": ladder["ladder_sha256"],
        "arm": args.arm,
        "model": args.model,
        "serve_flags": args.serve_flags,
        "thinking_budget": args.thinking_budget,
        "tiers": tier_summary,
        "cases": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print(f"GOVERNOR PROFILED [{args.arm}]:")
    for tier in ("trivial", "medium", "hard", "loop_trap"):
        if tier in tier_summary:
            ts = tier_summary[tier]
            print(
                f"  {tier:<10} acc={ts['accuracy_pct']:>5.1f}%  med_think={ts['median_think_tokens']:>5} tok  "
                f"wall={ts['median_wall_s']:>5.2f}s  loops={ts['loops']}  empty={ts['truncated_empty']}"
            )
    return 0


def cmd_scan_trace(args: argparse.Namespace) -> int:
    p = Path(args.trace)
    if not p.is_file():
        raise Fail(f"trace file not found: {p}")
    raw = p.read_text(encoding="utf-8")
    reasoning = raw
    if p.suffix.lower() == ".json":
        doc = json.loads(raw)
        if isinstance(doc, dict):
            msg = ((doc.get("choices") or [{}])[0]).get("message") or doc
            reasoning, _, _ = split_reasoning_and_answer(str(msg.get("content") or ""), str(msg.get("reasoning_content") or ""))
    res = analyze_reasoning_trace(reasoning, ngram_n=args.ngram, repeat_threshold=args.repeats)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")

    print(
        f"trace: ~{res['reasoning_tokens_est']} think tokens | hesitations={res['hesitation_hits']} "
        f"| max_ngram_repeats={res['max_ngram_repeats']}"
    )
    if res["loop_detected"]:
        if res["repeated_phrase"]:
            print(f"  repeated cycle: {res['repeated_phrase']!r}")
        if res["recommended_budget"]:
            print(f"  recommended cap: --thinking-budget {res['recommended_budget']} --thinking-reserve 512 --loop-guard --loop-guard-stop")
        print("TRACE: LOOP_DETECTED")
        return 1
    print("TRACE: HEALTHY")
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    paths: List[Path] = [Path(p) for p in args.receipts]
    if args.dir:
        d = Path(args.dir)
        if not d.is_dir():
            raise Fail(f"not a directory: {args.dir}")
        paths.extend(sorted(d.glob("*.json")))
    receipts: List[Dict[str, object]] = []
    for p in paths:
        if not p.is_file():
            raise Fail(f"receipt not found: {p}")
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("format") == RECEIPT_FORMAT:
            receipts.append(doc)
    if not receipts:
        raise Fail("no valid think-governor receipts found")

    hashes = {str(r.get("ladder_sha256") or "") for r in receipts}
    if len(hashes) != 1 or "" in hashes:
        raise Fail(f"receipts do not share one valid ladder_sha256: {sorted(hashes)}")

    thresholds = {
        "trivial": args.min_trivial_acc,
        "medium": args.min_medium_acc,
        "hard": args.min_hard_acc,
        "loop_trap": args.min_trap_acc,
    }
    policy: Dict[str, Dict[str, object]] = {}
    unmet: List[str] = []

    for tier in ("trivial", "medium", "hard", "loop_trap"):
        candidates = []
        for r in receipts:
            ts = (r.get("tiers") or {}).get(tier)
            if not ts or ts.get("cases") == 0:
                continue
            ok = (
                float(ts["accuracy_pct"]) >= thresholds[tier]
                and int(ts["truncated_empty"]) == 0
                and int(ts["loops"]) == 0
            )
            candidates.append((ok, r, ts))
        if not candidates:
            unmet.append(tier)
            policy[tier] = {"selected_arm": None, "status": "NO_TIER_EVIDENCE"}
            continue
        passing = [c for c in candidates if c[0]]
        if passing:
            # Pick lowest median thinking tokens, breaking ties by lowest wall latency.
            _, best_r, best_ts = min(passing, key=lambda c: (int(c[2]["median_think_tokens"]), float(c[2]["median_wall_s"])))
            worst_toks = max(int(c[2]["median_think_tokens"]) for c in candidates)
            saved_pct = round(100.0 * (worst_toks - int(best_ts["median_think_tokens"])) / worst_toks, 1) if worst_toks > 0 else 0.0
            budget = best_r.get("thinking_budget")
            if int(best_ts["median_think_tokens"]) == 0:
                tf_cmd = "tensorfold serve MODEL --no-thinking --loop-guard"
            elif budget:
                tf_cmd = f"tensorfold serve MODEL --thinking --thinking-budget {budget} --thinking-reserve 512 --loop-guard --loop-guard-stop"
            else:
                cap = max(256, int(round((int(best_ts["max_think_tokens"]) * 1.25) / 128.0) * 128))
                tf_cmd = f"tensorfold serve MODEL --thinking --thinking-budget {cap} --thinking-reserve 512 --loop-guard --loop-guard-stop"
            policy[tier] = {
                "selected_arm": best_r["arm"],
                "accuracy_pct": best_ts["accuracy_pct"],
                "median_think_tokens": best_ts["median_think_tokens"],
                "median_wall_s": best_ts["median_wall_s"],
                "think_tokens_saved_vs_max_pct": saved_pct,
                "tensorfold_serve": tf_cmd,
            }
        else:
            unmet.append(tier)
            _, best_r, best_ts = max(candidates, key=lambda c: (float(c[2]["accuracy_pct"]), -int(c[2]["loops"]), -int(c[2]["truncated_empty"])))
            policy[tier] = {
                "selected_arm": None,
                "best_observed_arm": best_r["arm"],
                "accuracy_pct": best_ts["accuracy_pct"],
                "loops": best_ts["loops"],
                "truncated_empty": best_ts["truncated_empty"],
                "status": "NO_ARM_MET_GATE",
            }

    out = {
        "ladder_sha256": next(iter(hashes)),
        "arms_compared": [r["arm"] for r in receipts],
        "gates": thresholds,
        "unmet_tiers": unmet,
        "policy": policy,
    }
    if args.policy_out:
        Path(args.policy_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"think-governor policy ({len(receipts)} arms, ladder {out['ladder_sha256'][:12]}...):")
    for tier, pinfo in policy.items():
        if pinfo.get("selected_arm"):
            print(
                f"  {tier:<10} -> {pinfo['selected_arm']:<16} acc={pinfo['accuracy_pct']:>5.1f}%  "
                f"think={pinfo['median_think_tokens']:>4} tok (saves {pinfo['think_tokens_saved_vs_max_pct']:>4.1f}%)"
            )
            print(f"               {pinfo['tensorfold_serve']}")
        elif pinfo.get("status") == "NO_TIER_EVIDENCE":
            print(f"  {tier:<10} -> NO_TIER_EVIDENCE")
        else:
            print(f"  {tier:<10} -> NO_ARM_MET_GATE (best={pinfo['best_observed_arm']} acc={pinfo['accuracy_pct']}% loops={pinfo['loops']} empty={pinfo['truncated_empty']})")

    if unmet:
        print(f"GOVERNOR: INCOMPLETE (unmet tiers: {', '.join(unmet)})")
        return 1
    print("GOVERNOR: POLICY_READY")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="generate a frozen 4-tier reasoning calibration ladder")
    p_gen.add_argument("--seed", type=int, default=20261123)
    p_gen.add_argument("--per-tier", type=int, default=4, help="cases per tier (default: 4)")
    p_gen.add_argument("--out", required=True, help="output ladder JSON path")

    p_prof = sub.add_parser("profile", help="run the calibration ladder against one effort/budget arm")
    p_prof.add_argument("--ladder", required=True, help="path to ladder.json")
    p_prof.add_argument("--url", required=True, help="OpenAI-compatible base URL")
    p_prof.add_argument("--model", required=True, help="model ID")
    p_prof.add_argument("--arm", required=True, help="effort/budget arm label (e.g. no-thinking, budget-512, budget-2048)")
    p_prof.add_argument("--thinking-budget", type=int, help="configured thinking token cap for this arm")
    p_prof.add_argument("--serve-flags", default="", help="exact server flags recorded in receipt")
    p_prof.add_argument("--max-tokens", type=int, default=4096)
    p_prof.add_argument("--temperature", type=float, default=0.6)
    p_prof.add_argument("--seed", type=int, default=0)
    p_prof.add_argument("--timeout", type=float, default=240.0)
    p_prof.add_argument("--api-key-env", help="env var holding Bearer token")
    p_prof.add_argument("--extra-json", help="extra request body JSON (e.g. reasoning_effort)")
    p_prof.add_argument("--out", required=True, help="output receipt JSON path")

    p_scan = sub.add_parser("scan-trace", help="audit a saved reasoning trace for overthinking loops")
    p_scan.add_argument("trace", help="path to raw text or JSON response trace")
    p_scan.add_argument("--ngram", type=int, default=8, help="word n-gram size for cycle detection (default: 8)")
    p_scan.add_argument("--repeats", type=int, default=3, help="non-overlapping repeats marking a loop (default: 3)")
    p_scan.add_argument("--json", dest="json_out", help="write JSON audit")

    p_tune = sub.add_parser("tune", help="compute Pareto-optimal per-tier reasoning policy from effort receipts")
    p_tune.add_argument("receipts", nargs="*", help="paths to receipt JSON files")
    p_tune.add_argument("--dir", help="directory of receipt JSON files")
    p_tune.add_argument("--min-trivial-acc", type=float, default=100.0)
    p_tune.add_argument("--min-medium-acc", type=float, default=90.0)
    p_tune.add_argument("--min-hard-acc", type=float, default=85.0)
    p_tune.add_argument("--min-trap-acc", type=float, default=90.0)
    p_tune.add_argument("--policy-out", help="write dynamic effort routing policy JSON")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "generate":
            return cmd_generate(args)
        if args.cmd == "profile":
            return cmd_profile(args)
        if args.cmd == "scan-trace":
            return cmd_scan_trace(args)
        if args.cmd == "tune":
            return cmd_tune(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"think_governor.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
