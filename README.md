# Agent Skills

![A red laser traces the profile of a black basalt lion.](assets/mythos-mode.webp)

Pick a skill for the job in front of you, from investigating a flaky test to checking that an inference kernel produces the same tokens. Each skill has a README with a use case and a `SKILL.md` with the agent instructions.

## Verification and review

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`mythos-mode`](skills/mythos-mode/README.md) | Checks work against seven rules for evidence, scope, verification, and concise reporting. | You want an agent to check its assumptions and show what its verification covered. |
| [`prove-it`](skills/prove-it/README.md) | Tests fixes against a baseline and checks whether tests detect a deliberately introduced fault. | You need evidence that a retry fix works or that a refactor preserves behaviour. |
| [`break-it`](skills/break-it/README.md) | Uses code mutations and edge-case inputs to find defects that a test suite or review missed. | You want to stress-test a code change, command-line tool, or implementation plan. |
| [`ship-check`](skills/ship-check/README.md) | Audits a Git diff for credential patterns, debug code, broken Python callers, and failed checks. | You want a pre-commit or pre-merge check of the code that will actually ship. |
| [`fair-bench`](skills/fair-bench/README.md) | Measures baseline noise and compares two commands in alternating order. | You need to tell a real speedup from run-to-run variation. |
| [`red-team`](skills/red-team/README.md) | Validates written security-review scope and records approvals and evidence in a hash-chained log. | You need to document what a defensive security review covers and make its evidence auditable. |

## Planning, building, and debugging

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`repo-map`](skills/repo-map/README.md) | Summarises a repository's layout, build and test commands, and heavily imported files. | You're opening an unfamiliar repository or planning changes across several modules. |
| [`spec-check`](skills/spec-check/README.md) | Finds vague requirements, missing failure cases, and broken links between a spec and its tests. | You want a design or feature proposal that someone can implement and verify. |
| [`engineering-excellence`](skills/engineering-excellence/README.md) | Guides production code changes through API design, typing, modularity, and verification. | You're adding a feature, reviewing a change, or refactoring code with existing callers. |
| [`blast-radius`](skills/blast-radius/README.md) | Traces callers, aliased imports, dependent modules, tests, and documentation for a proposed change. | You're renaming a symbol, changing a signature, or removing a module. |
| [`diagnosing-bugs`](skills/diagnosing-bugs/README.md) | Reproduces failures, reduces failing inputs, and narrows regressions with Git bisection. | A failure is intermittent, a first fix didn't work, or behaviour differs between versions. |
| [`migrate-safe`](skills/migrate-safe/README.md) | Lints SQL migrations and checks schema and row data across apply, repeat, and rollback in SQLite. | You're changing a schema, backfilling rows, or testing whether a migration can be reversed. |

## TensorFold and inference

These skills cover checkpoints, kernels, caching, decoding, and served performance. They live in the [TensorFold group](skills/tensorfold/) and can be installed individually.

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`model-bringup`](skills/tensorfold/model-bringup/README.md) | Inspects checkpoint shards, estimates KV-cache requirements, and checks output parity during model bring-up. | You're adding an open-weight model or checkpoint format to an inference runtime. |
| [`inference-speed-hunt`](skills/tensorfold/inference-speed-hunt/README.md) | Measures time spent in a served request and uses that breakdown to choose optimisations. | You know inference is slow and need to locate the bottleneck. |
| [`kernel-contract`](skills/tensorfold/kernel-contract/README.md) | Compares kernel outputs bit for bit or against a specified floating-point tolerance. | You're fusing, replacing, or tuning a GPU or CPU inference kernel. |
| [`one-stream-lanes`](skills/tensorfold/one-stream-lanes/README.md) | Measures the gap between single-request and concurrent throughput and evaluates draft widths. | One request leaves the accelerator idle or a wider draft window stops improving decode speed. |
| [`output-parity`](skills/tensorfold/output-parity/README.md) | Compares response text, finish reasons, token counts, and available token bytes across inference builds. | You've changed a kernel, cache path, or speculative decoder and expect identical output. |
| [`prefill-and-kv`](skills/tensorfold/prefill-and-kv/README.md) | Calculates prefill work, sizes cache residency in whole pages, and checks saved cache state. | You're tuning prompt chunking, prefix reuse, or KV-cache memory and spill behaviour. |
| [`serving-concurrency`](skills/tensorfold/serving-concurrency/README.md) | Audits concurrent request traces for actual overlap, token parity, throughput, and tail latency. | You're tuning batching, queueing, or concurrency limits. |
| [`speedup-referee`](skills/tensorfold/speedup-referee/README.md) | Independently reviews whether a claimed speedup holds under the same workload and output contract. | You're reviewing a performance claim before merging it or publishing the numbers. |

## Writing and maintenance

| Skill | What it does | When to use it |
| --- | --- | --- |
| [`handover`](skills/handover/README.md) | Writes handover notes and checks whether their file, branch, and commit references still match the repository. | Another person or agent is taking over work in progress. |
| [`shrink-model`](skills/shrink-model/README.md) | Plans structural pruning and recovery training from an open-weight parent checkpoint. | You need a smaller descendant model within a total or active parameter budget. |
| [`skill-check`](skills/skill-check/README.md) | Validates skill folders, builds distributable packages, and compares agent behaviour with and without a skill. | You're writing or updating skills for others to install. |
| [`slopchop`](skills/slopchop/README.md) | Finds filler and stock phrasing in prose and guides a rewrite that preserves meaning. | A README, report, or handover uses generic claims where readers need specifics. |

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
