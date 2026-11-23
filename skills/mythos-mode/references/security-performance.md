# Security and performance review

Use for APIs, databases, authentication, external integrations, sensitive data, or performance-critical tasks.

## Security gates

- Identify trust boundaries and threats before implementing new externally reachable behaviour.
- Validate types, shape, ranges, encodings, sizes, and format of all untrusted inputs. Reject or normalise deliberately.
- Enforce both authentication and resource-level authorisation at the actual operation, not just at the UI or route prefix.
- Use parameterised queries, safe output encoding, safe file-path handling, and established cryptography. Never hand-roll crypto or interpolate SQL.
- Protect cookies, sessions, tokens, CSRF-sensitive operations, and redirect targets according to the framework and threat model.
- Use least-privilege service identities. Never hardcode credentials or log secrets, personal data, or raw authentication headers.
- Handle SSRF risks for outbound fetches, insecure deserialisation, dangerous shell invocation, and untrusted file uploads.
- Place reasonable bounds on time, data size, retries, concurrency, and resource usage to resist accidental or malicious overload.
- Make error responses useful without leaking information. Retain contextual details in secured operational logs.
- Research current security advisories when adding or upgrading an affected dependency. Do not fix vulnerabilities by blindly installing incompatible major versions.
- Preserve auditability for privilege-sensitive actions and migrations where relevant.

## Performance gates

- State the actual objective (for example throughput, memory ceiling, p95 latency, CI time, or bundle size) and the measurement method.
- First improve complexity and I/O patterns. Check indexes, query plans, batching, N+1 patterns, unnecessary serial waits, and cache hit rates.
- Avoid unbounded scans, accumulating large arrays in memory, excessive object churn, and unconstrained parallelism.
- Cache only when invalidation, consistency, and operational impact are understood.
- Prefer simple code until profiling identifies a bottleneck. Do not sacrifice correctness or clarity for unmeasured micro-optimisations.
- Record before and after measurements under comparable conditions whenever claiming a speed or memory improvement.

## Reliability gates

- Treat transient failure separately from permanent failure. Retry only safe operations, with bounded exponential backoff and jitter as appropriate.
- Use timeouts and cancellation to prevent runaway operations. Ensure partial work is rolled back or compensated.
- Preserve atomicity and idempotency for repeated jobs, retries, and concurrent writes.
- Consider rate limits, graceful degradation, telemetry, alert signals, and recovery paths for production-critical flows.
