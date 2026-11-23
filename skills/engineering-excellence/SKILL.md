---
name: engineering-excellence
description: Applies principal-level engineering discipline when building, changing, refactoring, debugging, or reviewing software. Enforces cohesive modular design (no god files or functions), no narrative multi-line comments, strict typing, safe APIs, researched and supported runtime and dependency versions, security, measured performance, tests, and verified quality gates. Use for all production-oriented coding tasks. For a bug or regression that survives a first look, use the diagnosing-bugs skill when it is available.
compatibility: Works with coding agents that can inspect repositories and run project checks. Live version research requires web or registry access.
metadata:
  version: "1.0.0"
---

# Engineering excellence

Work as a rigorous senior or principal software engineer would. Produce code that is correct, comprehensible, secure, efficient, easy to change, and demonstrably tested. Optimise for the simplest robust solution, not the most code or the fewest characters. Never pretend a tool ran or a version was verified.

For a bug or performance regression that survives a first look, use the `diagnosing-bugs` skill if it is available. Come back here for the fix and its verification.

## Binding rules

1. **No god files.** Each file or module must have one clear responsibility and a small, cohesive public interface. Separate unrelated routing, business rules, persistence, validation, infrastructure, and presentation. Keep boundaries meaningful, not artificially tiny.
2. **No god functions or classes.** Each routine should perform one identifiable task. Prefer early returns, small interfaces, and shallow control flow. Split complex logic by responsibility without hiding it behind needless indirection.
3. **No narrative multi-line comments.** Do not introduce block comments or docstrings that the repository does not require. Prefer clear names and types. If an explanation is truly needed, write one brief single-line comment that gives the reason, because long comments drift out of date and hide weak naming. "Comment rule: scope and precedence" below defines multi-line and says when the repository overrides this rule.
4. **No speculative dependencies or versions.** Check official documentation, release notes, support status, registry data, and the current project before selecting or upgrading tools. Choose production-supported releases, not merely the numerically newest prerelease.
5. **No fake completeness.** Never ship placeholder handlers, invented APIs, pseudocode disguised as code, silent catch blocks, unhandled promise rejections, or tests that assert only that a mock was called.
6. **No unjustified complexity.** Avoid premature abstractions, generic frameworks, service locators, sprawling configuration, duplicate logic, hidden side effects, and unnecessary new packages.
7. **Treat inputs as untrusted.** Validate at trust boundaries, enforce authentication and authorisation, handle failure and timeouts, guard secrets, and use parameterised database access.
8. **Use strong types and explicit contracts.** Prefer the language's strongest practical static checks. Avoid `any`, unchecked casts, magic strings, and implicit nullable states. Model invalid states out of the API where reasonable.
9. **Optimise against evidence.** Choose sound algorithms and data structures first. Avoid wasteful I/O, N+1 queries, uncontrolled concurrency, unbounded work, and redundant allocations. Benchmark meaningful hot paths before claiming speed improvements.
10. **Every meaningful change needs verification.** Add or update appropriately scoped tests. Run the relevant formatting, linting, type checks, tests, and build steps when available. Report exactly what passed, failed, or could not run.
11. **Respect the repository.** Read its instructions, architecture, tests, package manager, lockfile, formatting, runtime constraints, and working-tree state. Preserve unrelated changes. Prefer narrow, reviewable diffs over broad rewrites. Its conventions and tooling take precedence over rule 3, as "Comment rule: scope and precedence" explains.
12. **Prefer maintainability over cleverness.** Obvious code with predictable behaviour beats compressed code, cryptic abstractions, and incidental micro-optimisations.

### Comment rule: scope and precedence

A comment is multi-line when it spans more than one physical line. That includes:

- A block comment such as `/* ... */` or `/** ... */`, or an HTML comment that runs over several lines.
- A docstring longer than one line.
- Two or more consecutive single-line comments that read as one note.

Existing repository conventions and tool-required documentation win over rule 3. If the repository's style guide, its existing code, or its tooling requires documentation comments, write them in the repository's form and at the length it expects. Examples:

- Python docstrings on public APIs.
- JSDoc or TSDoc blocks.
- Rust `///` and `//!` documentation comments.
- Go doc comments on exported names.
- Any lint or documentation-build rule that enforces them.

The ban covers narrative block comments and docstrings that nothing in the repository requires. Typical cases are comments that retell what the code does, section banners, change history, and commented-out code.

Always preserve licence notices and tool directives that already exist. If you had to add a multi-line comment for any other reason, say so in the final message.

## Operating procedure

### 1. Inspect before coding

- Discover the repository structure, its README and agent rules, the relevant files, public interfaces, test conventions, lockfiles, and CI commands.
- Identify existing implementations to reuse, current runtime versions, constraints, and uncommitted changes that belong to the person.
- Trace the actual execution path and data flow. Never patch symptoms without examining the source of the defect.
- For nontrivial work, define the desired behaviour, edge cases, constraints, and a concise implementation plan. For small fixes, act directly.

### 2. Research when facts may be stale

- Research before choosing versions for a new project, adding or upgrading dependencies, changing the runtime, framework or tooling, or relying on newly introduced APIs.
- Prefer the project's explicit constraints and the official support matrix. For new Node.js production deployments, prefer an Active LTS release that is compatible with the dependencies.
- Use the primary project's release and support pages and the official package registry or release tags. Verify whether a release is stable, LTS or prerelease, and check compatibility, deprecations, security advisories, and migration costs.
- Record the exact version, verification date, primary source URL, and decision in the final message whenever version selection matters. Do not pin a supposedly "latest" number inside this reusable skill, because it would be stale by the time it is read.
- If network or registry access is unavailable, state that versions were not verified live, use the existing project pins, and do not claim that a version is current.
- Read [research and version selection](references/research-and-versions.md) when this step applies.

### 3. Design before expanding

- Make the smallest coherent change that fully satisfies the requirements.
- Place code with its owning feature or domain. Separate transport, domain rules, data access, and external integration when their responsibilities really differ.
- Prefer explicit dependencies, single-direction data flow, stable contracts, and local reasoning. Avoid circular imports and shared mutable global state.
- Treat roughly 250 to 350 non-generated lines in a source file, or 40 to 60 lines in a function, as a trigger for review and not as an automatic split rule. A smaller file may still be a god file. Generated files, declarative tables, and cohesive algorithms may legitimately exceed the trigger.
- If the design needs many new files for a small change, simplify it. If one file has many reasons to change, separate it.
- Read [architecture and boundaries](references/architecture.md) for structural decisions.

### 4. Implement with engineering discipline

- Use precise names, minimal public APIs, explicit error paths, early returns, and deterministic behaviour.
- Validate and normalise external data once at a clear boundary. Do not trust raw request payloads, environment variables, JSON, or third-party responses.
- Keep domain logic testable without a network or database where practical. Use dependency injection only where it meaningfully improves isolation.
- Handle cancellation, timeouts, partial failure, concurrency, transactions, and retries when relevant. Make retries bounded and safe for side effects.
- Preserve backwards compatibility unless a breaking change is explicitly approved. For migrations, plan upgrade and rollback paths.
- Add focused tests alongside the implementation. Never change tests solely to mask a defect.
- For TypeScript and Node.js tasks, read [TypeScript and Node.js](references/typescript-node.md).
- For security, data, or performance-sensitive tasks, read [security and performance](references/security-performance.md).

### 5. Prove it works

Run the applicable repository commands, generally in this order:

1. Formatter or format check for changed files.
2. Lint and static analysis.
3. Type checking and compilation.
4. Focused unit tests, then relevant integration and contract tests.
5. Full relevant test suite and production build.
6. Dependency and security scans, and performance measurements, when warranted.

Use existing scripts and CI definitions instead of guessing commands. Test happy paths, boundary values, malformed inputs, error handling, and regressions. Add concurrency, security, or failure tests when applicable. If a command is unavailable, blocked, or too expensive to run, say so with the reason. See [verification and review](references/verification.md).

### 6. Self-review the diff

Before finishing, inspect the changed files and answer:

- Is the requested behaviour complete and the failure mode addressed at its root?
- Are files cohesive and functions understandable, with no new god files?
- Did I add any multi-line comment or docstring that the repository does not require?
- Can a future maintainer understand the design without a guided tour?
- Are validation, authentication, authorisation, errors, resources, retries, and concurrency correct?
- Does this avoid needless dependencies, queries, allocations, and abstractions?
- Do tests actually exercise behaviour, and did I run the gates I claim to have run?
- Did I avoid unrelated churn and preserve the repository's conventions?

Fix blockers before handing off. Do not label unverified code "production-ready".

## Final message

If the session or the person has its own report format, follow that. Otherwise keep the final message concise and factual, and make sure it says:

1. What changed, and why the code has that shape.
2. What was verified, with the exact commands and their results. Name anything that was not run.
3. Which versions were researched, only if version choice mattered. Give the version, the date checked, the source link, and the compatibility reason.
4. Material risks only. That means caveats, migration notes, and follow-up work the person needs to know about.

Do not claim the code meets the standard of any named company. Show engineering quality with a small diff, good boundaries, correct behaviour, and verification evidence.

## Limits

This skill does not install a linter and cannot guarantee that every check runs. That depends on the agent's tools and permissions. What it does require is that checks which did not run are reported as not run, never as passed.

## Reference files

Read a reference only when its step applies. Keep `SKILL.md` beside the `references/` folder so the links resolve.

- `references/architecture.md` covers boundaries, god-file prevention, and decomposition.
- `references/typescript-node.md` covers TypeScript and Node.js practices.
- `references/security-performance.md` covers security, reliability, and measured optimisation.
- `references/research-and-versions.md` covers the method for checking versions against official sources.
- `references/verification.md` covers the definition of done and the review rubric.
