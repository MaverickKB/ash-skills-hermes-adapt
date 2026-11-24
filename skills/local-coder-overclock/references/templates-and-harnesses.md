# Chat templates, tool parsers, prefix caching and coding harnesses

Reference fixes for `local-coder-overclock` when tuning local models for coding agents.

## Contents

1. Why local coding models fail inside agent harnesses
2. Chat template patches that fix tool calls and prefix caches
3. Engine tool-call and reasoning parser matrix
4. Keeping KV prefix cache hit rates above 90%
5. Speculative and prompt-lookup drafting for code edits
6. Harness-specific settings

## 1. Why local coding models fail inside agent harnesses

Most complaints that a 30B to 120B local model "cannot code in an agent loop" come from four plumbing bugs rather than model weights:

1. **Unparsed tool tags.** The server omits `--jinja` or selects the wrong `--tool-call-parser`, so `<tool_call>` JSON arrives inside plain `content` text. The harness sees no tool invocation and stalls or loops.
2. **Prefix cache invalidation.** A dynamic timestamp in the Jinja template, unstable tool schema ordering, or tiny `--parallel` slot eviction forces a 20K-token cold prefill on every single tool turn.
3. **Thinking-history bloat.** Leaving previous turns' `<think>...</think>` reasoning inside multi-turn message history eats context fast and breaks prefix alignment whenever the harness strips or reformats reasoning blocks.
4. **Looping sampler settings.** Greedy `temperature=0.0` combined with no mild repetition guard on thinking models (or aggressive `repeat_penalty > 1.15` on code syntax with repeated braces and indentation) triggers infinite retry loops or syntax corruption.

## 2. Chat template patches that fix tool calls and prefix caches

Always run `coder_audit.py lint-template` on the model's `.gguf`, `tokenizer_config.json` or `.jinja` file before serving.

- **Remove dynamic dates:** Replace `strftime_now(...)` or `Cutting Knowledge Date / Today Date: {{ date_string }}` in stock Llama/Mistral templates with a fixed string so identical coding sessions reuse KV pages.
- **Keep tool calls in history.** Serialize `message['tool_calls']` inside assistant turns using the model's exact tool-call syntax; dropping them causes the model to call `read_file` on the same file repeatedly.
- **Strip old reasoning blocks cleanly:** Render `reasoning_content` only after the last user message (the active turn) unless the model card explicitly requires multi-turn interleaved thinking, and make sure the server splits reasoning into `message.reasoning_content` rather than leaking raw `<think>` tags into `content`.
- **Avoid double BOS:** When `tokenizer.ggml.add_bos_token` is `true`, do not hardcode `<s>` or `<|begin_of_text|>` inside the Jinja template.

## 3. Engine tool-call and reasoning parser matrix

| Model family | `llama-server` flags | `vllm serve` flags | `sglang` flags |
| --- | --- | --- | --- |
| Qwen3 | `--jinja --reasoning-format deepseek` | `--enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3` | `--tool-call-parser qwen25 --reasoning-parser qwen3` |
| Qwen2.5-Coder | `--jinja` | `--enable-auto-tool-choice --tool-call-parser hermes` | `--tool-call-parser qwen25` |
| DeepSeek V3 / R1 | `--jinja --reasoning-format deepseek` | `--enable-auto-tool-choice --tool-call-parser deepseek_v3 --reasoning-parser deepseek_r1` | `--tool-call-parser deepseekv3 --reasoning-parser deepseek-r1` |
| GLM-4.5 / 4.7 | `--jinja --reasoning-format deepseek` | `--enable-auto-tool-choice --tool-call-parser glm45 --reasoning-parser glm45` | `--tool-call-parser glm45 --reasoning-parser glm45` |
| Devstral / Mistral | `--jinja` | `--enable-auto-tool-choice --tool-call-parser mistral` | `--tool-call-parser mistral` |
| Llama 3.x | `--jinja` | `--enable-auto-tool-choice --tool-call-parser llama3_json` | `--tool-call-parser llama3` |

Always confirm the live server with `coder_audit.py probe-tools` after starting it. Parser flag names shift between engine releases, and a mismatched parser silently degrades parallel tool calling.

## 4. Keeping KV prefix cache hit rates above 90%

Coding agents re-send the exact same system prompt, tool definitions and earlier turns on every step. To avoid 10-to-30-second prefills on every edit:

- **`tensorfold serve`:** Allocate `--prompt-cache-gib` (and `--prompt-cache-disk` for persistent multi-session reuse), set `--loop-guard` to catch edit loops early, and pass `--chat-template FILE` whenever `lint-template` finds a stock template fault.
- **`llama-server`:** Enable `--cache-reuse 256` and `--slot-prompt-similarity 0.5`. Keep `-np` (slot count) matched to actual concurrent agents (`-np 1` or `-np 2` for solo coding), because `-c` is divided across slots and excess slots waste KV VRAM and cause slot thrashing.
- **`vllm serve`:** Pass `--enable-prefix-caching` and ensure `chunked_prefill` is active so long warm turns do not stall interactive latency.
- **Stable harness serialization:** Keep tool JSON keys and tool array order deterministic across turns; never inject per-turn wall-clock timestamps into the system prompt (put dynamic timestamps at the tail of the latest user message instead).
- **Verify with `probe-cache`:** Confirm Turn 2 and Turn 3 report `cached_ratio >= 70%` (or warm `prompt_ms <= 0.45 * cold_ms`) AND Turn 4 (the header-perturbation control) actually busts the cache.

## 5. Speculative and prompt-lookup drafting for code edits

Code edits repeat large spans of existing file content, identifiers, imports and JSON tool wrappers. That makes coding one of the highest-payoff workloads for exact lossless drafting:

- **Dense models (Qwen2.5-Coder, Devstral, Qwen3 dense):** Prompt-lookup / n-gram drafting (`--lookup-cache-dynamic` in `llama.cpp`, or vLLM `--speculative-config '{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_max":4}'`) or a same-tokenizer small draft model (`-md`) regularly gives 1.5x to 2.5x speedups on file edits with zero quality change.
- **Routed-expert MoE models (Qwen3-MoE, DeepSeek, GLM-MoE):** Be cautious with wide draft windows or bushy draft trees. Extra draft rows activate extra experts across layers and can drop passes/second faster than acceptance rises. Prefer native multi-token prediction (MTP) heads or narrow draft windows (`<= 4`), and check real served tokens/second before keeping the flag.
- **Measure honestly:** Check output parity (`output-parity`) and mirrored throughput (`fair-bench`) on actual edit prompts rather than trusting synthetic microbenchmarks.

## 6. Harness-specific settings

- **Claude Code / OpenCode / Cline / Roo / Codex / OMP:** Pin the client context cap below the server's per-slot `-c / -np` limit so the server never silently truncates the middle of the conversation or evicts the system prompt.
- **Diff format over full-file rewrites:** Prefer targeted search/replace or unified-diff edit tools on local models under 70B; full-file rewrites quadruple output tokens and amplify repetition errors.
- **Loop breaker:** If a local model fails the same test or tool call twice in a row, feed it the exact failing diff and stderr in a fresh concise turn (or escalate that single step to a stronger model) instead of letting it burn 40K tokens repeating the same edit.
