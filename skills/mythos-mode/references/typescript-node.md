# TypeScript and Node.js defaults

Apply only to TypeScript, JavaScript and Node.js projects. Existing repository requirements take precedence over new-project defaults.

## Runtime and dependencies

- For production Node.js, check the official release schedule and select the newest suitable Active LTS release, unless infrastructure or dependency compatibility requires a different supported line.
- Check release status and the current patch at https://nodejs.org/en/about/previous-releases and https://nodejs.org/en/download/.
- Never confuse an Alpha or Current release with a production LTS release.
- For new apps, declare consistent runtime expectations in `engines`, the chosen version-manager file, local tooling, CI, and deployment. Pin the actual runtime in deployment and refresh supported patch releases through a managed process.
- Respect the project's existing package manager. Commit and use exactly one corresponding lockfile. Install deterministically in CI (`npm ci`, `pnpm install --frozen-lockfile`, or equivalent).
- Prefer built-in platform APIs when they are sufficient. Check licence, maintenance, security, transitive dependency count, Node.js support, API stability, and ecosystem integration before introducing a package.
- Do not auto-upgrade major dependencies during unrelated feature work.

## TypeScript defaults for new projects

- Enable `strict`. Prefer `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, and `noFallthroughCasesInSwitch` when compatible. Do not silently impose them on existing projects without accounting for migration cost.
- Prefer `unknown` at boundaries and narrow using validation. Avoid `any`, non-null assertions, broad `as` casts, and `@ts-ignore` except with a documented, unavoidable justification.
- Prefer small discriminated unions for mutually exclusive states and exhaustive handling for domain cases.
- Use `readonly` where it communicates ownership. Avoid mutable exports and global state.
- Use explicit return and error contracts for public APIs. Keep internal inference ergonomic. Do not annotate every local variable needlessly.
- Avoid boolean-flag APIs when a named option or separate operation makes intent clearer.
- Choose ESM or CommonJS to fit the project and the actual ecosystem. For new Node.js projects, favour supported modern ESM with a configuration that matches the resolver and runtime. Verify tooling compatibility first.
- Keep request and response schemas and data contracts explicit. TypeScript types alone do not validate runtime inputs.

## Server-side engineering

- Do not block the event loop with CPU-heavy processing or sync filesystem operations in request hot paths.
- Bound database queries, pagination, queue lengths, batch sizes, retries, request bodies, and fan-out concurrency.
- Use proper async error propagation, `finally` cleanup, resource pools, timeouts, cancellation, and backpressure when applicable.
- Keep transactions short and enforce uniqueness and consistency in the database where appropriate.
- Use structured, contextual logs with sensitive fields redacted. Emit observability signals for failures and important latency, but not redundant noise.
- Implement idempotency where retries can repeat side effects, particularly in payment, email, and event processing paths.
- Ensure graceful shutdown for servers and workers, and test it if the lifecycle changes.

## Testing defaults

- Test pure domain rules quickly and deterministically.
- Use integration tests for database behaviour, authentication and authorisation, request boundaries, and external service contracts.
- Mock only controlled boundaries. Do not mock the unit's actual behaviour or assert implementation details in place of outcomes.
- Exercise malformed data, missing fields, timeouts, partial failures, race conditions, and regression cases as relevant.

## Comment policy reminder

- Follow the repository first. If it documents public APIs with JSDoc or TSDoc, or a lint rule requires those blocks, write them in its style.
- Where nothing requires them, add no multi-line JSDoc, block comments, or multi-line docstrings.
- Express contracts with names, types and external docs. Allow a single-line comment only for a non-obvious invariant or the reason a workaround exists.
