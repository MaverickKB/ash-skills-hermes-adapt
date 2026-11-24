#!/usr/bin/env python3
"""Audit and tune local LLM servers for agentic coding harnesses.

Diagnoses the silent infrastructure faults that break local coding models:
mangled Jinja chat templates, leaked XML/JSON tool calls, dropped parallel tool
invocations, busted KV prefix caches across multi-turn edits, and bad sampling
or speculative settings. Uses only the Python 3.9+ standard library.

Subcommands:
  lint-template  Inspect a .jinja, tokenizer_config.json or binary .gguf chat
                 template for tool, history, reasoning and cache-busting faults.
  probe-tools    Run live single-call, parallel-call and tool-result round-trip
                 probes against any OpenAI-compatible server.
  probe-cache    Measure cold vs warm multi-turn KV prefix cache hits plus a
                 deliberate header-perturbation control.
  recommend      Print tuned server, sampling, prefix-cache and speculative
                 code-edit flags for a model family, engine and coding harness.

Exit codes: 0 clean/pass, 1 fault/failure detected, 2 usage/endpoint error,
            3 inconclusive cache control.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

USER_AGENT = "local-coder-overclock/1"

TOOL_LEAK_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("qwen_xml", re.compile(r"<tool_call>.*?</tool_call>|<tool_call>", re.S | re.I)),
    ("mistral_bracket", re.compile(r"\[TOOL_CALLS\]", re.I)),
    ("llama_python_tag", re.compile(r"<\|python_tag\|>", re.I)),
    ("deepseek_marker", re.compile(r"tool▁calls▁begin|<｜tool▁call", re.I)),
    ("glm_xml", re.compile(r"<tool_call>\s*[a-zA-Z0-9_]+\s*<arg_key>", re.S | re.I)),
    ("raw_json_call", re.compile(r'^\s*\{\s*"name"\s*:\s*"[a-zA-Z0-9_]+"', re.S)),
]
STOP_LEAK_RE = re.compile(r"(<\|im_end\|>|<\|eot_id\|>|<\|endoftext\|>|</s>|<\|end_of_turn\|>)")
THINK_LEAK_RE = re.compile(r"<think>|</think>", re.I)

SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


class Fail(Exception):
    """Usage, file or endpoint error -> exit 2."""


# --------------------------------------------------- stdlib GGUF template read

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


def extract_gguf_chat_template(path: Path) -> Tuple[str, Optional[bool]]:
    with path.open("rb") as fh:
        if fh.read(4) != b"GGUF":
            raise Fail(f"{path}: not a GGUF file")
        ver = struct.unpack("<I", fh.read(4))[0]
        if ver not in (2, 3):
            raise Fail(f"{path}: unsupported GGUF version {ver}")
        _tensors = struct.unpack("<Q", fh.read(8))[0]
        kv_count = struct.unpack("<Q", fh.read(8))[0]
        template = None
        add_bos = None
        for _ in range(kv_count):
            klen = struct.unpack("<Q", fh.read(8))[0]
            key = fh.read(klen).decode("utf-8", errors="replace")
            vtype = struct.unpack("<I", fh.read(4))[0]
            if key == "tokenizer.chat_template" and vtype == 8:
                vlen = struct.unpack("<Q", fh.read(8))[0]
                template = fh.read(vlen).decode("utf-8", errors="replace")
            elif key == "tokenizer.ggml.add_bos_token" and vtype == 7:
                add_bos = struct.unpack("<?", fh.read(1))[0]
            else:
                _skip_gguf_val(fh, vtype)
    if template is None:
        raise Fail(f"{path}: no tokenizer.chat_template metadata entry found in GGUF")
    return template, add_bos


def load_template_source(path: Path) -> Tuple[str, Optional[bool]]:
    if not path.is_file():
        raise Fail(f"file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".gguf":
        return extract_gguf_chat_template(path)
    if suffix == ".json":
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "chat_template" not in doc:
            raise Fail(f"{path}: missing 'chat_template' field")
        tpl = doc["chat_template"]
        if isinstance(tpl, list):
            default = next((item.get("template") for item in tpl if isinstance(item, dict) and item.get("name") == "default"), None)
            tpl = default or (tpl[0].get("template") if tpl and isinstance(tpl[0], dict) else "")
        if not isinstance(tpl, str) or not tpl.strip():
            raise Fail(f"{path}: empty chat_template")
        add_bos = doc.get("add_bos_token") if isinstance(doc.get("add_bos_token"), bool) else None
        return tpl, add_bos
    return path.read_text(encoding="utf-8"), None


# ----------------------------------------------------------- template linting

def lint_chat_template(template: str, add_bos_token: Optional[bool] = None) -> Dict[str, object]:
    findings: List[Dict[str, str]] = []

    def add(severity: str, code: str, message: str, fix: str) -> None:
        findings.append({"severity": severity, "code": code, "message": message, "fix": fix})

    if re.search(r"\b(strftime_now|now\s*\(|date_string|today\s*\(|datetime)\b", template):
        add(
            "ERROR",
            "DYNAMIC_DATE_CACHE_BUST",
            "Template injects a dynamic date or timestamp into the rendered prompt, invalidating KV prefix caches across sessions or turns.",
            "Remove date injection from the chat template or pin a static system header.",
        )

    if not re.search(r"\btools\b", template):
        add(
            "ERROR",
            "NO_TOOLS_SUPPORT",
            "Template never references `tools`; tool schemas sent by coding agents will be silently dropped.",
            "Load a tool-capable Jinja template (`--chat-template-file`) matching the model family.",
        )

    if not re.search(r"['\"]tool['\"]|tool_response|tool_result", template):
        add(
            "ERROR",
            "NO_TOOL_ROLE_HANDLER",
            "Template has no handler for `role == 'tool'` messages; multi-turn tool outputs will be lost or misformatted.",
            "Add a `message['role'] == 'tool'` branch wrapping tool outputs in the model's native response tags.",
        )

    if not re.search(r"tool_calls", template):
        add(
            "ERROR",
            "DROPS_TOOL_CALLS_HISTORY",
            "Template never renders `message.tool_calls` in assistant history; the model forgets prior calls and enters edit loops.",
            "Render `message['tool_calls']` inside the assistant turn block.",
        )

    if "add_generation_prompt" not in template:
        add(
            "WARN",
            "NO_GENERATION_PROMPT",
            "Template never checks `add_generation_prompt`; assistant turn framing can break on continuation calls.",
            "Add an `{% if add_generation_prompt %}` block emitting the assistant start marker.",
        )

    if not re.search(r"['\"]system['\"]", template):
        add(
            "WARN",
            "MISSING_SYSTEM_ROLE",
            "Template has no explicit `system` role branch; coding-harness rules may be dropped or merged unpredictably.",
            "Add a dedicated `system` turn handler at the start of the conversation.",
        )

    if re.search(r"reasoning_content|<think>", template):
        strips_old = bool(re.search(r"loop\.index|last_query_index|ns\.last_user|loop\.last", template))
        if not strips_old:
            add(
                "WARN",
                "REASONING_HISTORY_LEAK",
                "Template handles reasoning/think tags without detecting prior-turn boundaries; keeping old thinking blocks bloats context and shifts prefix tokens.",
                "Strip `reasoning_content` from assistant turns prior to the latest user message.",
            )

    if add_bos_token is True and re.search(r"bos_token|<\|begin_of_text\|>|<s>", template):
        add(
            "WARN",
            "DOUBLE_BOS_RISK",
            "`add_bos_token` is true while the template also emits BOS explicitly; double BOS degrades tool-call accuracy.",
            "Set `add_bos_token: false` or rely on the server tokenizer without duplicating BOS in the template.",
        )

    # Check Jinja block balance ({% if %} / {% endif %}, {% for %} / {% endfor %}).
    if_opens = len(re.findall(r"\{%-?\s*if\b", template))
    if_closes = len(re.findall(r"\{%-?\s*endif\b", template))
    for_opens = len(re.findall(r"\{%-?\s*for\b", template))
    for_closes = len(re.findall(r"\{%-?\s*endfor\b", template))
    if if_opens != if_closes or for_opens != for_closes:
        add(
            "ERROR",
            "UNBALANCED_JINJA_BLOCKS",
            f"Unbalanced Jinja tags (if={if_opens}/endif={if_closes}, for={for_opens}/endfor={for_closes}).",
            "Fix mismatched Jinja control tags before serving.",
        )

    errors = sum(1 for f in findings if f["severity"] == "ERROR")
    warns = sum(1 for f in findings if f["severity"] == "WARN")
    return {
        "clean": errors == 0 and warns == 0,
        "errors": errors,
        "warnings": warns,
        "template_chars": len(template),
        "findings": findings,
    }


# ----------------------------------------------------------- OpenAI HTTP client

def post_chat(
    base_url: str,
    model: str,
    messages: Sequence[Dict[str, object]],
    tools: Optional[Sequence[Dict[str, object]]] = None,
    max_tokens: int = 256,
    temperature: float = 0.0,
    timeout: float = 120.0,
    api_key: Optional[str] = None,
) -> Dict[str, object]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload: Dict[str, object] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "seed": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = list(tools)
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Fail(f"POST {url} failed: {exc}") from exc
    wall_ms = (time.perf_counter() - t0) * 1000.0
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Fail(f"invalid JSON from {url}: {exc}") from exc
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise Fail(f"missing choices from {url}")
    msg = choices[0].get("message") or {}
    usage = body.get("usage") or {}
    timings = body.get("timings") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or (int(timings.get("cache_n") or 0) + int(timings.get("prompt_n") or 0)))
    cached_tokens = int(
        (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        or timings.get("cache_n")
        or 0
    )
    prompt_ms = float(timings.get("prompt_ms") or wall_ms)
    return {
        "content": msg.get("content") or "",
        "reasoning_content": msg.get("reasoning_content") or "",
        "tool_calls": msg.get("tool_calls") or [],
        "finish_reason": choices[0].get("finish_reason"),
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached_tokens,
        "prompt_ms": round(prompt_ms, 2),
        "wall_ms": round(wall_ms, 2),
    }


# ------------------------------------------------------------------ live probes

CODING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a line slice from a repository file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Search repository files for an exact symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "directory": {"type": "string"},
                },
                "required": ["query", "directory"],
            },
        },
    },
]


def detect_tool_leak(text: str) -> Optional[str]:
    for label, pat in TOOL_LEAK_PATTERNS:
        if pat.search(text):
            return label
    return None


def cmd_probe_tools(args: argparse.Namespace) -> int:
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    checks: List[Dict[str, object]] = []

    # 1. Single coding tool call
    r1 = post_chat(
        args.url,
        args.model,
        [{"role": "user", "content": "Read lines 10 to 40 of src/server.py using read_file."}],
        tools=CODING_TOOLS,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        api_key=api_key,
    )
    leak1 = detect_tool_leak(r1["content"])
    stop1 = bool(STOP_LEAK_RE.search(r1["content"]))
    think1 = bool(THINK_LEAK_RE.search(r1["content"]))
    ok1 = False
    reason1 = ""
    if r1["tool_calls"]:
        fn = (r1["tool_calls"][0].get("function") or {})
        try:
            a = json.loads(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else fn.get("arguments")
            if fn.get("name") == "read_file" and a.get("path") == "src/server.py" and a.get("start_line") == 10 and a.get("end_line") == 40:
                ok1 = True
                reason1 = "parsed cleanly into tool_calls"
            else:
                reason1 = f"wrong tool or args: {fn.get('name')} {a}"
        except json.JSONDecodeError as exc:
            reason1 = f"invalid JSON in tool_calls[0].arguments: {exc}"
    else:
        reason1 = f"no structured tool_calls (leaked={leak1 or 'none'})"
    checks.append({"probe": "single_tool_call", "passed": ok1 and not stop1, "reason": reason1, "leaked_syntax": leak1, "stop_leak": stop1, "think_leak": think1})

    # 2. Parallel tool calls
    r2 = post_chat(
        args.url,
        args.model,
        [
            {
                "role": "user",
                "content": (
                    "Issue two tool calls in parallel right now: "
                    "(1) read_file on src/config.py lines 1 to 25, and "
                    "(2) grep_search for 'DEFAULT_PORT' in directory 'src'."
                ),
            }
        ],
        tools=CODING_TOOLS,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        api_key=api_key,
    )
    names2 = [((tc.get("function") or {}).get("name")) for tc in r2["tool_calls"]]
    ok2 = "read_file" in names2 and "grep_search" in names2
    reason2 = f"returned {len(names2)} parallel call(s): {names2}" if ok2 else f"expected read_file + grep_search, got {names2} (leaked={detect_tool_leak(r2['content']) or 'none'})"
    checks.append({"probe": "parallel_tool_calls", "passed": ok2, "reason": reason2})

    # 3. Multi-turn role=tool consumption (catches infinite re-call loops)
    msgs3 = [
        {"role": "user", "content": "Check src/config.py lines 1 to 5 and tell me the value of DEFAULT_PORT."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_cfg_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "src/config.py", "start_line": 1, "end_line": 5})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_cfg_1", "content": "1: # config\n2: DEFAULT_PORT = 9417\n"},
    ]
    r3 = post_chat(args.url, args.model, msgs3, tools=CODING_TOOLS, max_tokens=args.max_tokens, timeout=args.timeout, api_key=api_key)
    stop3 = bool(STOP_LEAK_RE.search(r3["content"]))
    think3 = bool(THINK_LEAK_RE.search(r3["content"]))
    ok3 = ("9417" in r3["content"]) and not r3["tool_calls"] and not stop3
    if r3["tool_calls"]:
        reason3 = "model ignored tool result and re-called a tool (loop risk)"
    elif "9417" not in r3["content"]:
        reason3 = f"answer did not report tool result 9417 (content={r3['content'][:80]!r})"
    elif stop3:
        reason3 = "stop token leaked into assistant reply"
    else:
        reason3 = "consumed tool result cleanly"
    checks.append({"probe": "tool_roundtrip", "passed": ok3, "reason": reason3, "think_leak": think3, "stop_leak": stop3})

    passed = sum(1 for c in checks if c["passed"])
    report = {"url": args.url, "model": args.model, "passed": passed, "total": len(checks), "checks": checks}
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for c in checks:
        status = "PASS" if c["passed"] else "FAIL"
        print(f"  [{status}] {c['probe']:<22} {c['reason']}")
        if c.get("think_leak"):
            print("         note: raw <think> tags leaked into content; configure --reasoning-format or reasoning parser")
    verdict = "TOOLS: PASS" if passed == len(checks) else f"TOOLS: FAIL ({passed}/{len(checks)} passed)"
    print(verdict)
    return 0 if passed == len(checks) else 1


def cmd_probe_cache(args: argparse.Namespace) -> int:
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    # Build a deterministic multi-KB coding system prompt + synthetic source file.
    pad_lines = [
        f"def handler_{i:03d}(ctx: dict, payload: int) -> int: return (payload * {(i % 13) + 3} + {i}) % 997"
        for i in range(max(40, args.prefix_chars // 90))
    ]
    sys_prefix = (
        "You are a terse local coding assistant. Answer with one short line.\n"
        "Repository snapshot (read-only context):\n```python\n" + "\n".join(pad_lines) + "\n```"
    )
    t1_msgs = [
        {"role": "system", "content": sys_prefix},
        {"role": "user", "content": "What modulo constant do all handlers use? Reply with the integer only."},
    ]
    r1 = post_chat(args.url, args.model, t1_msgs, tools=CODING_TOOLS, max_tokens=24, timeout=args.timeout, api_key=api_key)

    t2_msgs = t1_msgs + [
        {"role": "assistant", "content": r1["content"] or "997"},
        {"role": "user", "content": "What is the parameter name for the dict argument in handler_001?"},
    ]
    r2 = post_chat(args.url, args.model, t2_msgs, tools=CODING_TOOLS, max_tokens=24, timeout=args.timeout, api_key=api_key)

    t3_msgs = t2_msgs + [
        {"role": "assistant", "content": r2["content"] or "ctx"},
        {"role": "user", "content": "What return type annotation do the handlers declare?"},
    ]
    r3 = post_chat(args.url, args.model, t3_msgs, tools=CODING_TOOLS, max_tokens=24, timeout=args.timeout, api_key=api_key)

    # Deliberate prefix-bust control: perturb the very first line of system prompt.
    busted_msgs = [
        {"role": "system", "content": "SESSION_NONCE=994182\n" + sys_prefix},
        {"role": "user", "content": "What modulo constant do all handlers use? Reply with the integer only."},
    ]
    r_bust = post_chat(args.url, args.model, busted_msgs, tools=CODING_TOOLS, max_tokens=24, timeout=args.timeout, api_key=api_key)

    def ratio(r: Dict[str, object]) -> float:
        pt = int(r["prompt_tokens"])
        return round(int(r["cached_tokens"]) / pt, 3) if pt > 0 else 0.0

    warm_cached_ratio = min(ratio(r2), ratio(r3))
    bust_cached_ratio = ratio(r_bust)
    cold_ms = float(r1["prompt_ms"])
    warm_ms = max(float(r2["prompt_ms"]), float(r3["prompt_ms"]))
    bust_ms = float(r_bust["prompt_ms"])
    has_token_counters = (r2["cached_tokens"] > 0 or r3["cached_tokens"] > 0)

    if has_token_counters:
        healthy = warm_cached_ratio >= 0.70 and bust_cached_ratio < warm_cached_ratio * 0.5
        control_separated = bust_cached_ratio < warm_cached_ratio
    else:
        healthy = cold_ms > 0 and warm_ms <= cold_ms * 0.55 and bust_ms >= warm_ms * 1.35
        control_separated = bust_ms > warm_ms * 1.15

    if healthy:
        verdict, code = "CACHE: HEALTHY", 0
    elif not has_token_counters and not control_separated and cold_ms < 150.0:
        verdict, code = "CACHE: INCONCLUSIVE (prefix too small to separate cold vs warm timing; raise --prefix-chars)", 3
    else:
        verdict, code = "CACHE: BUSTED", 1

    report = {
        "turns": [
            {"turn": "t1_cold", "prompt_tokens": r1["prompt_tokens"], "cached_tokens": r1["cached_tokens"], "cached_ratio": ratio(r1), "prompt_ms": cold_ms},
            {"turn": "t2_warm", "prompt_tokens": r2["prompt_tokens"], "cached_tokens": r2["cached_tokens"], "cached_ratio": ratio(r2), "prompt_ms": r2["prompt_ms"]},
            {"turn": "t3_warm", "prompt_tokens": r3["prompt_tokens"], "cached_tokens": r3["cached_tokens"], "cached_ratio": ratio(r3), "prompt_ms": r3["prompt_ms"]},
            {"turn": "t4_bust_control", "prompt_tokens": r_bust["prompt_tokens"], "cached_tokens": r_bust["cached_tokens"], "cached_ratio": bust_cached_ratio, "prompt_ms": bust_ms},
        ],
        "verdict": verdict,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for t in report["turns"]:
        print(
            f"  {t['turn']:<16} prompt_toks={t['prompt_tokens']:>5}  cached={t['cached_tokens']:>5} "
            f"({t['cached_ratio']*100:>5.1f}%)  prompt_ms={t['prompt_ms']:>7.1f}"
        )
    print(verdict)
    return code


# ----------------------------------------------------------- recipe recommender

FAMILY_PRESETS: Dict[str, Dict[str, object]] = {
    "qwen3": {
        "arch": "dense_or_moe",
        "sampling": "temperature=0.6, top_p=0.95, top_k=20, min_p=0.0 (non-thinking: temp=0.7, top_p=0.8)",
        "tensorfold_flags": "--context 65536 --kv-cache bf16 --prompt-cache-gib 8 --loop-guard --thinking --thinking-budget 4096",
        "llama_flags": "--jinja --reasoning-format deepseek --cache-reuse 256 --slot-prompt-similarity 0.5",
        "vllm_flags": "--enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3 --enable-prefix-caching",
        "sglang_flags": "--tool-call-parser qwen25 --reasoning-parser qwen3",
        "spec_note": "On dense checkpoints, prompt-lookup / drafter acceleration speeds up file edits; on routed-expert MoE checkpoints, keep draft windows narrow so extra rows do not fan out routed experts.",
    },
    "qwen2.5-coder": {
        "arch": "dense",
        "sampling": "temperature=0.2, top_p=0.9, min_p=0.05, repeat_penalty=1.05",
        "tensorfold_flags": "--context 65536 --kv-cache bf16 --prompt-cache-gib 8 --loop-guard",
        "llama_flags": "--jinja --cache-reuse 256 --slot-prompt-similarity 0.5 --lookup-cache-dynamic lookup.bin",
        "vllm_flags": '--enable-auto-tool-choice --tool-call-parser hermes --enable-prefix-caching --speculative-config \'{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_max":4}\'',
        "sglang_flags": "--tool-call-parser qwen25",
        "spec_note": "Dense architecture: native drafting (`--drafter`) or n-gram prompt lookup yields strong lossless gains on code edits.",
    },
    "deepseek-v3": {
        "arch": "moe",
        "sampling": "temperature=0.3, top_p=0.95 (coding), min_p=0.02",
        "tensorfold_flags": "--context 65536 --kv-cache bf16 --prompt-cache-gib 16 --loop-guard",
        "llama_flags": "--jinja --cache-reuse 256 --slot-prompt-similarity 0.5",
        "vllm_flags": "--enable-auto-tool-choice --tool-call-parser deepseek_v3 --enable-prefix-caching",
        "sglang_flags": "--tool-call-parser deepseekv3",
        "spec_note": "Large MoE with native MTP heads: prefer native MTP drafting over bushy trees; wide speculative trees inflate routed-expert bandwidth.",
    },
    "deepseek-r1": {
        "arch": "moe",
        "sampling": "temperature=0.6, top_p=0.95 (avoid temp=0.0 loops)",
        "tensorfold_flags": "--context 65536 --kv-cache bf16 --prompt-cache-gib 16 --loop-guard --thinking",
        "llama_flags": "--jinja --reasoning-format deepseek --cache-reuse 256 --slot-prompt-similarity 0.5",
        "vllm_flags": "--enable-auto-tool-choice --tool-call-parser deepseek_v3 --reasoning-parser deepseek_r1 --enable-prefix-caching",
        "sglang_flags": "--tool-call-parser deepseekv3 --reasoning-parser deepseek-r1",
        "spec_note": "Strip prior-turn reasoning_content in the chat template so coding loops keep prefix cache hits.",
    },
    "glm4": {
        "arch": "dense_or_moe",
        "sampling": "temperature=0.6, top_p=0.95 (code edits: temp=0.3)",
        "tensorfold_flags": "--context 65536 --kv-cache bf16 --prompt-cache-gib 8 --loop-guard --thinking",
        "llama_flags": "--jinja --reasoning-format deepseek --cache-reuse 256 --slot-prompt-similarity 0.5",
        "vllm_flags": "--enable-auto-tool-choice --tool-call-parser glm45 --reasoning-parser glm45 --enable-prefix-caching",
        "sglang_flags": "--tool-call-parser glm45 --reasoning-parser glm45",
        "spec_note": "Verify XML/arg_key tool parser matches checkpoint revision with probe-tools before wiring into Claude Code or OpenCode.",
    },
    "devstral": {
        "arch": "dense",
        "sampling": "temperature=0.15, top_p=0.95",
        "tensorfold_flags": "--context 65536 --kv-cache bf16 --prompt-cache-gib 8 --loop-guard",
        "llama_flags": "--jinja --cache-reuse 256 --slot-prompt-similarity 0.5 --lookup-cache-dynamic lookup.bin",
        "vllm_flags": '--enable-auto-tool-choice --tool-call-parser mistral --enable-prefix-caching --speculative-config \'{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_max":4}\'',
        "sglang_flags": "--tool-call-parser mistral",
        "spec_note": "Mistral/Devstral [TOOL_CALLS] parser is sensitive to template version; confirm parallel calls via probe-tools.",
    },
    "llama3": {
        "arch": "dense",
        "sampling": "temperature=0.2, top_p=0.9, min_p=0.05",
        "tensorfold_flags": "--context 65536 --kv-cache bf16 --prompt-cache-gib 8 --loop-guard",
        "llama_flags": "--jinja --cache-reuse 256 --slot-prompt-similarity 0.5",
        "vllm_flags": "--enable-auto-tool-choice --tool-call-parser llama3_json --enable-prefix-caching",
        "sglang_flags": "--tool-call-parser llama3",
        "spec_note": "Watch for `strftime_now` date strings in stock Llama 3 templates that bust prefix caching every day.",
    },
    "gemma3": {
        "arch": "dense",
        "sampling": "temperature=0.4, top_p=0.95, top_k=64",
        "tensorfold_flags": "--context 65536 --kv-cache bf16 --prompt-cache-gib 8 --loop-guard",
        "llama_flags": "--jinja --cache-reuse 256 --slot-prompt-similarity 0.5",
        "vllm_flags": "--enable-auto-tool-choice --tool-call-parser pythonic --enable-prefix-caching",
        "sglang_flags": "--tool-call-parser pythonic",
        "spec_note": "Gemma uses hybrid sliding-window + global attention; confirm your engine keeps sliding-window KV bounded.",
    },
}

HARNESS_TIPS: Dict[str, str] = {
    "claude-code": "Route via Anthropic-compatible server endpoint (`/v1/messages` in recent llama-server/vLLM) or thin translation proxy; pin tool order and disable dynamic per-turn headers to preserve prefix cache.",
    "opencode": "Configure custom OpenAI-compatible provider with explicit `context` and `output` limits matching `-c`; confirm parallel tool calls pass `probe-tools`.",
    "cline": "Disable prompt-caching headers only if your local proxy rejects them; use unified diff edit mode rather than full-file rewrites on sub-32B local models.",
    "aider": "Use `--edit-format diff` (or `udiff`) with low temperature (0.15-0.25); pair with n-gram lookup drafting for fast search/replace blocks.",
    "omp": "Point custom provider at local `/v1`; verify `reasoning_content` separation so thinking tokens never leak into tool JSON.",
    "codex": "Set OpenAI base URL to local `/v1`, pin model ID, and verify `probe-tools` passes parallel + error-recovery turns.",
}


def cmd_lint_template(args: argparse.Namespace) -> int:
    tpl, add_bos = load_template_source(Path(args.path))
    res = lint_chat_template(tpl, add_bos_token=add_bos)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(f"template: {args.path} ({res['template_chars']} chars)")
    for f in res["findings"]:
        print(f"  [{f['severity']}] {f['code']}: {f['message']}")
        print(f"           fix: {f['fix']}")
    if res["clean"]:
        print("TEMPLATE: CLEAN")
        return 0
    print(f"TEMPLATE: ISSUES ({res['errors']} errors, {res['warnings']} warnings)")
    return 1


def cmd_recommend(args: argparse.Namespace) -> int:
    preset = FAMILY_PRESETS[args.family]
    engine_key = {
        "tensorfold": "tensorfold_flags",
        "llama-server": "llama_flags",
        "vllm": "vllm_flags",
        "sglang": "sglang_flags",
    }.get(args.engine, "tensorfold_flags")
    out = {
        "family": args.family,
        "engine": args.engine,
        "harness": args.harness,
        "architecture": preset["arch"],
        "sampling": preset["sampling"],
        "server_flags": preset[engine_key],
        "speculative_note": preset["spec_note"],
        "harness_tip": HARNESS_TIPS[args.harness],
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"family:   {out['family']} ({out['architecture']})")
    print(f"engine:   {out['engine']}")
    print(f"flags:    {out['server_flags']}")
    print(f"sampling: {out['sampling']}")
    print(f"drafting: {out['speculative_note']}")
    print(f"harness ({out['harness']}): {out['harness_tip']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_lint = sub.add_parser("lint-template", help="lint a .jinja, tokenizer_config.json or .gguf chat template")
    p_lint.add_argument("path", help="path to .jinja, tokenizer_config.json or .gguf file")
    p_lint.add_argument("--json", dest="json_out", help="write JSON findings")

    p_tools = sub.add_parser("probe-tools", help="probe single, parallel and multi-turn tool calling on a live server")
    p_tools.add_argument("--url", required=True, help="OpenAI-compatible base URL (e.g. http://127.0.0.1:8080/v1)")
    p_tools.add_argument("--model", required=True, help="model ID")
    p_tools.add_argument("--max-tokens", type=int, default=256)
    p_tools.add_argument("--timeout", type=float, default=120.0)
    p_tools.add_argument("--api-key-env", help="env var holding Bearer token")
    p_tools.add_argument("--json", dest="json_out", help="write JSON probe result")

    p_cache = sub.add_parser("probe-cache", help="measure cold vs warm multi-turn KV prefix cache reuse + bust control")
    p_cache.add_argument("--url", required=True, help="OpenAI-compatible base URL")
    p_cache.add_argument("--model", required=True, help="model ID")
    p_cache.add_argument("--prefix-chars", type=int, default=12000, help="synthetic repo prefix size in chars (default: 12000)")
    p_cache.add_argument("--timeout", type=float, default=120.0)
    p_cache.add_argument("--api-key-env", help="env var holding Bearer token")
    p_cache.add_argument("--json", dest="json_out", help="write JSON cache probe result")

    p_rec = sub.add_parser("recommend", help="print tuned flags, sampling and speculative advice for a local coding stack")
    p_rec.add_argument("--family", required=True, choices=tuple(FAMILY_PRESETS))
    p_rec.add_argument("--engine", default="tensorfold", choices=("tensorfold", "llama-server", "vllm", "sglang"))
    p_rec.add_argument("--harness", default="opencode", choices=tuple(HARNESS_TIPS))
    p_rec.add_argument("--json", dest="json_out", help="write JSON recommendation")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "lint-template":
            return cmd_lint_template(args)
        if args.cmd == "probe-tools":
            return cmd_probe_tools(args)
        if args.cmd == "probe-cache":
            return cmd_probe_cache(args)
        if args.cmd == "recommend":
            return cmd_recommend(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"coder_audit.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
