# Agent Skills

![A red laser traces the profile of a black basalt lion.](assets/mythos-mode.webp)

I want my agent to show me the bug it reproduced, the test that caught it, and the runs behind its speedup claim. These 38 skills give you a way to ask for that work.

[Install](#install) · [Try Mythos Mode](#see-mythos-mode-in-action) · [Pick a workflow](#pick-a-workflow) · [Browse the skills](#skill-directory) · [Supported agents](#supported-agents)

## Install

From your project, use the Skills CLI with Node.js 22.20 or later. [Skills CLI](https://github.com/vercel-labs/skills)

```bash
npx skills add ashhart/skills --skill mythos-mode
```

Replace `mythos-mode` with any name in the directory. For a manual install, copy the folder containing the skill's `SKILL.md` into [your agent's skills directory](#supported-agents).

## See Mythos Mode in action

> [!TIP]
> **When you want to go beast mode on something.**

```text
Use mythos-mode. This test passes locally and fails on CI.
Reproduce the failure, narrow down the cause, and show me the check that proves the fix.
```

You can see the reply gate reject an unsupported fix claim locally. Run this from a checkout on macOS or Linux.

```bash
printf '%s\n' 'Fixed. All tests pass.' | python3 skills/mythos-mode/scripts/gate.py /dev/stdin --work
```

The gate reports `GATE: FAIL (3 problems)`. It asks which checks ran, what remains unchecked, and whether a check caught the failure. [Mythos Mode guide](skills/mythos-mode/README.md)

## Pick a workflow

| What you're facing | Skills to use in order | What you get |
| --- | --- | --- |
| "Which stack should I build this on?" | [Research then build](skills/research-then-build/README.md) | Live sources, upstream code review, and a trial against your requirements. |
| "It only fails on CI." | [Diagnosing bugs](skills/diagnosing-bugs/README.md) &rarr; [Prove it](skills/prove-it/README.md) | A reproducer and a test that catches the original failure. |
| "This change touches a lot of callers." | [Blast radius](skills/blast-radius/README.md) &rarr; [Ship check](skills/ship-check/README.md) | A map of what depends on your changes and a verdict on the staged snapshot. |
| "Decode is faster. Can we ship it?" | [Output parity](skills/tensorfold/output-parity/README.md) &rarr; [Fair bench](skills/fair-bench/README.md) &rarr; [Speedup referee](skills/tensorfold/speedup-referee/README.md) | A parity verdict, repeat timings, and an independent review of the speedup claim. |
| "Push TensorFold speed locally without regressions." | [TensorFold speed hunt](skills/tensorfold/tensorfold-speed-hunt/README.md) ([Critical path X-ray](skills/tensorfold/critical-path-xray/README.md) &rarr; [Optimization ledger](skills/tensorfold/optimization-ledger/README.md) &rarr; [SASS and AIR diff](skills/tensorfold/sass-and-air-diff/README.md) &rarr; [Speedup referee](skills/tensorfold/speedup-referee/README.md)) | A tamper-checked 7-gate loop from served trace to fresh-session confirmed speedup and owed-port queue. |
| "Which quant fits my GPU and won't break coding agents?" | [VRAM alchemist](skills/tensorfold/vram-alchemist/README.md) &rarr; [Quant autopsy](skills/tensorfold/quant-autopsy/README.md) &rarr; [Local coder overclock](skills/local-coder-overclock/README.md) | Exact TensorFold VRAM fit and serve flags, an all-quant Pareto scorecard, and verified tool calling and prefix caching. |

## Skill directory

Open a guide for a copyable prompt, bundled tools, and related skills.

### Verification and review

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`mythos-mode`](skills/mythos-mode/README.md) | Check your agent's claims against its work. | When you want to go beast mode on something. |
| [`prove-it`](skills/prove-it/README.md) | Show that the same test fails on the baseline and passes with the fix. | A green test needs a meaningful failure case. |
| [`break-it`](skills/break-it/README.md) | Find failing inputs and mutations your tests miss. | You're about to rely on a diff or local CLI. |
| [`ship-check`](skills/ship-check/README.md) | Audit staged changes and run your chosen checks. | You're about to commit or merge. |
| [`fair-bench`](skills/fair-bench/README.md) | Compare commands against measured baseline noise. | A speedup needs more than one timing. |
| [`red-team`](skills/red-team/README.md) | Review approved scope and verify a hash-chained evidence log. | You need a documented defensive review. |
| [`deepfield`](skills/deepfield/README.md) | Read confidential agreements locally and tie every claim to cited evidence. | You need page coverage, linked context, and explicit gaps instead of a guessed answer. |

### Planning, building, and debugging

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`research-then-build`](skills/research-then-build/README.md) | Check live sources, inspect upstream code, and run the same acceptance tests on two options. | You need to pick a stack, dependency, or major upgrade. |
| [`repo-map`](skills/repo-map/README.md) | Find entry points, heavily imported files, and test commands. | You just opened an unfamiliar codebase. |
| [`spec-check`](skills/spec-check/README.md) | Turn vague requirements into checks and flag missing failure paths. | A feature or design needs testable acceptance criteria. |
| [`blast-radius`](skills/blast-radius/README.md) | Trace callers, aliases, re-exports, and affected tests. | You're removing or renaming a shared symbol. |
| [`diagnosing-bugs`](skills/diagnosing-bugs/README.md) | Reproduce a failure, shrink its input, and bisect the regression. | A test flakes or a bug only appears on some builds. |
| [`think-like-ash`](skills/think-like-ash/README.md) | Fan out causes across every layer, jump to the middle, and sweep the tiny details. | A fault has no obvious cause or a linear checklist keeps missing it. |
| [`migrate-safe`](skills/migrate-safe/README.md) | Lint SQL and verify schema and row data through a SQLite rollback. | You're changing a schema or backfilling data. |

For production design, TypeScript, and dependency choices, use the [Mythos coding playbook](skills/mythos-mode/references/coding.md).

### TensorFold and inference

If you need to find where a served request spends its time, start with the [TensorFold performance playbook](skills/tensorfold/README.md). Use the trace to choose which specialist to load.

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`tensorfold-speed-hunt`](skills/tensorfold/tensorfold-speed-hunt/README.md) | Drive the 7-gate closed-loop TensorFold speedup workflow with hashed receipts. | You're running an end-to-end local TensorFold optimization hunt. |
| [`critical-path-xray`](skills/tensorfold/critical-path-xray/README.md) | Parse served traces into GPU/host/idle shares, rooflines, and Amdahl bounds. | You need to find the real #1 bottleneck and verify kernel dispatch. |
| [`sass-and-air-diff`](skills/tensorfold/sass-and-air-diff/README.md) | Diff compiled SASS/ptxas/AIR binaries to catch untouched-kernel register and spill drift. | You edited a CUDA or Metal kernel or shared header before benchmarking. |
| [`optimization-ledger`](skills/tensorfold/optimization-ledger/README.md) | Block repeat dead ends, score mirrored `B,C,C,B` A/B runs, and track owed ports. | You want multi-session experiment memory and thermal-checked A/B verdicts. |
| [`graph-and-dispatch-surgeon`](skills/tensorfold/graph-and-dispatch-surgeon/README.md) | Audit between-pass host syncs and graph recaptures, and gate dynamic-width exactness. | GPU busy share is low or CUDA/Metal graphs recapture on width shifts. |
| [`model-bringup`](skills/tensorfold/model-bringup/README.md) | Inspect checkpoint shards and estimate KV-cache memory. | You're adding a new model or checkpoint format. |
| [`kernel-contract`](skills/tensorfold/kernel-contract/README.md) | Compare kernel output bit for bit or within a numerical tolerance. | You're replacing, fusing, or tuning a kernel. |
| [`one-stream-lanes`](skills/tensorfold/one-stream-lanes/README.md) | Compare single-stream throughput and draft widths. | One request leaves capacity unused. |
| [`output-parity`](skills/tensorfold/output-parity/README.md) | Compare responses and token bytes across builds. | A cache, kernel, or decoding change should preserve output. |
| [`prefill-and-kv`](skills/tensorfold/prefill-and-kv/README.md) | Size whole cache pages and verify saved state. | You're tuning prefix reuse, prompt chunking, or spill. |
| [`serving-concurrency`](skills/tensorfold/serving-concurrency/README.md) | Audit overlapping requests, throughput, and tail latency. | You're changing batching or concurrency limits. |
| [`speedup-referee`](skills/tensorfold/speedup-referee/README.md) | Review workload, baseline noise, and output parity behind a claimed gain. | You're deciding whether to merge or publish a speedup. |
| [`vram-alchemist`](skills/tensorfold/vram-alchemist/README.md) | Solve TensorFold VRAM fit, KV spill, and SSD expert flags before pulling weights. | You're picking the best TensorFold quant and serve flags for your machine. |
| [`quant-autopsy`](skills/tensorfold/quant-autopsy/README.md) | Sweep all TensorFold quants on uncontaminated tool-call, context-cliff, and Pareto benchmarks. | You want to see how every quant performs before committing to one. |
| [`drafter-forge`](skills/tensorfold/drafter-forge/README.md) | Verify vocab parity, harvest on-policy traces, solve draft depth, and gate `--drafter`. | You're building or validating a companion speculative drafter for TensorFold. |
| [`moe-xray`](skills/tensorfold/moe-xray/README.md) | Profile `--fsr-route-trace` MoE routing, `--ssd-experts` residency, and router drift. | You're auditing MoE expert utilization, SSD hit rates, or quant router drift. |
| [`local-coder-overclock`](skills/local-coder-overclock/README.md) | Lint GGUF/Jinja templates and probe live tool calling and KV prefix caches. | You're tuning a local model behind Claude Code, OpenCode, Cline, Aider, or OMP. |
| [`think-governor`](skills/think-governor/README.md) | Kill overthinking loops and calibrate per-tier reasoning budgets and loop guards. | A local thinking model loops, truncates blank, or burns tokens on easy tasks. |
| [`control-vector-forge`](skills/control-vector-forge/README.md) | Build, inspect, and gate GGUF control vectors with Mythos and Slopchop presets. | You want to steer local model behavior at zero prompt-token cost. |
| [`distill-foundry`](skills/distill-foundry/README.md) | Build execution-verified, zero-slop SFT and DPO splits with SHA-256 leak audits. | You're distilling a stronger teacher into a fast local student or LoRA. |

### Prompts, writing, and handover

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`prompt-roast`](skills/prompt-roast/README.md) | Gives your brief one sharp roast, then hands you a copyable rewrite. | You've left your agent guessing and want it to call you out. |
| [`handover`](skills/handover/README.md) | Record checked work and verify stale file and commit references. | You need another session to pick up the work. |
| [`slopchop`](skills/slopchop/README.md) | Cut filler, remove em dashes, and write in the speaker's voice. | You want a draft to sound like you. |

### Optional maintainer tool

Use [`skill-check`](skills/skill-check/README.md) when you write or publish a skill. It validates folders, builds packages, and compares agent behaviour on held-out tasks.

## Supported Agents

The Skills CLI detects supported agents automatically. For manual installation, copy a skill directory into the appropriate project or global path. Copy the folder that contains the chosen `SKILL.md` so its scripts and references stay with it. [Skills CLI](https://github.com/vercel-labs/skills)

| Platform | Project path | Global path |
| --- | --- | --- |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| OpenAI Codex | `.agents/skills/` | `~/.agents/skills/` |
| Gemini CLI | `.gemini/skills/` | `~/.gemini/skills/` |
| Cursor | `.cursor/skills/` | `~/.cursor/skills/` |
| GitHub Copilot | `.github/skills/` | `~/.copilot/skills/` |
| Windsurf | `.windsurf/skills/` | `~/.codeium/windsurf/skills/` |
| [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md) | `.omp/skills/` | `~/.omp/agent/skills/` |
| [Pi](https://docs.pi.dev/latest/configuration) | `.pi/skills/` | `~/.pi/agent/skills/` |
