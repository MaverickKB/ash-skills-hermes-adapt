# Verification and principal-level review

Read before declaring an implementation complete or when asked for a code review.

## Before implementation

- Capture the exact acceptance criteria and observable behaviour.
- Identify affected boundaries, regressions, migrations, failure modes, and ownership of changed modules.
- Check which checks CI already enforces and which tests are relevant.

## Evidence-based quality gates

1. **Correctness.** Realistic happy path, failure path, boundaries, and regression cases work. Behaviour matches requirements.
2. **Architecture.** Files and functions are cohesive. Interfaces have clear ownership. No unrelated subsystems share a god file.
3. **Maintainability.** Clear names, low coupling, a small public interface, minimal duplication, no speculative abstraction, and no narrative block comments.
4. **Type safety.** No unjustified escape hatches. External input is validated at runtime. Impossible states are constrained where practical.
5. **Security.** Authorisation, validation, query safety, secret handling, and resource limits are appropriate to the change.
6. **Performance.** No obvious algorithmic or I/O regressions. Any performance claim has a reproducible measurement.
7. **Operational safety.** Failure handling, retries, timeouts, observability, config, and migrations are adequate for the feature.
8. **Testing.** The mix of unit, integration and end-to-end tests fits the risk. Tests fail meaningfully when behaviour regresses.
9. **Reproducibility.** Lockfile, runtime and toolchain constraints align across local, CI, and deployment environments.
10. **Diff hygiene.** No unrelated edits, accidental generated artifacts, hardcoded secrets, abandoned TODOs, or temporary debug output.

## What blocks completion

- A functional or security regression.
- Untested complex behaviour when test infrastructure is available.
- A build, type or lint gate that fails because of the change.
- A new module whose responsibilities cannot be described in one cohesive sentence.
- New multi-line comments or docstrings that the repository does not require.
- Claimed performance or version freshness without verification.
- False statements that tests were run or that source information is current.

## Reasonable exceptions

- Existing legacy violations do not require broad cleanup for a narrow fix. Avoid making them worse, and call out high-risk issues.
- Generated code, schemas, datasets, migrations, and intrinsically cohesive algorithms may exceed rough line counts.
- Documentation comments that the repository's conventions or lint rules require are not violations. Write them in the repository's form.
- Mandatory licence notices and auto-generated third-party content must not be removed merely to satisfy comment style.
- If tools cannot run, be explicit about the limitation and give the exact checks you suggest. Do not claim verified readiness.

## Review response when reviewing code and not editing it

- Rank findings by impact: critical, high, medium, low.
- Give file and line evidence, the user-visible or operational consequences, and the minimum corrective action.
- Separate confirmed defects from hypotheses.
- Highlight missing tests and compatibility or security risks.
- Do not create a wall of speculative style complaints in the absence of functional issues.
