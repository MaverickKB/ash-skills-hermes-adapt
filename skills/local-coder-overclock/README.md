# Local coder overclock

Fix broken Jinja templates, leaked tool calls, busted KV prefix caches and slow code-edit loops on local coding servers.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You are wiring a local model (Qwen, DeepSeek, GLM, Devstral, Llama, Gemma) into Claude Code, Hermes Agent, OpenCode, Cline, Aider, Codex or OMP and want reliable tool calling, hot prefix caches and fast edits.

## Try it

```text
Use local-coder-overclock. Lint the GGUF chat template, probe live parallel tool calling and KV prefix caching on my local server, and give me the tuned flags for OpenCode.
```

## What you get

Use the [local coding server auditor](scripts/coder_audit.py) for GGUF/Jinja template linting, live tool-call and prefix-cache probes and stack recipes, backed by [template and harness fixes](references/templates-and-harnesses.md).

## Pair it with

[Quant autopsy](../quant-autopsy/README.md) · [VRAM alchemist](../vram-alchemist/README.md) · [One-stream lanes](../one-stream-lanes/README.md) · [Output parity](../output-parity/README.md)
