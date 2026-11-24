---
name: local-coder-overclock
description: Audits and fixes local LLM servers behind agentic coding harnesses (Claude Code, OpenCode, Cline, Roo, Aider, Codex, OMP). Lints Jinja, tokenizer_config.json and binary GGUF chat templates for tool-drop and cache-busting bugs, runs live single/parallel/multi-turn tool-call probes, measures KV prefix-cache hit rates with a deliberate bust control, and prescribes model-family parser, sampling and code-edit speculative flags. Use when a local coding model leaks raw tool XML, fails parallel tool calls, loops on edits, re-prefills slowly every turn, or needs tuning for an agentic coding setup.
metadata:
  version: "1.0.0"
---

# Local coder overclock

Most failures when replacing cloud coding APIs with a local model are plumbing faults, not weight limits: a broken Jinja template, a mismatched tool-call parser, a hidden timestamp busting the KV prefix cache every turn, or looping sampler settings. Audit the serving contract end to end, fix each silent failure, and verify the live endpoint passes before writing code with it.

## Ground rules

1. **Test the wire, not the config.** Never assume tool calling or prefix caching works because a flag is present. Run `probe-tools` and `probe-cache` against the live `/v1` endpoint.
2. **Keep prefix caches hot.** A 25K coding prompt that misses KV prefix cache on every agent turn turns a fast GPU into a crawl. Treat `DYNAMIC_DATE_CACHE_BUST` and unstable tool ordering as P0 bugs.
3. **Price speculative drafting by architecture.** Code edits are copy-heavy and reward n-gram / prompt-lookup or draft-model acceleration on dense models, while routed-expert MoE models punish wide draft windows. Verify net served speed with `fair-bench`.
4. **Separate reasoning from visible output.** On thinking models (Qwen3, DeepSeek R1, GLM-4.5), confirm `<think>` blocks stay in `reasoning_content` and never leak into tool arguments or diffs.

## Step 1: Lint the chat template (GGUF, JSON or Jinja)

Inspect the model's actual template before launching or patching flags. `lint-template` reads `.jinja`, `tokenizer_config.json`, or extracts `tokenizer.chat_template` directly from a local `.gguf` header:

```bash
python3 scripts/coder_audit.py lint-template /path/to/model.gguf
python3 scripts/coder_audit.py lint-template /path/to/tokenizer_config.json
```

It catches nine coding-agent killers: dynamic date/time injection (`DYNAMIC_DATE_CACHE_BUST`), missing `tools` block, missing `role == 'tool'` handler, dropped `tool_calls` in assistant history, missing `add_generation_prompt`, prior-turn reasoning bloat, double BOS risk, missing `system` branch and unbalanced Jinja control blocks.

Exit codes: `0` `TEMPLATE: CLEAN`, `1` `TEMPLATE: ISSUES`, `2` unreadable/invalid file.

## Step 2: Probe live tool calling and multi-turn recovery

Point `probe-tools` at the running server to test the exact interactions coding agents depend on:

```bash
python3 scripts/coder_audit.py probe-tools --url http://127.0.0.1:8080/v1 --model my-coder
```

It verifies:

1. **Single structured tool call** (`read_file` with exact path and line ints), flagging exact raw-tag leaks (`qwen_xml`, `mistral_bracket`, `llama_python_tag`, `deepseek_marker`, `glm_xml`, `raw_json_call`) and leaked stop tokens.
2. **Parallel tool calling** (`read_file` + `grep_search` in one turn), catching parsers that silently drop every call after the first.
3. **Multi-turn `role: "tool"` round-trip**, confirming the model reads the returned file contents instead of looping on the same tool call.

Exit codes: `0` `TOOLS: PASS`, `1` `TOOLS: FAIL`, `2` endpoint/usage error.

## Step 3: Verify KV prefix-cache hits (with bust control)

Run `probe-cache` to simulate a multi-turn coding session with a shared multi-KB system prompt, tool definitions and repository file context:

```bash
python3 scripts/coder_audit.py probe-cache --url http://127.0.0.1:8080/v1 --model my-coder --prefix-chars 16000
```

It sends Turn 1 (cold), Turn 2 and Turn 3 (warm extensions), and Turn 4 (deliberate 1-line header change control). It reads both OpenAI `prompt_tokens_details.cached_tokens` and `llama-server` `timings.cache_n` / `prompt_ms`.

- `CACHE: HEALTHY` (exit 0): warm turns hit >= 70% cached tokens (or cut prompt latency >= 45%) and Turn 4 drops back down as expected.
- `CACHE: BUSTED` (exit 1): warm turns are re-prefilling from scratch; fix server cache flags (`--cache-reuse`, `--slot-prompt-similarity`, `--enable-prefix-caching`), slot count (`-np`), or dynamic headers in the client/template.
- `CACHE: INCONCLUSIVE` (exit 3): prefix too short to separate cold from warm timing; rerun with a larger `--prefix-chars`.

## Step 4: Generate tuned server and harness flags

Use `recommend` to pull tested parser, reasoning, sampling, prefix-cache and speculative-drafting settings for your exact stack:

```bash
python3 scripts/coder_audit.py recommend --family qwen3 --engine tensorfold --harness opencode
```

Then verify exactness and speed before locking the config in:

1. Re-run `probe-tools` and `probe-cache` until both exit `0`.
2. If you added speculative or n-gram drafting, confirm token-for-token exactness with `output-parity` and mirrored throughput gain with `fair-bench`.
3. If tool calls still fail after the template and parser are clean, the quant itself may be degraded; run `quant-autopsy` across higher quants to check.

## Detailed patches and parser tables

Read `references/templates-and-harnesses.md` for Jinja patch patterns, the `llama-server` / `vLLM` / `SGLang` parser flag matrix, prefix-cache tuning rules, dense vs MoE code-edit drafting tradeoffs and per-harness configs.
