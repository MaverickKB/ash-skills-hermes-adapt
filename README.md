# Agent Skills

![A red laser traces the profile of a black basalt lion.](assets/mythos-mode.webp)

I want my agent to show me the bug it reproduced, the test that caught it, and the runs behind its speedup claim. These 24 skills give you a way to ask for that work.

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
| "It only fails on CI." | [Diagnosing bugs](skills/diagnosing-bugs/README.md) &rarr; [Prove it](skills/prove-it/README.md) | A reproducer and a test that catches the original failure. |
| "This change touches a lot of callers." | [Blast radius](skills/blast-radius/README.md) &rarr; [Ship check](skills/ship-check/README.md) | A map of what depends on your changes and a verdict on the staged snapshot. |
| "Decode is faster. Can we ship it?" | [Output parity](skills/tensorfold/output-parity/README.md) &rarr; [Fair bench](skills/fair-bench/README.md) &rarr; [Speedup referee](skills/tensorfold/speedup-referee/README.md) | A parity verdict, repeat timings, and an independent review of the speedup claim. |

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

### Planning, building, and debugging

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`repo-map`](skills/repo-map/README.md) | Find entry points, heavily imported files, and test commands. | You just opened an unfamiliar codebase. |
| [`spec-check`](skills/spec-check/README.md) | Turn vague requirements into checks and flag missing failure paths. | A feature or design needs testable acceptance criteria. |
| [`engineering-excellence`](skills/engineering-excellence/README.md) | Review interfaces, modularity, and failure paths before making a change. | You're building or refactoring production code. |
| [`blast-radius`](skills/blast-radius/README.md) | Trace callers, aliases, re-exports, and affected tests. | You're removing or renaming a shared symbol. |
| [`diagnosing-bugs`](skills/diagnosing-bugs/README.md) | Reproduce a failure, shrink its input, and bisect the regression. | A test flakes or a bug only appears on some builds. |
| [`migrate-safe`](skills/migrate-safe/README.md) | Lint SQL and verify schema and row data through a SQLite rollback. | You're changing a schema or backfilling data. |

### TensorFold and inference

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`model-bringup`](skills/tensorfold/model-bringup/README.md) | Inspect checkpoint shards and estimate KV-cache memory. | You're adding a new model or checkpoint format. |
| [`inference-speed-hunt`](skills/tensorfold/inference-speed-hunt/README.md) | Break down served request time and measure a targeted change. | You need to find where inference slows down. |
| [`kernel-contract`](skills/tensorfold/kernel-contract/README.md) | Compare kernel output bit for bit or within a numerical tolerance. | You're replacing, fusing, or tuning a kernel. |
| [`one-stream-lanes`](skills/tensorfold/one-stream-lanes/README.md) | Compare single-stream throughput and draft widths. | One request leaves capacity unused. |
| [`output-parity`](skills/tensorfold/output-parity/README.md) | Compare responses and token bytes across builds. | A cache, kernel, or decoding change should preserve output. |
| [`prefill-and-kv`](skills/tensorfold/prefill-and-kv/README.md) | Size whole cache pages and verify saved state. | You're tuning prefix reuse, prompt chunking, or spill. |
| [`serving-concurrency`](skills/tensorfold/serving-concurrency/README.md) | Audit overlapping requests, throughput, and tail latency. | You're changing batching or concurrency limits. |
| [`speedup-referee`](skills/tensorfold/speedup-referee/README.md) | Review workload, baseline noise, and output parity behind a claimed gain. | You're deciding whether to merge or publish a speedup. |
| [`shrink-model`](skills/shrink-model/README.md) | Plan structural pruning and verify a smaller checkpoint. | You need to hit a total or active parameter budget. |

### Writing and maintenance

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`handover`](skills/handover/README.md) | Record checked work and verify stale file and commit references. | You need another session to pick up the work. |
| [`skill-check`](skills/skill-check/README.md) | Check skill folders, package them, and compare behaviour on held-out tasks. | You're writing or updating a skill. |
| [`slopchop`](skills/slopchop/README.md) | Cut filler, remove em dashes, and write in the speaker's voice. | You want a draft to sound like you. |

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
