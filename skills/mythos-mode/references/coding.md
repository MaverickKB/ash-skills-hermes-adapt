# Writing, changing, and reviewing code

Use this playbook when you are writing, changing, debugging, or reviewing code.

## Contents

1. Before you edit
2. Debugging
3. Making the change
4. Dependencies
5. Tests
6. Review your own diff
7. Version control
8. Security basics
9. Performance
10. Reviewing someone else's code
11. Explaining code
12. References by task

## Before you edit

1. Read the code you will change and the code that calls it.
2. Find out how the project runs and how it is tested. The package manifest, the CI config, and the README usually say.
3. Learn the conventions from two or three neighboring files: formatting, naming, error handling, test layout.
4. For a bug, reproduce it first. For a feature, find the closest existing feature and follow its shape.
5. Check version control. Note the branch and any uncommitted changes that aren't yours, and keep your work separate from them.

## Debugging

The general diagnosis loop is in `thinking.md`. These tactics are specific to code.

- Write the failing test first. It is your reproduction now and your regression guard later.
- Read the stack trace from the deepest frame that is in the project's own code.
- Bisect. Use commit history when it used to work, the input when only some inputs fail, and the code itself when you can switch halves off.
- Print the real values at the point where behavior and expectation part ways. Don't reason about what a value must be. Look at it.
- Check what is running: the installed versions, the environment variables, the built artifact against the source.
- In a large system, shrink the problem to a minimal reproduction.
- When a fix works, make sure you can say why. A fix you can't explain may have only moved the bug.
- Take the diagnostics out when you finish.

Four traps catch most people. They fix the first odd thing they see, when it is unrelated. They change several things at once. They trust a comment or a doc over what the code does. They blame the library when the bug is in their own call to it, which is where it usually is.

## Making the change

- Make the smallest diff that fully solves the problem. Leave untouched lines unformatted and unrenamed.
- Match the project's conventions, even where you would have chosen differently.
- Search for an existing helper before you write a new one.
- Build for the stated need. Options, layers, and config for needs nobody has raised are cost with no buyer.
- Name things for what they are, using the vocabulary the code already uses.
- Give each module one clear responsibility and each routine one job. Keep interfaces small and use the language's strongest practical types for their contracts.
- Use a brief comment when the reason for the code isn't obvious. Follow the repository's documentation conventions and preserve licence notices and tool directives.
- Handle an error where something useful can be done about it. Otherwise let it travel upward with context. An empty catch block hides the next bug.
- Keep retries bounded and safe to repeat. Account for cancellation, timeouts, partial failure, and concurrent writes where they affect callers.
- Validate at the edges, where input arrives from users, the network, or files. Trust calls inside the codebase.
- Keep a behavior change and a refactor in separate changes, so each can be reviewed and reverted alone.
- Delete what you replace. Commented-out code and unused functions mislead the next reader.
- Say so when you change a public interface, a file format, or a default. Callers you can't see depend on them.

## Dependencies

- Prefer what the project already uses.
- Before adding a package, confirm that it exists under that exact name in the registry and is the one you mean. Models produce plausible package names that don't exist, and attackers register those names.
- Check the API against the installed version's docs or source. Your memory may be of a different version.
- Check official support and compatibility before choosing or upgrading a version. When the choice matters, record the version, date checked, source, and compatibility reason.
- Use the project's package manager and keep its lockfile consistent.
- Say in your report that you added a dependency.

## Tests

- For a bug, add a test that fails before the fix and passes after it.
- For a feature, test the main path, the boundaries, and the failure path.
- Follow the existing test style and location.
- Run the specific test first, then the relevant suite, formatting, lint, type, and build checks.
- For a fix, run the same test against the old and new versions. For a behaviour-preserving refactor, expect both versions to pass, then check that a deliberately faulty copy is rejected.
- Never weaken, skip, or delete a test to get a green run, and never hard-code the expected output. If the test itself is wrong, say so, say why, and change it openly.
- If a failure looks flaky, rerun once to check, then report it as flaky with the evidence. Retrying until green and staying quiet hides a real problem.
- If you can't run the tests, say so.

## Review your own diff

Read the full diff before you report.

- Does every changed line serve the request? Revert the ones that don't.
- Are there debug prints, temporary files, or commented-out code?
- Is there a secret, key, token, or piece of personal data?
- What happens on the error path, and with empty, null, and very large input?
- Did you update the callers, types, tests, and docs that the change touches?
- Does it build, lint, and pass?

## Version control

- Check status and branch before you start and again before you finish.
- Commit, push, or open a pull request only when asked. If asked to commit while on the default branch, make a branch first unless told otherwise.
- Stage files by name. A blanket add sweeps in files you never looked at.
- Write a commit message that says what changed and why, in the project's style.
- Never rewrite shared history, force-push, or delete a branch without a clear yes.
- Never commit secrets, large generated files, or local config.
- Leave other people's uncommitted work alone.

## Security basics

- Treat all outside input as hostile. Validate it and encode it at the boundary.
- Build queries with parameters. Never build them by joining strings.
- Keep user input away from shells, `eval`, and deserializers.
- When a path is built from input, confirm it stays inside the intended directory.
- Keep secrets in the environment or a secret store, never in code or logs.
- Don't switch off certificate checks, authentication, or validation to make something work.
- Give tokens, roles, and files the least privilege that does the job.
- If you notice a vulnerability outside the request, report it clearly. It is the one kind of nearby problem that deserves more than a line.

## Performance

- Measure before you optimize and again after. Report both numbers.
- Find the hot spot with a profiler or timings. Intuition about where time goes is usually wrong.
- Fix the algorithm or the query before you tune constants.
- Don't trade clarity for speed nobody needs.

## Reviewing someone else's code

1. Understand what the change is for before judging how it does it.
2. Look for correctness problems first: logic errors, missed cases, broken callers, races, error handling, security.
3. Verify each finding before you report it. Trace the path or run it. A plausible bug that isn't real costs the author time and costs you credibility.
4. Check the tests as well as the code. Remove the change and run the new tests. If they still pass, they don't test the change. `red-team.md` covers the rest.
5. Report each finding with its location, the input that triggers it, what goes wrong, and a suggested fix. Put the most severe first.
6. Separate what must change from what is optional, and label taste as taste.
7. If the change is fine, say so in one line.

## Explaining code

Say what it does in one sentence, then how the pieces fit, then the surprises. Point to path and line. Match the level of the person who asked.

## References by task

| Task | Read |
| --- | --- |
| Choose module boundaries or split an overloaded file | [Architecture and module boundaries](architecture.md) |
| Choose or upgrade a runtime, dependency, or API | [Live research and version selection](research-and-versions.md) |
| Work in a TypeScript or Node.js project | [TypeScript and Node.js defaults](typescript-node.md) |
| Review a trust boundary, retry path, or measured optimisation | [Security and performance review](security-performance.md) |
| Decide which checks prove an implementation or review is complete | [Verification and review](verification.md) |
| Profile an inference engine end to end | [TensorFold performance playbook](inference-performance.md) |
