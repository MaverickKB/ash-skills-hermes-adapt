#!/usr/bin/env python3
"""Forensic repository scanner and line-by-line review gate for hidden telemetry.

Walks every file in a repository to surface outbound URLs, raw IPv4/IPv6
endpoints, base64/hex-obfuscated domains, telemetry/analytics/crash SDKs,
install-hook network calls, default-on telemetry flags, machine fingerprinting,
and credential/auth-file snooping paired with hashing or outbound sinks (such
as reading local AI auth files and transmitting hashed identifiers).

Tracks 100% file and line coverage, explicitly lists unscanned blind spots
(binaries, minified bundles, skipped vendor folders), and blocks a clean
verdict until every flagged file has been reviewed line by line. Uses only the
Python 3.9+ standard library.

Subcommands:
  scan           Scan an entire repository and write a complete audit ledger.
  record-review  Record a line-verified review disposition for one file in the
                 audit ledger (`CONFIRMED_TELEMETRY`, `SUSPICIOUS`,
                 `BENIGN_CORE_API`, `DOC_OR_TEST_ONLY`).
  verdict        Enforce 100% review completion on all CRITICAL/HIGH/MEDIUM
                 files and print the final verdict (`Zero telemetry is
                 transmitted as far as I can tell` or `This part looks sus`).

Exit codes: 0 clean verdict / scan clean, 1 sus findings or incomplete audit,
            2 usage or invalid ledger error.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime
import hashlib
import ipaddress
import json
import os
import re
import sys
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "target",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".cache",
}

MANIFEST_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "cargo.toml",
    "go.mod",
    "gemfile",
    "composer.json",
    "build.gradle",
    "pom.xml",
    "dockerfile",
    "makefile",
}

DOC_EXTS = {".md", ".rst", ".txt", ".adoc", ".mdx"}
CODE_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".pyi", ".go", ".rs", ".zig",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".swift",
    ".java", ".kt", ".kts", ".cs", ".rb", ".php", ".sh", ".bash", ".zsh", ".ps1",
    ".lua", ".dart", ".ex", ".exs", ".scala", ".pl",
}

URL_RE = re.compile(r"\b(?:https?|wss?|udp|tcp)://[^\s\"'`<>()\[\]{},;]+", re.I)
IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")
B64_CAND_RE = re.compile(r"(?<![A-Za-z0-9+/=])([A-Za-z0-9+/]{20,}={0,2})(?![A-Za-z0-9+/=])")
HEX_URL_RE = re.compile(r"(?:68747470733a2f2f|687474703a2f2f|7773733a2f2f)", re.I)
CHARCODE_RE = re.compile(r"\b(?:String\.fromCharCode|bytes\.fromhex|atob|Buffer\.from\([^)]*['\"](?:base64|hex)['\"])")
SPLIT_URL_RE = re.compile(r"['\"]https?://['\"]\s*\+|`https?://\$\{|['\"](?:posthog|sentry|segment|mixpanel|amplitude|datadog|honeycomb)['\"]", re.I)

TELEMETRY_SDKS: Tuple[str, ...] = (
    "posthog",
    "segment",
    "analytics-node",
    "mixpanel",
    "amplitude",
    "sentry",
    "datadog",
    "dd-trace",
    "honeycomb",
    "opentelemetry",
    "otlp",
    "telemetrydeck",
    "plausible",
    "google-analytics",
    "gtag",
    "heap-analytics",
    "fullstory",
    "logrocket",
    "bugsnag",
    "rollbar",
    "scarf",
    "axiom",
    "rudderstack",
    "snowplow",
    "statsig",
    "launchdarkly",
    "appcenter",
    "crashlytics",
    "matomo",
    "umami",
    "aptabase",
    "telemetry",
)

TELEMETRY_DOMAINS: Tuple[str, ...] = (
    "posthog.com",
    "segment.io",
    "segment.com",
    "mixpanel.com",
    "amplitude.com",
    "sentry.io",
    "datadoghq.com",
    "datadoghq.eu",
    "honeycomb.io",
    "telemetrydeck.com",
    "google-analytics.com",
    "googletagmanager.com",
    "plausible.io",
    "scarf.sh",
    "axiom.co",
    "bugsnag.com",
    "rollbar.com",
    "logrocket.io",
    "fullstory.com",
    "statsigapi.net",
    "launchdarkly.com",
    "aptabase.com",
    "firebase.googleapis.com",
    "appcenter.ms",
    "ingest.",
    "telemetry.",
    "metrics.",
    "analytics.",
)

# Common documentation/schema/example hosts that are not live phone-home endpoints when outside network sinks.
DOC_SAFE_HOSTS = {
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    "schema.org",
    "json-schema.org",
    "w3.org",
    "www.w3.org",
    "spdx.org",
    "opensource.org",
    "creativecommons.org",
    "mozilla.org",
    "developer.mozilla.org",
}

NETWORK_SINK_RE = re.compile(
    r"\b(?:fetch|axios|got|superagent|undici|sendBeacon|XMLHttpRequest|WebSocket|"
    r"http\.request|https\.request|net\.connect|tls\.connect|dgram\.createSocket|"
    r"urllib\.request|urlopen|requests\.(?:get|post|put|patch|delete|request)|"
    r"httpx\.|aiohttp\.|http\.client|socket\.create_connection|"
    r"reqwest::|hyper::|ureq::|http\.NewRequest|http\.Post|http\.Get|"
    r"std\.http\.Client|URLSession|curl|wget|Invoke-WebRequest|Invoke-RestMethod|"
    r"dns\.resolve|dns\.lookup|getaddrinfo)\b"
)

CRED_AND_AUTH_RE = re.compile(
    r"(?:\.codex|\.claude|\.openai|\.anthropic|\.aws|\.ssh|\.netrc|\.npmrc|\.pypirc|"
    r"\.docker/config\.json|auth\.json|credentials\.json|keychain|find-generic-password|"
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GITHUB_TOKEN|GH_TOKEN|AWS_SECRET|"
    r"API_KEY|AUTH_TOKEN|ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET)",
    re.I,
)

HASHING_RE = re.compile(
    r"\b(?:createHash|sha256|sha512|sha1|md5|blake2|hashlib|crypto\.subtle\.digest|hmac)\b",
    re.I,
)

FINGERPRINT_RE = re.compile(
    r"(?:/etc/machine-id|machine_id|machineId|IOPlatformUUID|os\.hostname|socket\.gethostname|"
    r"os\.userInfo|getpass\.getuser|networkInterfaces|mac_address|macAddress|uuid\.getnode|"
    r"git\s+config\s+[^\n]*user\.email|distinctId|distinct_id|anonymousId|anonymous_id|identify\s*\()",
    re.I,
)

DEFAULT_ON_TELEMETRY_RE = re.compile(
    r"(?:TELEMETRY|ANALYTICS|TRACKING|METRICS|OPT_OUT)[A-Z0-9_]*[^\n]*(?:!==?\s*['\"](?:false|0|no|off)['\"]|!=\s*['\"](?:false|0|no|off)['\"]|\|\|\s*true|default\s*[:=]\s*true)",
    re.I,
)

UPDATE_CHECK_RE = re.compile(
    r"(?:update[-_]?check|check[-_]?for[-_]?updates|latest[-_]?version|api\.github\.com/repos/[^/\s]+/[^/\s]+/releases/latest|registry\.npmjs\.org/)",
    re.I,
)

ENV_DUMP_RE = re.compile(
    r"(?:process\.env\b|os\.environ\b)[^\n]{0,80}(?:JSON\.stringify|captureException|captureMessage|fetch|post|send|body|payload|extra)",
    re.I,
)

SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "CLEAN": 0}
REVIEW_DISPOSITIONS = ("CONFIRMED_TELEMETRY", "SUSPICIOUS", "BENIGN_CORE_API", "DOC_OR_TEST_ONLY")


class Fail(Exception):
    """Usage, missing file or schema error -> exit 2."""


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_public_ip(text: str) -> bool:
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def classify_file_kind(rel: str) -> str:
    p = Path(rel)
    low_name = p.name.lower()
    low_parts = {part.lower() for part in p.parts}
    if low_name in MANIFEST_NAMES or ".github" in low_parts:
        return "manifest_or_ci"
    if any(t in low_parts for t in ("test", "tests", "__tests__", "spec", "fixtures", "mocks")) or ".test." in low_name or ".spec." in low_name or low_name.startswith("test_"):
        return "test"
    if p.suffix.lower() in DOC_EXTS or low_name.startswith(("readme", "license", "changelog", "contributing")):
        return "doc"
    if p.suffix.lower() in CODE_EXTS:
        return "code"
    return "config_or_other"


def endpoint_host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def decode_suspicious_base64(line: str) -> List[str]:
    hits: Set[str] = set()
    for m in B64_CAND_RE.finditer(line):
        tok = m.group(1)
        if len(tok) > 512:
            continue
        padded = tok + "=" * ((4 - len(tok) % 4) % 4)
        try:
            raw = base64.b64decode(padded, validate=True)
            decoded = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        hosts = {endpoint_host(match.group(0)) for match in URL_RE.finditer(decoded)} - {""}
        hits.update(hosts or {domain for domain in TELEMETRY_DOMAINS if domain in decoded.lower()})
    return sorted(hits)


def scan_manifest_hooks(rel: str, text: str) -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    if Path(rel).name.lower() != "package.json":
        return findings
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return findings
    if not isinstance(doc, dict):
        return findings

    scripts = doc.get("scripts")
    if isinstance(scripts, dict):
        for hook in ("preinstall", "install", "postinstall", "prepare"):
            cmd = scripts.get(hook)
            if isinstance(cmd, str) and (URL_RE.search(cmd) or re.search(r"\b(?:curl|wget|node\s+-e|python|telemetry|scarf)\b", cmd, re.I)):
                findings.append({
                    "line": 1,
                    "severity": "CRITICAL",
                    "rule": "LIFECYCLE_HOOK_NETWORK",
                    "detail": f"package.json scripts.{hook} invokes a network or telemetry command",
                })

    for dep_key in ("dependencies", "optionalDependencies", "peerDependencies", "devDependencies"):
        deps = doc.get(dep_key)
        if not isinstance(deps, dict):
            continue
        for pkg in sorted(deps):
            low = pkg.lower()
            if any(sdk in low for sdk in TELEMETRY_SDKS):
                sev = "HIGH" if dep_key != "devDependencies" else "MEDIUM"
                findings.append({
                    "line": 1,
                    "severity": sev,
                    "rule": "TELEMETRY_DEPENDENCY",
                    "detail": f"{dep_key} includes telemetry/analytics package `{pkg}`",
                })
    return findings


def scan_single_file(path: Path, rel: str) -> Dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {
            "path": rel,
            "kind": classify_file_kind(rel),
            "status": "UNREADABLE",
            "lines": 0,
            "sha256": "",
            "max_severity": "MEDIUM",
            "findings": [{"line": 0, "severity": "MEDIUM", "rule": "UNREADABLE_FILE", "detail": f"could not read file: {exc.strerror or type(exc).__name__}"}],
        }

    digest = sha256_bytes(raw)
    kind = classify_file_kind(rel)
    if b"\x00" in raw[:4096]:
        return {
            "path": rel,
            "kind": kind,
            "status": "BINARY_UNSCANNED",
            "bytes": len(raw),
            "lines": 0,
            "sha256": digest,
            "max_severity": "LOW",
            "findings": [],
        }

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    lines = text.splitlines()
    max_line_len = max((len(ln) for ln in lines), default=0)
    minified = max_line_len > 2500 and len(lines) < 50

    findings: List[Dict[str, object]] = []
    findings.extend(scan_manifest_hooks(rel, text))

    has_network = False
    has_cred_or_auth = False
    has_hash = False
    has_fingerprint = False
    has_telemetry_sdk = False
    endpoints_found: Set[str] = set()

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        # 1. URLs & external domains
        for m in URL_RE.finditer(line):
            raw_url = m.group(0).rstrip(".,;:'\")")
            host = endpoint_host(raw_url)
            if not host or host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
                continue
            endpoints_found.add(host)
            is_tel_dom = any(td in host for td in TELEMETRY_DOMAINS)
            if is_tel_dom:
                has_telemetry_sdk = True
                sev = "CRITICAL" if kind in ("code", "manifest_or_ci", "config_or_other") else "LOW"
                findings.append({"line": lineno, "severity": sev, "rule": "TELEMETRY_ENDPOINT", "detail": f"telemetry host {host}"})
            elif kind in ("code", "manifest_or_ci", "config_or_other") and host not in DOC_SAFE_HOSTS:
                findings.append({"line": lineno, "severity": "MEDIUM", "rule": "EXTERNAL_URL", "detail": f"external URL host {host}"})

        # 2. Public IPv4 literals (ignoring obvious version strings prefixed with 'v')
        for m in IPV4_RE.finditer(line):
            ip_str = m.group(1)
            prefix_char = line[m.start() - 1] if m.start() > 0 else " "
            if prefix_char.lower() == "v":
                continue
            if is_public_ip(ip_str):
                endpoints_found.add(ip_str)
                sev = "HIGH" if kind in ("code", "manifest_or_ci", "config_or_other") else "LOW"
                findings.append({"line": lineno, "severity": sev, "rule": "HARDCODED_PUBLIC_IP", "detail": f"hardcoded public IP `{ip_str}`"})

        # 3. Obfuscated URLs (base64 / hex / split)
        if kind != "doc":
            for host in decode_suspicious_base64(line):
                has_network = True
                endpoints_found.add(host)
                findings.append({"line": lineno, "severity": "CRITICAL", "rule": "OBFUSCATED_BASE64_ENDPOINT", "detail": f"Base64 decodes to endpoint host {host}"})
            if HEX_URL_RE.search(line):
                findings.append({"line": lineno, "severity": "CRITICAL", "rule": "OBFUSCATED_HEX_URL", "detail": "hex-encoded http(s)/wss URL prefix detected"})
            if SPLIT_URL_RE.search(line) and not any(f["line"] == lineno and f["rule"] == "TELEMETRY_ENDPOINT" for f in findings):
                findings.append({"line": lineno, "severity": "HIGH", "rule": "DYNAMIC_OR_TELEMETRY_URL", "detail": "dynamic or split endpoint or telemetry vendor string"})
            if CHARCODE_RE.search(line) and re.search(r"http|url|host|endpoint|telemetry|track", line, re.I):
                findings.append({"line": lineno, "severity": "HIGH", "rule": "OBFUSCATED_STRING_DECODE", "detail": "runtime decoding near an endpoint or telemetry symbol"})

        # 4. Outbound network sinks
        if kind in ("code", "manifest_or_ci") and NETWORK_SINK_RE.search(line):
            has_network = True
            findings.append({"line": lineno, "severity": "MEDIUM", "rule": "OUTBOUND_NETWORK_SINK", "detail": "outbound network primitive detected"})

        # 5. Telemetry SDK imports / calls
        if kind in ("code", "config_or_other"):
            low = line.lower()
            for sdk in TELEMETRY_SDKS:
                if sdk == "telemetry":
                    if re.search(r"\b(?:telemetry|sendTelemetry|trackEvent|captureEvent|reportTelemetry)\b", line):
                        has_telemetry_sdk = True
                        findings.append({"line": lineno, "severity": "HIGH", "rule": "TELEMETRY_CODE_PATH", "detail": "telemetry identifier detected"})
                    continue
                if re.search(rf"(?:import|from|require|use|include)\b[^\n]*{re.escape(sdk)}", low):
                    has_telemetry_sdk = True
                    findings.append({"line": lineno, "severity": "HIGH", "rule": "TELEMETRY_SDK_IMPORT", "detail": f"imports telemetry package {sdk}"})

        # 6. Default-on telemetry toggle
        if kind != "doc" and DEFAULT_ON_TELEMETRY_RE.search(line):
            has_telemetry_sdk = True
            findings.append({"line": lineno, "severity": "HIGH", "rule": "DEFAULT_ON_TELEMETRY", "detail": "opt-out or default-on telemetry guard detected"})

        # 7. Silent update / version check phone-home
        if kind in ("code", "config_or_other") and UPDATE_CHECK_RE.search(line):
            findings.append({"line": lineno, "severity": "MEDIUM", "rule": "UPDATE_CHECK_PHONE_HOME", "detail": "automatic update check detected"})

        # 8. Unscrubbed env dump to outbound/error reporter
        if kind == "code" and ENV_DUMP_RE.search(line):
            findings.append({"line": lineno, "severity": "HIGH", "rule": "ENV_EXFIL_OR_LEAK_RISK", "detail": "environment data may enter an outbound payload"})

        # Track file-level correlation signals
        if kind in ("code", "config_or_other"):
            if CRED_AND_AUTH_RE.search(line):
                has_cred_or_auth = True
            if HASHING_RE.search(line):
                has_hash = True
            if FINGERPRINT_RE.search(line):
                has_fingerprint = True

    # Correlated T3-Code-class compound detections across the whole file!
    if kind in ("code", "config_or_other"):
        if has_cred_or_auth and (has_hash or has_network or has_telemetry_sdk):
            findings.append({
                "line": 1,
                "severity": "CRITICAL",
                "rule": "CREDENTIAL_TELEMETRY_COMBO",
                "detail": (
                    "File accesses local auth/credential files or secret env vars AND performs "
                    + "+".join(
                        lbl
                        for lbl, flag in (("hashing", has_hash), ("network_io", has_network), ("telemetry", has_telemetry_sdk))
                        if flag
                    )
                    + "; inspect every line for credential-derived identifier harvesting."
                ),
            })
        if has_fingerprint and (has_network or has_telemetry_sdk or has_hash):
            findings.append({
                "line": 1,
                "severity": "HIGH",
                "rule": "FINGERPRINT_TELEMETRY_COMBO",
                "detail": "File collects persistent host/user/account identifiers alongside hashing or outbound telemetry.",
            })
        if minified:
            findings.append({
                "line": 1,
                "severity": "HIGH",
                "rule": "MINIFIED_BUNDLE_BLIND_SPOT",
                "detail": f"Single-line/minified text ({max_line_len} chars/line); beautify and inspect manually.",
            })

    # Deduplicate excessive findings per line/rule while keeping complete line visibility.
    deduped: List[Dict[str, object]] = []
    seen_lr = set()
    for f in findings:
        key = (f["line"], f["rule"])
        if key in seen_lr:
            continue
        seen_lr.add(key)
        deduped.append(f)

    max_sev = "CLEAN"
    for f in deduped:
        if SEVERITY_RANK[str(f["severity"])] > SEVERITY_RANK[max_sev]:
            max_sev = str(f["severity"])

    return {
        "path": rel,
        "kind": kind,
        "status": "SCANNED",
        "lines": len(lines),
        "bytes": len(raw),
        "sha256": digest,
        "minified": minified,
        "endpoints": sorted(endpoints_found),
        "max_severity": max_sev,
        "findings": deduped,
        "review": None,
    }


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    if not root.is_dir():
        raise Fail(f"repository directory not found: {root}")

    skip_dirs = set() if args.include_vendor else set(DEFAULT_SKIP_DIRS)
    skip_dirs.add(".git")
    skipped_encountered: List[str] = []
    skipped_files: List[str] = []
    files_records: List[Dict[str, object]] = []
    total_lines = 0
    sev_counts: Counter = Counter()
    all_endpoints: Set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        kept_dirs = []
        for d in dirnames:
            full_d = Path(dirpath) / d
            rel_d = full_d.relative_to(root).as_posix()
            if d in skip_dirs or full_d.is_symlink():
                if d != ".git":
                    skipped_encountered.append(rel_d)
            else:
                kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            rel = fpath.relative_to(root).as_posix()
            if fpath.is_symlink() or not fpath.is_file():
                skipped_files.append(rel)
                continue
            rec = scan_single_file(fpath, rel)
            files_records.append(rec)
            total_lines += int(rec.get("lines") or 0)
            sev_counts[str(rec["max_severity"])] += 1
            for ep in rec.get("endpoints") or []:
                all_endpoints.add(str(ep))

    review_queue = [
        {
            "path": r["path"],
            "kind": r["kind"],
            "lines": r["lines"],
            "max_severity": r["max_severity"],
            "finding_count": len(r["findings"]),
            "rules": sorted({str(f["rule"]) for f in r["findings"]}),
        }
        for r in files_records
        if SEVERITY_RANK[str(r["max_severity"])] >= SEVERITY_RANK["MEDIUM"]
    ]
    review_queue.sort(key=lambda x: (-SEVERITY_RANK[str(x["max_severity"])], str(x["path"])))

    binaries = [r["path"] for r in files_records if r["status"] == "BINARY_UNSCANNED"]
    minified_files = [r["path"] for r in files_records if r.get("minified")]

    ledger = {
        "format": "truffle-hunter-ledger-v1",
        "scanned_at": now_utc(),
        "repo": str(root),
        "summary": {
            "files_total": len(files_records),
            "lines_scanned": total_lines,
            "severity_files": dict(sev_counts),
            "review_queue_files": len(review_queue),
            "unique_external_hosts": sorted(all_endpoints),
        },
        "blind_spots": {
            "skipped_dirs": sorted(skipped_encountered),
            "skipped_files": sorted(skipped_files),
            "binary_files": binaries,
            "minified_files": minified_files,
        },
        "review_queue": review_queue,
        "files": files_records,
    }

    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    print(
        f"truffle-hunter scan: {len(files_records)} files, {total_lines:,} lines | "
        f"CRITICAL={sev_counts['CRITICAL']} HIGH={sev_counts['HIGH']} MEDIUM={sev_counts['MEDIUM']} "
        f"LOW={sev_counts['LOW']} CLEAN={sev_counts['CLEAN']}"
    )
    if all_endpoints:
        print(f"external hosts/IPs seen ({len(all_endpoints)}): {', '.join(sorted(all_endpoints)[:15])}")
    if skipped_encountered or skipped_files or binaries or minified_files:
        print(
            f"blind spots: skipped_dirs={len(skipped_encountered)} skipped_files={len(skipped_files)} "
            f"binaries={len(binaries)} minified={len(minified_files)}"
        )
    if review_queue:
        print(f"\nline-by-line review queue ({len(review_queue)} file(s)):")
        for item in review_queue[:30]:
            print(f"  [{item['max_severity']:<8}] {item['path']} ({item['lines']}L) -> {', '.join(item['rules'])}")
        return 1
    print("\nSTATIC SCAN: ZERO HIGH/MEDIUM SIGNALS (still inspect entrypoints, manifests and blind spots before verdict)")
    return 0


def covers_scanned_lines(line_count: int, span: object) -> bool:
    if not isinstance(span, str):
        return False
    allowed = {f"1-{line_count}"} if line_count > 0 else {"0"}
    if line_count == 1:
        allowed.add("1")
    return span.strip() in allowed


def cmd_record_review(args: argparse.Namespace) -> int:
    lp = Path(args.ledger)
    if not lp.is_file():
        raise Fail(f"ledger not found: {lp}")
    doc = json.loads(lp.read_text(encoding="utf-8"))
    if doc.get("format") != "truffle-hunter-ledger-v1":
        raise Fail(f"{lp}: invalid truffle-hunter ledger")

    target = args.file.removeprefix("./")
    rec = next((f for f in doc["files"] if f["path"] == target), None)
    if rec is None:
        raise Fail(f"file {target!r} not found in ledger")
    if rec.get("status") == "UNREADABLE":
        raise Fail(f"{target} was unreadable during the scan. Make it readable and rerun scan.")
    if not covers_scanned_lines(rec["lines"], args.lines):
        raise Fail(f"--lines must cover the entire scanned file ({rec['lines']} lines)")
    if not args.note.strip():
        raise Fail("--note is required")

    rec["review"] = {
        "disposition": args.disposition,
        "lines_checked": args.lines,
        "default_on": bool(args.default_on),
        "data_sent": args.data_sent or "",
        "note": args.note.strip(),
        "reviewed_at": now_utc(),
    }
    lp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"recorded review: {target} [{args.disposition}] lines={args.lines}")
    return 0


def cmd_verdict(args: argparse.Namespace) -> int:
    lp = Path(args.ledger)
    if not lp.is_file():
        raise Fail(f"ledger not found: {lp}")
    doc = json.loads(lp.read_text(encoding="utf-8"))
    if doc.get("format") != "truffle-hunter-ledger-v1":
        raise Fail(f"{lp}: invalid truffle-hunter ledger")

    unreviewed: List[str] = []
    sus_items: List[Dict[str, object]] = []
    benign_apis: List[Dict[str, object]] = []

    for f in doc["files"]:
        sev = str(f["max_severity"])
        rev = f.get("review")
        needs_review = SEVERITY_RANK[sev] >= SEVERITY_RANK["MEDIUM"]
        complete = (
            f.get("status") != "UNREADABLE" and isinstance(rev, dict)
            and rev.get("disposition") in REVIEW_DISPOSITIONS
            and covers_scanned_lines(f["lines"], rev.get("lines_checked"))
        )
        if needs_review and not complete:
            unreviewed.append(f"{f['path']} [{sev}]")
            continue
        if isinstance(rev, dict):
            disp = rev.get("disposition")
            if disp in ("CONFIRMED_TELEMETRY", "SUSPICIOUS"):
                sus_items.append({"path": f["path"], "findings": f["findings"], **rev})
            elif disp == "BENIGN_CORE_API":
                benign_apis.append({"path": f["path"], "endpoints": f.get("endpoints") or [], "note": rev.get("note")})

    s = doc["summary"]
    bs = doc["blind_spots"]
    print(f"coverage: {s['files_total']} files, {s['lines_scanned']:,} lines scanned")
    print(
        f"blind spots: skipped_dirs={len(bs['skipped_dirs'])} skipped_files={len(bs.get('skipped_files', []))} "
        f"binaries={len(bs['binary_files'])} minified={len(bs['minified_files'])}"
    )

    if unreviewed:
        print(f"unreviewed flagged files ({len(unreviewed)}):")
        for u in unreviewed[:20]:
            print(f"  - {u}")
        print("VERDICT: INCOMPLETE_AUDIT (read and record-review every CRITICAL/HIGH/MEDIUM file first)")
        return 1

    if sus_items:
        print(f"\nsuspicious or confirmed telemetry ({len(sus_items)} file(s)):")
        for item in sus_items:
            def_tag = " [DEFAULT-ON]" if item.get("default_on") else ""
            data_tag = f" | data: {item['data_sent']}" if item.get("data_sent") else ""
            print(f"  - {item['path']} ({item['lines_checked']}) [{item['disposition']}]{def_tag}{data_tag}")
            print(f"    {item['note']}")
        print("\nVERDICT: This part looks sus")
        return 1

    if benign_apis:
        print(f"\nverified benign core-functionality endpoints ({len(benign_apis)} file(s)):")
        for b in benign_apis:
            print(f"  - {b['path']}: {b['note']}")
    print("\nVERDICT: Zero telemetry is transmitted as far as I can tell")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="scan every file and line in a repository for telemetry and covert exfiltration")
    p_scan.add_argument("repo", help="path to repository root")
    p_scan.add_argument("--out", help="write full audit ledger JSON")
    p_scan.add_argument("--include-vendor", action="store_true", help="also scan node_modules, dist, build, target, venv")

    p_rev = sub.add_parser("record-review", help="record a line-verified review disposition for one file")
    p_rev.add_argument("--ledger", required=True, help="path to audit ledger JSON")
    p_rev.add_argument("--file", required=True, help="relative file path inside the repository")
    p_rev.add_argument("--lines", required=True, help="full scanned line range (e.g. 1-145, or 0 for an empty file)")
    p_rev.add_argument("--disposition", required=True, choices=REVIEW_DISPOSITIONS)
    p_rev.add_argument("--default-on", action="store_true", help="mark telemetry/phone-home as enabled by default")
    p_rev.add_argument("--data-sent", help="exact fields, hashes or identifiers transmitted")
    p_rev.add_argument("--note", required=True, help="concise evidence-backed explanation")

    p_ver = sub.add_parser("verdict", help="verify 100% review completion and print the final verdict")
    p_ver.add_argument("--ledger", required=True, help="path to audit ledger JSON")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "scan":
            return cmd_scan(args)
        if args.cmd == "record-review":
            return cmd_record_review(args)
        if args.cmd == "verdict":
            return cmd_verdict(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"truffle_hunter.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
