---
name: truffle-hunter
description: Performs an exhaustive line-by-line forensic repository audit for hidden telemetry, phone-home domains, hardcoded IP addresses, obfuscated endpoints, default-on analytics, machine fingerprinting, and credential or local auth-file harvesting (such as reading `~/.codex` or `~/.claude` and transmitting unsalted hashes). Ships a stdlib whole-repo scanner and mandatory line-review gate (`truffle_hunter.py`) that refuses to issue a clean verdict until every flagged file is inspected. Use when auditing a repository for telemetry, third-party data exfiltration, sneaky tracking, or deciding whether a tool is safe to run with local API keys.
metadata:
  version: "1.0.0"
---

# Truffle Hunter

Never trust a README or privacy page when deciding whether to hand a tool your API keys and source code. Projects routinely ship default-on telemetry, read local credential files (`~/.codex`, `~/.claude`, `~/.aws`, `~/.git/config`) to hash account IDs into persistent tracking identifiers, bury phone-home pings inside startup/update hooks, or leak `process.env` inside crash dumps. Audit the actual code line by line and deliver one blunt verdict.

## Ground rules

1. **Read code, never docs.** Documentation claims ("we collect zero personal data", "we never send API keys") are untrusted marketing. Every claim in your verdict must trace to exact file paths and line numbers you read in this session.
2. **Account for every file and every line.** Run `scripts/truffle_hunter.py scan` first, then read **every** `CRITICAL`, `HIGH`, and `MEDIUM` file in full from line 1 to EOF. Never skim a grep match and guess what the rest of the file does.
3. **Hashing does not make snooping safe.** Unsalted SHA-256 (or MD5/SHA-1) hashes of account IDs, emails, API keys, machine UUIDs or MAC addresses are persistent cross-session tracking identifiers. Reading local auth files owned by other tools (`.codex`, `.claude`, `.openai`, `.aws`, `.ssh`) for telemetry is always **sus**, even when raw keys are not sent verbatim.
4. **Expose every blind spot.** Static source audits cannot see inside compiled binaries, uninspected third-party packages in `node_modules`/lockfiles, dynamically downloaded remote code, or live packets unless captured. State every unscanned blind spot plainly beside your verdict.
5. **No clean verdict with unreviewed files.** `truffle_hunter.py verdict` exits `1` (`INCOMPLETE_AUDIT`) if even one `CRITICAL`, `HIGH`, or `MEDIUM` file has no recorded line-by-line review.

## Step 1: Full-repository forensic scan

Scan the repository and write the ledger to a private folder outside the checkout. The ledger records locations and detected hosts without copying source excerpts or URL payloads.

```bash
python3 scripts/truffle_hunter.py scan /path/to/repo --out ../truffle_audit.json
```

Pass `--include-vendor` when auditing small vendored bundles or suspect dependencies directly.

The scanner skips symlinks and special files and reports them as blind spots. It inspects readable text files for the following signals.

- Hardcoded URLs, external domains, and public IPv4 literals (separating executable code/config/manifests from docs)
- Base64-encoded and hex-encoded obfuscated URLs (automatically decodes candidate base64 literals!)
- Split/dynamic URL assembly and runtime string decoders near URL/telemetry symbols
- Known analytics, telemetry, crash-reporting and session-replay SDKs in both manifests and imports
- `package.json` lifecycle hooks (`preinstall`, `install`, `postinstall`, `prepare`) invoking network or telemetry commands
- Every outbound network primitive across JS/TS, Python, Go, Rust, Zig, C/C++, Swift, Shell, Java, Ruby and PHP
- Default-on / opt-out telemetry flags (`!== "false"`, `!= "0"`, `default: true`)
- Silent update/version checks and unscrubbed `process.env` / `os.environ` error-reporter leaks
- **Correlated compound combos:** local auth/credential file reads paired with hashing or outbound telemetry (`CREDENTIAL_TELEMETRY_COMBO`), and machine/user fingerprinting paired with outbound sinks (`FINGERPRINT_TELEMETRY_COMBO`)

## Step 2: Mandatory line-by-line deep inspection

Work through **100% of the review queue** in `truffle_audit.json`, highest severity first, reading each file completely:

1. **Follow the full data flow** for every outbound sink and telemetry call:
   - Trigger: install time, app boot, every request, crash, or explicit user action?
   - Default state: opt-out (DEFAULT-ON) or strictly opt-in?
   - Exact payload: every field serialized, hashed, or attached (account IDs, auth files, env vars, file paths, prompts, hostnames, git email, stack traces).
   - Destination: exact domain, IP, port, protocol, and third-party vendor.
   - Kill switch: exact env var, flag or config key that actually disables it (verify the guard covers *every* call site, not just one!).
2. **Audit unflagged high-risk surfaces** that regex scanners can miss:
   - Entrypoints and startup/boot modules (check what runs before user consent)
   - Dependency manifests (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`) and lockfile registry URLs
   - Custom HTTP wrapper modules, error/crash handlers, logger transports, and build/install scripts
   - Minified bundles listed under `blind_spots.minified_files` (beautify and inspect)
3. **Record every reviewed file** in the ledger. Supply the full scanned line range and describe the data by field name. Rerun the scan after changes to the repository.

```bash
python3 scripts/truffle_hunter.py record-review \
  --ledger ../truffle_audit.json \
  --file apps/server/src/telemetry/Identify.ts \
  --lines 1-92 \
  --disposition CONFIRMED_TELEMETRY \
  --default-on \
  --data-sent "unsalted SHA-256 of local auth account IDs" \
  --note "Reads local auth files and hashes account IDs for default-on PostHog identify()."
```

Dispositions:
- `CONFIRMED_TELEMETRY`: transmits usage, analytics, crash, update-ping, or fingerprint telemetry to a third party.
- `SUSPICIOUS`: unexplained external IP/domain, obfuscated endpoint, auth-file snooping, unscrubbed env/error leak, or install-time phone-home.
- `BENIGN_CORE_API`: strictly user-requested core functionality (for example calling the user's configured model API or git remote).
- `DOC_OR_TEST_ONLY`: dead reference inside docs, comments, or isolated offline tests with zero runtime transmission.

## Step 3: Gate completion and deliver the blunt verdict

```bash
python3 scripts/truffle_hunter.py verdict --ledger ../truffle_audit.json
```

Open your report to the user with the exact verdict on line 1:

- **`Zero telemetry is transmitted as far as I can tell`** when `verdict` exits `0` (zero `CONFIRMED_TELEMETRY`, zero `SUSPICIOUS`, zero unreviewed flagged files). Follow immediately with:
  1. Exact coverage (`N files, M lines scanned`, plus flagged files manually read line by line).
  2. Every intentional `BENIGN_CORE_API` outbound host the code talks to.
  3. Every remaining blind spot (binaries, uninspected third-party packages, no live packet capture run).
- **`This part looks sus`** when `verdict` exits `1` due to any `CONFIRMED_TELEMETRY` or `SUSPICIOUS` finding. Follow immediately with:
  1. Exact `file:line` citations and offending code behavior.
  2. Exact data leaving the machine (including hashed identifiers and local auth files touched) and destination host/vendor.
  3. Whether it is **default-on** and the verified kill switch (or firewall block if none exists).
  4. Coverage counts and remaining blind spots.

## Covert telemetry tactics reference

Read `references/telemetry-tactics.md` for sneaky exfiltration patterns (credential-derived hashing, opt-out dark patterns, crash-reporter env leaks, Scarf/pixel pings, build-time hooks, DNS/CDN beacons and live packet verification commands).
