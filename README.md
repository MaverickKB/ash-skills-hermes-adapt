# Agent Skills

![A red laser traces the profile of a black basalt lion.](assets/mythos-mode.webp)

I want my agent to show me the bug it reproduced, the test that caught it, and the runs behind its speedup claim. These 41 skills give you a way to ask for that work.

This is Ken Bandoly's Hermes Agent fork of [ashhart/skills](https://github.com/ashhart/skills). The skill content is upstream's; the packaging is adapted for Hermes: flat skill folders a Hermes tap can discover, `metadata.hermes` tags, `skills.sh.json` groupings, and Hermes shell-hook support in the Mythos reply gate. See [HERMES.md](HERMES.md) for the adaptation notes and hook setup.

[Install](#install) · [Try Mythos Mode](#see-mythos-mode-in-action) · [Hermes hooks](#hermes-hooks) · [Pick a workflow](#pick-a-workflow) · [Browse the skills](#skill-directory) · [Supported agents](#supported-agents)

## Install

Hermes installs from this fork through a tap. Add the tap once, then install any skill by name:

```bash
hermes skills tap add MaverickKB/ash-skills-hermes-adapt
hermes skills install MaverickKB/ash-skills-hermes-adapt/skills/repo-map
```

Replace `repo-map` with any folder name under `skills/`. The direct identifier also works without adding the tap. Installed skills land in the active profile's skills directory: `~/.hermes/skills/` for the default profile, or `~/.hermes/profiles/<profile>/skills/` for a named profile.

Skills rated caution by the scanner (break-it, breakthrough-architect, diagnosing-bugs, distill-foundry, handover, prove-it, ship-check, slopchop, vram-alchemist) install with `--force`.

`mythos-mode` is rated dangerous because `scripts/gate.py` contains the destructive-command patterns the gate detects, and the hub refuses a dangerous verdict even with `--force`. Copy that skill into the profile instead:

```bash
cp -R skills/mythos-mode ~/.hermes/profiles/<profile>/skills/
```

For a manual install of any skill, copy the skill folder containing `SKILL.md` into the profile skills directory. Hermes scans recursively, so a category folder is allowed, but the tap installer expects the flat `skills/<name>/` layout this fork uses.

Other agents can still use the Skills CLI with Node.js 22.20 or later:

```bash
npx skills add MaverickKB/ash-skills-hermes-adapt --skill mythos-mode
```

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

## Hermes hooks

Mythos Mode can enforce its gates through Hermes shell hooks instead of Claude Code hooks. Add these entries to the active profile's `config.yaml`, pointing at the installed skill's script:

```yaml
hooks:
  pre_llm_call:
    - command: "python3 ~/.hermes/skills/mythos-mode/scripts/gate.py --hook session --client hermes"
  pre_tool_call:
    - matcher: "terminal"
      command: "python3 ~/.hermes/skills/mythos-mode/scripts/gate.py --hook pre-tool --client hermes"
      fail_closed: true
  post_tool_call:
    - matcher: "terminal|patch|write_file"
      command: "python3 ~/.hermes/skills/mythos-mode/scripts/gate.py --hook post-tool --client hermes"
  pre_verify:
    - command: "python3 ~/.hermes/skills/mythos-mode/scripts/gate.py --hook pre-verify --client hermes"
```

Hermes asks for consent the first time each `(event, command)` pair runs. `--accept-hooks`, `HERMES_ACCEPT_HOOKS=1`, or `hooks_auto_accept: true` skips the prompt. The gate returns Hermes-native directives: `{"context": ...}` on the first turn, `{"decision": "block"}` for a catastrophic command, and `{"action": "continue", "message": ...}` at the verify gate. See [HERMES.md](HERMES.md) for the payload mapping and `MYTHOS_GATE=warn` behavior.

## Pick a workflow

| What you're facing | Skills to use in order | What you get |
| --- | --- | --- |
| "Which stack should I build this on?" | [Research then build](skills/research-then-build/README.md) | Live sources, upstream code review, and a trial against your requirements. |
| "It only fails on CI." | [Diagnosing bugs](skills/diagnosing-bugs/README.md) &rarr; [Prove it](skills/prove-it/README.md) | A reproducer and a test that catches the original failure. |
| "This change touches a lot of callers." | [Ash blast radius](skills/ash-blast-radius/README.md) &rarr; [Ship check](skills/ship-check/README.md) | A map of what depends on your changes and a verdict on the staged snapshot. |
| "Does this repo phone home or snoop my API keys?" | [Truffle Hunter](skills/truffle-hunter/README.md) | Full-repo telemetry, IP/domain, obfuscation and auth-file audit with a blunt clean-or-sus verdict. |
| "Decode is faster. Can we ship it?" | [Output parity](skills/output-parity/README.md) &rarr; [Fair bench](skills/fair-bench/README.md) &rarr; [Speedup referee](skills/speedup-referee/README.md) | A parity verdict, repeat timings, and an independent review of the speedup claim. |
| "Push TensorFold speed locally without regressions." | [Breakthrough architect](skills/breakthrough-architect/README.md) &rarr; [TensorFold speed hunt](skills/tensorfold-speed-hunt/README.md) ([Critical path X-ray](skills/critical-path-xray/README.md) &rarr; [Optimization ledger](skills/optimization-ledger/README.md) &rarr; [SASS and AIR diff](skills/sass-and-air-diff/README.md) &rarr; [Speedup referee](skills/speedup-referee/README.md)) | Stage 0 physics gaps, 10-minute kill-probes and on-silicon autotuning feeding a tamper-checked 7-gate landing loop. |
| "Which quant fits my GPU and won't break coding agents?" | [VRAM alchemist](skills/vram-alchemist/README.md) &rarr; [Quant autopsy](skills/quant-autopsy/README.md) &rarr; [Local coder overclock](skills/local-coder-overclock/README.md) | Exact TensorFold VRAM fit and serve flags, an all-quant Pareto scorecard, and verified tool calling and prefix caching. |

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
| [`truffle-hunter`](skills/truffle-hunter/README.md) | Scan every file and line for telemetry, phone-home endpoints, obfuscated URLs, and auth-file snooping. | You want to know if a repo phones home before giving it API keys. |
| [`deepfield`](skills/deepfield/README.md) | Read confidential agreements locally and tie every claim to cited evidence. | You need page coverage, linked context, and explicit gaps instead of a guessed answer. |

### Planning, building, and debugging

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`research-then-build`](skills/research-then-build/README.md) | Check live sources, inspect upstream code, and run the same acceptance tests on two options. | You need to pick a stack, dependency, or major upgrade. |
| [`repo-map`](skills/repo-map/README.md) | Find entry points, heavily imported files, and test commands. | You just opened an unfamiliar codebase. |
| [`spec-check`](skills/spec-check/README.md) | Turn vague requirements into checks and flag missing failure paths. | A feature or design needs testable acceptance criteria. |
| [`ash-blast-radius`](skills/ash-blast-radius/README.md) | Trace callers, aliases, re-exports, and affected tests. | You're removing or renaming a shared symbol. |
| [`diagnosing-bugs`](skills/diagnosing-bugs/README.md) | Reproduce a failure, shrink its input, and bisect the regression. | A test flakes or a bug only appears on some builds. |
| [`think-like-ash`](skills/think-like-ash/README.md) | Fan out causes across every layer, jump to the middle, and sweep the tiny details. | A fault has no obvious cause or a linear checklist keeps missing it. |
| [`migrate-safe`](skills/migrate-safe/README.md) | Lint SQL and verify schema and row data through a SQLite rollback. | You're changing a schema or backfilling data. |

For production design, TypeScript, and dependency choices, use the [Mythos coding playbook](skills/mythos-mode/references/coding.md).

### TensorFold and inference

If you need to find where a served request spends its time, start with the [TensorFold performance playbook](docs/tensorfold.md). Use the trace to choose which specialist to load.

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`breakthrough-architect`](skills/breakthrough-architect/README.md) | Quantify silicon physics gaps, run 10-minute kill-probes, and autotune schedules on chip. | You want 2x-10x architectural leaps before entering the landing loop. |
| [`tensorfold-speed-hunt`](skills/tensorfold-speed-hunt/README.md) | Drive the 7-gate closed-loop TensorFold speedup workflow with hashed receipts. | You're running an end-to-end local TensorFold optimization hunt. |
| [`fabric-and-multi-rank`](skills/fabric-and-multi-rank/README.md) | Model NVLink/PCIe/MCDMA collectives and gate multi-rank deterministic parity. | You're scaling TensorFold across multiple GPUs or peer Macs. |
| [`critical-path-xray`](skills/critical-path-xray/README.md) | Parse served traces into GPU/host/idle shares, rooflines, and Amdahl bounds. | You need to find the real #1 bottleneck and verify kernel dispatch. |
| [`sass-and-air-diff`](skills/sass-and-air-diff/README.md) | Diff compiled SASS/ptxas/AIR binaries to catch untouched-kernel register and spill drift. | You edited a CUDA or Metal kernel or shared header before benchmarking. |
| [`optimization-ledger`](skills/optimization-ledger/README.md) | Block repeat dead ends, score mirrored `B,C,C,B` A/B runs, and track owed ports. | You want multi-session experiment memory and thermal-checked A/B verdicts. |
| [`graph-and-dispatch-surgeon`](skills/graph-and-dispatch-surgeon/README.md) | Audit between-pass host syncs and graph recaptures, and gate dynamic-width exactness. | GPU busy share is low or CUDA/Metal graphs recapture on width shifts. |
| [`model-bringup`](skills/model-bringup/README.md) | Inspect checkpoint shards and estimate KV-cache memory. | You're adding a new model or checkpoint format. |
| [`kernel-contract`](skills/kernel-contract/README.md) | Compare kernel output bit for bit or within a numerical tolerance. | You're replacing, fusing, or tuning a kernel. |
| [`one-stream-lanes`](skills/one-stream-lanes/README.md) | Compare single-stream throughput and draft widths. | One request leaves capacity unused. |
| [`output-parity`](skills/output-parity/README.md) | Compare responses and token bytes across builds. | A cache, kernel, or decoding change should preserve output. |
| [`prefill-and-kv`](skills/prefill-and-kv/README.md) | Size whole cache pages and verify saved state. | You're tuning prefix reuse, prompt chunking, or spill. |
| [`serving-concurrency`](skills/serving-concurrency/README.md) | Audit overlapping requests, throughput, and tail latency. | You're changing batching or concurrency limits. |
| [`speedup-referee`](skills/speedup-referee/README.md) | Review workload, baseline noise, and output parity behind a claimed gain. | You're deciding whether to merge or publish a speedup. |
| [`vram-alchemist`](skills/vram-alchemist/README.md) | Solve TensorFold VRAM fit, KV spill, and SSD expert flags before pulling weights. | You're picking the best TensorFold quant and serve flags for your machine. |
| [`quant-autopsy`](skills/quant-autopsy/README.md) | Sweep all TensorFold quants on uncontaminated tool-call, context-cliff, and Pareto benchmarks. | You want to see how every quant performs before committing to one. |
| [`drafter-forge`](skills/drafter-forge/README.md) | Verify vocab parity, harvest on-policy traces, solve draft depth, and gate `--drafter`. | You're building or validating a companion speculative drafter for TensorFold. |
| [`moe-xray`](skills/moe-xray/README.md) | Profile `--fsr-route-trace` MoE routing, `--ssd-experts` residency, and router drift. | You're auditing MoE expert utilization, SSD hit rates, or quant router drift. |
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

Hermes Agent is the primary target of this fork. The Skills CLI detects other supported agents automatically; for those, copy a skill directory into the appropriate project or global path. Copy the folder that contains the chosen `SKILL.md` so its scripts and references stay with it. [Skills CLI](https://github.com/vercel-labs/skills)

| Platform | Project path | Global path |
| --- | --- | --- |
| Hermes Agent (this fork) | Tap: `hermes skills tap add MaverickKB/ash-skills-hermes-adapt` | `~/.hermes/skills/` or `~/.hermes/profiles/<profile>/skills/` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| OpenAI Codex | `.agents/skills/` | `~/.agents/skills/` |
| Gemini CLI | `.gemini/skills/` | `~/.gemini/skills/` |
| Cursor | `.cursor/skills/` | `~/.cursor/skills/` |
| GitHub Copilot | `.github/skills/` | `~/.copilot/skills/` |
| Windsurf | `.windsurf/skills/` | `~/.codeium/windsurf/skills/` |
| [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md) | `.omp/skills/` | `~/.omp/agent/skills/` |
| [Pi](https://docs.pi.dev/latest/configuration) | `.pi/skills/` | `~/.pi/agent/skills/` |
