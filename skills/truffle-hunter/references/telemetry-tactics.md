# Covert telemetry tactics and forensic verification

Patterns repositories use to collect user data quietly, why "we hash it" and "we never send API keys" still fail privacy scrutiny, and how to prove zero egress at runtime.

## Contents

1. Credential-derived fingerprinting and auth-file snooping
2. Default-on opt-out dark patterns
3. Crash reporters and unscrubbed diagnostic leaks
4. Obfuscated and indirect phone-home channels
5. Build, install and update-check beacons
6. Live egress verification beyond static code review

## 1. Credential-derived fingerprinting and auth-file snooping

A common privacy failure in developer and AI tools is reading local authentication files owned by other tools (such as `.codex/auth.json`, `.claude`, `.openai`, `.anthropic`, `.aws/credentials`, `.netrc`, `.git/config`, or macOS Keychain), extracting an account ID, email or key prefix, and running an **unsalted SHA-256** hash to populate `distinctId` or `identify()` in PostHog, Segment or Mixpanel.

Why this is always **sus**:

- **Unsalted hashes are correlatable identifiers, not anonymous.** The same user produces the exact same hash across sessions, machines and tools, and any known account ID or email can be hashed to match the telemetry record directly.
- **Cross-tool credential file reads violate least privilege.** A tool should never read authentication files belonging to other CLI tools solely to mint a tracking ID.
- **Fallback leakage.** Code that tries to read an account ID and falls back to hashing another config field or raw token frequently drifts across versions and accidentally hashes secrets.

Always trace every read of `~/` dot-directories and `*_API_KEY` / `*_TOKEN` env vars through to every hash and outbound call in the repository.

## 2. Default-on opt-out dark patterns

Check the exact boolean condition around every telemetry toggle:

- **Default-on (sus):** `process.env.APP_TELEMETRY !== "false"`, `os.getenv("NO_TELEMETRY") != "1"`, or `enabled ?? true`. Telemetry fires on the very first run before the user ever learns the flag exists.
- **Partial kill switches:** Some apps honor `DO_NOT_TRACK=1` for PostHog while leaving Sentry crash uploads, startup update checks or scarf download pings active. Verify the disable flag guards **every** outbound telemetry sink in the repo.
- **Silent startup races:** Even when a user disables telemetry in a settings UI, check whether boot code already fired an `app_started` or `identify` ping before loading the config file.

## 3. Crash reporters and unscrubbed diagnostic leaks

Error trackers (Sentry, Bugsnag, Rollbar, Datadog, OpenTelemetry) routinely capture more than stack traces when left on default settings:

- Check the installed SDK and enabled integrations for collection and scrubbing options such as `sendDefaultPii` or `dataCollection`. Header values, cookies, query strings and inferred IP addresses can have separate controls. [Sentry Node.js data collection](https://docs.sentry.io/platforms/javascript/guides/node/data-management/data-collected/)
- Serializing `process.env`, `os.environ`, full CLI argv, or raw HTTP request/response bodies inside exception context can leak API keys and proprietary source code.
- Check explicitly for a `beforeSend` / scrubber hook and verify it strips headers, env vars, local home paths and prompt/code payloads.

## 4. Obfuscated and indirect phone-home channels

Static greps for `https://` miss deliberate or accidental indirection:

- **Base64 or hex literals.** Strings like `aHR0cHM6Ly8...` decoded via `atob`, `Buffer.from(..., "base64")`, or `bytes.fromhex`. `truffle_hunter.py` automatically decodes candidate base64 literals and checks them against URL and telemetry-domain rules.
- **Split host assembly.** `["us", "i", "posthog", "com"].join(".")`, template strings with env defaults, or config JSON files loaded dynamically.
- **Markdown/SVG tracking pixels and redirectors.** README badges, `scarf.sh` package-download redirectors, or HTML `<img>` / `navigator.sendBeacon` calls inside Electron/webview UIs.
- **Raw IP literals and non-HTTP protocols.** Hardcoded public IPv4/IPv6 addresses (outside RFC 5737 test ranges such as `192.0.2.1`), raw UDP/TCP sockets, or DNS TXT/A lookups encoding host telemetry into subdomains.

## 5. Build, install and update-check beacons

Inspect everything that executes before the main binary ever starts:

- `package.json` `preinstall`, `install`, `postinstall` and `prepare` scripts
- Python `setup.py` / custom build backends and Rust `build.rs`
- Shell install scripts (`install.sh`), `Makefile` targets and Dockerfiles
- Startup version checks hitting GitHub Releases, npm registries or custom version endpoints (every version check leaks IP address, timestamp, OS/arch and active usage cadence even with an empty body).

## 6. Live egress verification beyond static code review

Use a disposable sandbox and synthetic credentials to check runtime egress from compiled dependencies and downloaded plugins. Keep the resulting captures local.

- Run a quick offline smoke test with networking blocked (or inside a `--network=none` container) to see whether startup fails or logs blocked third-party domains.
- Capture live DNS and TLS SNI connections (`lsof -i`, macOS Little Snitch / LuLu, or `tcpdump` / `mitmproxy` in a disposable sandbox) on first boot and during a normal task to confirm zero unexpected hosts appear on the wire.
