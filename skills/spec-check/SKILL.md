---
name: spec-check
description: Sharpens a feature idea, RFC, ADR or engineering plan into a falsifiable specification before code is written, and lints spec files for vague requirements, missing failure or rollback paths, broken repository paths and untraced requirement IDs. Ships a stdlib spec linter (`spec_lint.py`). Use before starting a nontrivial feature, when reviewing an RFC or design doc, when requirements sound loose or unmeasurable, or when verifying that every requirement in a spec maps to code and tests.
metadata:
  version: "1.0.0"
---

# Spec check

Most wasted coding hours start with a vague specification. A requirement like "make sync fast and handle errors gracefully" cannot fail a test, so the agent guesses and the human rewrites it later. Turn every wish into a falsifiable contract before writing code.

## 1. Interrogate the gaps

Before drafting or approving a spec, settle five questions. Ask only what the repository and existing docs cannot answer.

1. **Goal and boundary.** What exact outcome marks success, and what is explicitly out of scope?
2. **Concrete thresholds.** Replace adjectives ("fast", "small", "reliable") with numbers and units ("p95 under 120 ms on 10,000 rows").
3. **Failure modes.** What happens on empty input, malformed input, timeouts, partial failure and duplicate execution?
4. **Reversibility.** If the rollout fails halfway, how do you roll back without losing data?
5. **Falsifiable acceptance checks.** Give every requirement an ID (`REQ-01`, `REQ-02`) and one concrete check that fails before implementation and passes after.

## 2. Lint the specification

Run `scripts/spec_lint.py` on the written spec before implementing:

```bash
python3 scripts/spec_lint.py docs/rfc-sync.md --repo .
python3 scripts/spec_lint.py docs/rfc-sync.md --repo . --check-trace --require-rollback
```

`spec_lint.py` uses only the Python 3.9+ standard library and checks four things:

- **Required sections:** goal, out-of-scope, failure modes, acceptance checks, plus rollback whenever the spec mentions migrations, schemas, production deploys or deletion.
- **Unmeasurable wording:** flags vague terms (`fast`, `scalable`, `gracefully`, `TBD`, `etc.`) that have no numeric threshold or exit-code check in the same sentence.
- **Repository reality (`--repo`):** verifies every backticked file path exists on disk unless tagged `(planned)` or `(gone)`.
- **Traceability (`--check-trace`):** matches complete `REQ-`, `AC-` and `SPEC-` identifiers in code or tests, preserving their digits and leading zeros.

Exit codes: `0` on `SPEC: CLEAN`, `1` on `SPEC: FINDINGS`, `2` on usage or read error.

## Rules

- Do not start coding against a spec with unresolved `TBD`s or missing acceptance checks unless the user explicitly asks to prototype first.
- Separate what the user asked for from design assumptions you proposed; mark every assumption so it can be overturned cheaply.
- Keep specs short and testable. One falsifiable table beats five pages of prose.
