#!/usr/bin/env python3
"""Check a target and a technique class against an engagement scope file.

Usage:
  scope_check.py --scope scope.json --target TARGET --technique CLASS [--now ISO]
  scope_check.py --scope scope.json --validate
  scope_check.py --list-techniques

Exit codes:
  0  IN SCOPE (with --validate: the scope file is valid and complete)
  1  not in scope, or the scope file is incomplete. The reason is printed.
  2  malformed scope file or bad usage

The check passes only when all of these hold:
  - the scope file is complete (authorizer, reference, window, contacts)
  - the target matches an in_scope rule
  - the target matches no out_of_scope rule (out of scope always wins)
  - the technique class is known, allowed, not forbidden, and fits the
    kind of target
  - the current time is inside the testing window

Matching rules:
  - Host names are compared as text, in lower case, without a trailing dot
    and without a port. Internationalized names are compared in their ASCII
    (punycode) form.
  - "example.com" matches that name only. "*.example.com" matches every name
    under example.com at any depth, but not example.com itself. This holds
    for in_scope and out_of_scope alike.
  - This tool never resolves a name. A host name is never in scope because of
    the address it resolves to. DNS resolution is the operator's job, and
    every resolved address must be checked with this tool as well.
  - Addresses are matched against CIDR ranges. An IPv4-mapped IPv6 address is
    checked as the IPv4 address inside it. Other transition formats are
    compared as plain IPv6. A range given as the target must lie fully inside
    in_scope and must not overlap out_of_scope. An address in a non-standard
    form (one decimal number, hex, or parts with leading zeros) is refused.
  - A URL is in scope when its host is in scope, or when an in_scope URL rule
    has the same scheme, host and port and a path that is a prefix of the
    URL path on a segment boundary. The text before "@" is user info, never
    the host. Out-of-scope URL rules compare the path without regard to case
    and treat ports 80 and 443 as the same site. URLs are split by one strict
    rule in this file, so the result does not depend on the Python version.
    A URL that tools could read in two ways is refused.
  - Binaries and firmware images are matched by SHA-256. Cloud accounts,
    repositories and hardware are matched by identifier.

Standard library only. Tested on Python 3.9 to 3.14.
"""

import argparse
import hashlib
import ipaddress
import json
import re
import sys
from collections import namedtuple
from datetime import datetime, timezone
from urllib.parse import unquote

EXIT_IN_SCOPE = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2

TARGET_LISTS = (
    "hostnames",
    "ip_ranges",
    "urls",
    "cloud_accounts",
    "repositories",
    "artifacts",
    "hardware",
)
NETWORK_TYPES = ("hostname", "ip", "url")
ALL_TYPES = NETWORK_TYPES + ("cloud-account", "repository", "artifact", "hardware")
TYPE_CHOICES = ("auto",) + ALL_TYPES

# Technique classes this skill can perform: description, target kinds.
TECHNIQUES = {
    "passive-recon": (
        "Public sources only. Nothing is sent to the target.",
        ALL_TYPES,
    ),
    "active-scan": (
        "Non-intrusive enumeration: host and port discovery, service banners, "
        "fetching public pages.",
        NETWORK_TYPES,
    ),
    "vuln-scan": (
        "Automated checks for known vulnerabilities.",
        NETWORK_TYPES,
    ),
    "web-testing": (
        "Manual testing of web application inputs and logic.",
        NETWORK_TYPES,
    ),
    "auth-testing": (
        "Testing sign-in and session handling within the agreed limits.",
        NETWORK_TYPES + ("cloud-account",),
    ),
    "exploitation": (
        "Proving a vulnerability with the least harmful proof.",
        NETWORK_TYPES + ("cloud-account", "hardware"),
    ),
    "post-exploitation": (
        "Actions after access is gained: raising privileges, using found "
        "credentials, moving to other systems.",
        NETWORK_TYPES + ("cloud-account", "hardware"),
    ),
    "cloud-review": (
        "Reading cloud configuration through the provider interface with "
        "credentials the operator supplied.",
        ("cloud-account",),
    ),
    "source-review": (
        "Reading source code and configuration.",
        ("repository",),
    ),
    "static-analysis": (
        "Inspecting a binary or firmware image without running it.",
        ("artifact", "repository"),
    ),
    "dynamic-analysis": (
        "Running or debugging a binary in an isolated lab.",
        ("artifact",),
    ),
    "hardware-testing": (
        "Bench work on a device: debug interfaces, bus capture, reading storage.",
        ("hardware",),
    ),
    "firmware-modification": (
        "Writing firmware or configuration to a device.",
        ("hardware",),
    ),
}

# Classes the checker knows by name so a scope file can forbid them.
# They are refused even if a scope file tries to allow them.
NEVER = {
    "denial-of-service": "this skill does not degrade or interrupt a service",
    "detection-evasion": "this skill does not evade or disable monitoring or detection",
    "covert-exfiltration": "this skill does not build hidden channels to move data out",
    "evidence-destruction": "this skill does not destroy evidence, clear logs or wipe storage",
    "social-engineering": "this skill does not cover deceiving people",
    "physical-entry": "this skill does not cover entering premises",
}

# Classes that always need the operator's yes for the specific action.
GATED_ALWAYS = ("post-exploitation", "firmware-modification")
GATED_NOTES = {
    "post-exploitation": "required. Get the operator's explicit yes for this "
    "specific action before you run it.",
    "firmware-modification": "required. Get the operator's explicit yes for this "
    "specific action before you run it.",
    "exploitation": "required for anything beyond a minimal proof that changes "
    "nothing and reads no data.",
    "auth-testing": "required before guessing passwords or using any credential "
    "that was not issued to you for this test.",
}

DNS_NOTE = (
    "host names are matched as text. Resolving the name is the operator's job. "
    "Check every resolved address with this tool before you connect."
)

DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21, "ssh": 22}

_DOTS = re.compile("[\u002e\u3002\uff0e\uff61]")
# Characters that IDNA 2003 and IDNA 2008 map to different names.
_DEVIATION = frozenset("\u00df\u03c2\u200c\u200d")
_LABEL = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PORT = re.compile(r"^[0-9]{1,5}$")
# scheme://authority/path. The authority ends at the first "/", "?" or "#".
_URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)([^?#]*)")
_BRACKETED = re.compile(r"^\[([0-9A-Fa-f:.]+)\](?::([0-9]*))?$")
_SCP = re.compile(r"^(?:[A-Za-z0-9._-]+@)?([^@:/\s]+):(?!/)([^\s]+)$")
# Reserved names that never exist in the public DNS. A wildcard directly
# under one of these is allowed, for labs. Under any other single label
# it is refused as too wide.
_PRIVATE_SUFFIXES = ("test", "example", "invalid", "localhost", "local", "internal")

HostRule = namedtuple("HostRule", "wildcard base text")
NetRule = namedtuple("NetRule", "network text")
UrlRule = namedtuple("UrlRule", "url text")
CloudRule = namedtuple("CloudRule", "key text")
RepoRule = namedtuple("RepoRule", "host segments text")
ArtifactRule = namedtuple("ArtifactRule", "sha256 text")
HardwareRule = namedtuple("HardwareRule", "key text operator_owned restore_image_sha256")
ParsedUrl = namedtuple("ParsedUrl", "scheme host ip port segments ambiguous userinfo text")


class ScopeFileError(Exception):
    """The scope file cannot be read or is not JSON."""


class TargetError(Exception):
    """The target cannot be read safely. The check refuses it."""


# ---------------------------------------------------------------------------
# Normalizing names, addresses and URLs
# ---------------------------------------------------------------------------


def _has_space_or_control(text):
    return any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _ends_in_number(host):
    last = host.rsplit(".", 1)[-1]
    return bool(re.match(r"^([0-9]+|0x[0-9a-f]*)$", last))


def normalize_hostname(name):
    """Return the ASCII, lower-case form of a host name without a trailing dot.

    Raises TargetError when the name cannot be compared safely.
    """
    if not name:
        raise TargetError("host name is empty")
    if _has_space_or_control(name):
        raise TargetError('host name "%s" contains white space or control characters' % name)
    if any(ch in _DEVIATION for ch in name):
        raise TargetError(
            'host name "%s" contains a character that IDNA versions map to '
            "different names. Give the ASCII (punycode) form." % name
        )
    labels = _DOTS.split(name)
    if labels and labels[-1] == "":
        labels.pop()
    if not labels:
        raise TargetError('host name "%s" has no labels' % name)
    out = []
    for label in labels:
        if label == "":
            raise TargetError('host name "%s" has an empty label' % name)
        if label.isascii():
            ascii_label = label
        else:
            try:
                ascii_label = label.encode("idna").decode("ascii")
            except UnicodeError:
                raise TargetError(
                    'host name "%s" cannot be converted to ASCII. '
                    "Give the punycode form." % name
                )
        ascii_label = ascii_label.lower()
        if not _LABEL.match(ascii_label):
            raise TargetError(
                '"%s" is not a valid host name (label "%s")' % (name, ascii_label)
            )
        out.append(ascii_label)
    host = ".".join(out)
    if len(host) > 253:
        raise TargetError('host name "%s" is too long' % name)
    if _ends_in_number(host):
        raise TargetError(
            '"%s" ends in a number, so tools may read it as an address in a '
            "non-standard form. Write the address in standard notation." % name
        )
    return host


def _odd_ipv4_part(text):
    """True if a dotted part has a leading zero. Tools disagree on what it means."""
    dotted = text.rsplit(":", 1)[-1].split("/", 1)[0]
    if "." not in dotted:
        return False
    return any(len(part) > 1 and part.startswith("0") for part in dotted.split("."))


def _parse_ip(text):
    """Return (address, was_mapped), or None. IPv4-mapped IPv6 becomes IPv4."""
    if "%" in text or _odd_ipv4_part(text):
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped, True
    return address, False


def _parse_network(text):
    """Return an ip_network. Raises ValueError with a plain message."""
    if _odd_ipv4_part(text):
        raise ValueError("has a number with a leading zero. Write it in standard notation")
    try:
        network = ipaddress.ip_network(text, strict=True)
    except ValueError as exc:
        if "host bits" in str(exc):
            raise ValueError(
                "has host bits set. Write the network address, or a single "
                "address without a prefix"
            )
        raise ValueError("is not a valid address or CIDR range")
    if network.version == 6 and network.network_address.ipv4_mapped is not None:
        raise ValueError("is an IPv4-mapped IPv6 range. Write it as IPv4")
    return network


def _path_segments(raw_path):
    """Return (segments, ambiguous) for a URL path.

    Dot segments and repeated slashes are resolved. The path is ambiguous
    when servers disagree on how to read it.
    """
    lowered = raw_path.lower()
    ambiguous = ";" in raw_path or any(
        token in lowered for token in ("%2f", "%5c", "%25", "%3b", "%00")
    )
    decoded = unquote(raw_path)
    if "\\" in decoded or "\x00" in decoded or "\ufffd" in decoded:
        ambiguous = True
    segments = []
    for segment in decoded.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    return segments, ambiguous


def parse_url(text):
    """Parse an absolute URL into a ParsedUrl. Raises TargetError."""
    if _has_space_or_control(text):
        raise TargetError("URL contains white space or control characters")
    if "\\" in text:
        raise TargetError(
            "URL contains a backslash. Tools disagree on what it means, so it is refused."
        )
    # The URL is split here, by one strict rule, and not by a library. Library
    # parsers differ between versions on odd input, and a difference is a hole.
    match = _URL.match(text)
    if not match:
        raise TargetError('"%s" is not an absolute URL with a valid scheme' % text)
    scheme = match.group(1).lower()
    authority = match.group(2)
    raw_path = match.group(3)
    host_port = authority.rpartition("@")[2]
    port_text = ""
    bracketed = False
    if host_port.startswith("["):
        inner = _BRACKETED.match(host_port)
        if not inner:
            raise TargetError('cannot read the host in URL "%s"' % text)
        raw_host, port_text = inner.group(1), inner.group(2) or ""
        bracketed = True
        if ":" not in raw_host:
            raise TargetError('brackets in URL "%s" do not hold an IPv6 address' % text)
    elif host_port.count(":") > 1:
        raise TargetError('cannot read the host in URL "%s"' % text)
    else:
        raw_host, _, port_text = host_port.partition(":")
    if not raw_host:
        raise TargetError('URL "%s" has no host' % text)
    port = None
    if port_text:
        if not _PORT.match(port_text) or int(port_text) > 65535:
            raise TargetError('the port in URL "%s" is not a number from 0 to 65535' % text)
        port = int(port_text)
    host = None
    address = None
    parsed_ip = _parse_ip(raw_host)
    if parsed_ip is not None:
        address = parsed_ip[0]
    elif bracketed:
        raise TargetError('"[%s]" in URL "%s" is not an IP address' % (raw_host, text))
    else:
        host = normalize_hostname(raw_host)
    if address is not None and address.version == 6 and not bracketed:
        raise TargetError('cannot read the host in URL "%s"' % text)
    if port is None:
        port = DEFAULT_PORTS.get(scheme)
    segments, ambiguous = _path_segments(raw_path)
    shown_host = host
    if address is not None:
        shown_host = "[%s]" % address if address.version == 6 else str(address)
    shown_port = ""
    if port is not None and port != DEFAULT_PORTS.get(scheme):
        shown_port = ":%d" % port
    shown = "%s://%s%s/%s" % (scheme, shown_host, shown_port, "/".join(segments))
    userinfo = "@" in authority
    return ParsedUrl(scheme, host, address, port, segments, ambiguous, userinfo, shown)


def normalize_repo(text):
    """Return (host, path segments) for a repository reference. Raises TargetError."""
    if _has_space_or_control(text):
        raise TargetError("repository reference contains white space")
    if "://" in text:
        url = parse_url(text)
        if url.ambiguous:
            raise TargetError('repository path in "%s" is ambiguous' % text)
        host = url.host if url.host is not None else str(url.ip)
        segments = list(url.segments)
    else:
        match = _SCP.match(text)
        if match and "/" not in match.group(1):
            host_text, path_text = match.group(1), match.group(2)
        elif "/" in text:
            host_text, _, path_text = text.partition("/")
        else:
            raise TargetError('"%s" is not a repository reference' % text)
        host = normalize_hostname(host_text)
        segments = [part for part in path_text.split("/") if part != ""]
        if any(part in (".", "..") for part in segments):
            raise TargetError('repository path in "%s" has dot segments' % text)
    if segments and segments[-1].endswith(".git"):
        segments[-1] = segments[-1][:-4]
    if not segments or not segments[-1]:
        raise TargetError('repository reference "%s" has no path' % text)
    return host, segments


def _repo_text(host, segments):
    return "%s/%s" % (host, "/".join(segments))


def parse_time(text):
    """Parse an ISO 8601 time that carries a time zone. Raises ValueError."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("is empty")
    value = text.strip()
    if value[-1] in "zZ":
        value = value[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("is not an ISO 8601 date and time")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("has no time zone")
    return moment


# ---------------------------------------------------------------------------
# Loading and validating the scope file
# ---------------------------------------------------------------------------


def _blank(value):
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    if text.startswith("<") and text.endswith(">"):
        return True
    return text.upper() in ("TODO", "TBD", "CHANGEME", "FIXME")


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ScopeFileError('scope file repeats the key "%s"' % key)
        seen[key] = value
    return seen


def _strip_comments(value):
    """Drop keys that start with an underscore. They are comments."""
    if isinstance(value, dict):
        return {
            key: _strip_comments(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_strip_comments(item) for item in value]
    return value


class Scope(object):
    """A parsed scope file. Check .malformed and .incomplete before use."""

    def __init__(self):
        self.path = None
        self.sha256 = None
        self.malformed = []
        self.incomplete = []
        self.notes = []
        self.name = ""
        self.window_start = None
        self.window_end = None
        self.in_scope = dict((key, []) for key in TARGET_LISTS)
        self.out_of_scope = dict((key, []) for key in TARGET_LISTS)
        self.allowed = []
        self.forbidden = []

    def window_state(self, now):
        if self.window_start is None or self.window_end is None:
            return "unknown"
        if now < self.window_start:
            return "not started"
        if now >= self.window_end:
            return "closed"
        return "open"


def _dict_section(raw, key, allowed_keys, where, scope):
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        scope.malformed.append("%s must be an object" % where)
        return {}
    for name in sorted(value):
        if name not in allowed_keys:
            scope.malformed.append(
                '%s has an unknown key "%s". Comment keys must start with an underscore.'
                % (where, name)
            )
    return value


def _text_field(section, key, where, scope, required):
    value = section.get(key)
    if value is not None and not isinstance(value, str):
        scope.malformed.append("%s.%s must be a string" % (where, key))
        return ""
    if _blank(value):
        if required:
            scope.incomplete.append("%s.%s is empty" % (where, key))
        return ""
    return value.strip()


def _text_list(section, key, where, scope, required):
    value = section.get(key)
    label = "%s.%s" % (where, key) if where else key
    if value is None:
        value = []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        scope.malformed.append("%s must be a list of strings" % label)
        return []
    items = [item.strip() for item in value if not _blank(item)]
    if required and not items:
        scope.incomplete.append("%s is empty" % label)
    return items


def _parse_host_rule(text):
    wildcard = False
    base = text
    if text.startswith("*."):
        wildcard = True
        base = text[2:]
    if "*" in base:
        raise ValueError(
            'uses a wildcard in an unsupported place. Only the form "*.example.com" is allowed'
        )
    if _parse_ip(base) is not None or "/" in base:
        raise ValueError("is an address. Put addresses in ip_ranges")
    try:
        host = normalize_hostname(base)
    except TargetError as exc:
        raise ValueError("is not usable: %s" % exc)
    if wildcard and "." not in host and host not in _PRIVATE_SUFFIXES:
        raise ValueError("is too wide. A wildcard needs at least two labels after it")
    return HostRule(wildcard, host, ("*." + host) if wildcard else host)


def _parse_url_rule(text):
    try:
        url = parse_url(text)
    except TargetError as exc:
        raise ValueError("is not usable: %s" % exc)
    if "://" not in text:
        raise ValueError("must be an absolute URL with a scheme")
    if url.userinfo:
        raise ValueError("must not carry user info")
    if "?" in text or "#" in text:
        raise ValueError("must not carry a query or fragment. Rules match by path prefix")
    if url.ambiguous:
        raise ValueError("has an ambiguous path. Write it decoded and without ';'")
    return UrlRule(url, url.text)


def _parse_cloud_rule(item):
    if isinstance(item, dict):
        unknown = sorted(set(item) - {"provider", "id", "note"})
        if unknown:
            raise ValueError('has an unknown key "%s"' % unknown[0])
        provider, ident = item.get("provider"), item.get("id")
    elif isinstance(item, str) and ":" in item:
        provider, _, ident = item.partition(":")
    else:
        raise ValueError('must be an object with "provider" and "id", or "provider:id"')
    if not isinstance(provider, str) or not isinstance(ident, str):
        raise ValueError("provider and id must be strings")
    provider, ident = provider.strip(), ident.strip()
    if _blank(provider) or _blank(ident):
        raise ValueError("provider and id must not be empty")
    if _has_space_or_control(provider) or _has_space_or_control(ident) or ":" in provider:
        raise ValueError("provider and id must not contain white space")
    key = ("%s:%s" % (provider, ident)).casefold()
    return CloudRule(key, "%s:%s" % (provider, ident))


def _parse_repo_rule(text):
    try:
        host, segments = normalize_repo(text)
    except TargetError as exc:
        raise ValueError("is not usable: %s" % exc)
    return RepoRule(host, segments, _repo_text(host, segments))


def _parse_artifact_rule(item):
    if isinstance(item, dict):
        unknown = sorted(set(item) - {"sha256", "description"})
        if unknown:
            raise ValueError('has an unknown key "%s"' % unknown[0])
        digest = item.get("sha256")
    else:
        digest = item
    if not isinstance(digest, str):
        raise ValueError('must be a SHA-256 string or an object with "sha256"')
    digest = digest.strip().lower()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    if not _SHA256.match(digest):
        raise ValueError("is not a SHA-256 value (64 hexadecimal characters)")
    return ArtifactRule(digest, digest)


def _parse_hardware_rule(item):
    owned = False
    restore = ""
    if isinstance(item, dict):
        allowed = {"id", "description", "operator_owned", "restore_image_sha256"}
        unknown = sorted(set(item) - allowed)
        if unknown:
            raise ValueError('has an unknown key "%s"' % unknown[0])
        ident = item.get("id")
        owned = item.get("operator_owned", False)
        restore = item.get("restore_image_sha256", "")
        if not isinstance(owned, bool):
            raise ValueError("operator_owned must be true or false")
        if restore is None:
            restore = ""
        if not isinstance(restore, str):
            raise ValueError("restore_image_sha256 must be a string")
        restore = restore.strip().lower()
        if _blank(restore):
            restore = ""
        elif not _SHA256.match(restore):
            raise ValueError("restore_image_sha256 is not a SHA-256 value")
    else:
        ident = item
    if not isinstance(ident, str) or _blank(ident):
        raise ValueError('needs a non-empty "id" (serial number or label)')
    ident = " ".join(ident.split())
    return HardwareRule(ident.casefold(), ident, owned, restore)


_RULE_PARSERS = {
    "cloud_accounts": _parse_cloud_rule,
    "artifacts": _parse_artifact_rule,
    "hardware": _parse_hardware_rule,
}
_STRING_RULE_PARSERS = {
    "hostnames": _parse_host_rule,
    "urls": _parse_url_rule,
    "repositories": _parse_repo_rule,
}


def _parse_target_lists(section, where, scope):
    rules = dict((key, []) for key in TARGET_LISTS)
    for key in TARGET_LISTS:
        items = section.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            scope.malformed.append("%s.%s must be a list" % (where, key))
            continue
        for index, item in enumerate(items):
            label = "%s.%s[%d]" % (where, key, index)
            shown = item if isinstance(item, str) else json.dumps(item, sort_keys=True)
            try:
                if key == "ip_ranges":
                    if not isinstance(item, str) or not item.strip():
                        raise ValueError("must be a non-empty string")
                    network = _parse_network(item.strip())
                    if network.prefixlen == 0:
                        raise ValueError("covers every address. Name the real ranges")
                    rules[key].append(NetRule(network, str(network)))
                elif key in _STRING_RULE_PARSERS:
                    if not isinstance(item, str) or not item.strip():
                        raise ValueError("must be a non-empty string")
                    rules[key].append(_STRING_RULE_PARSERS[key](item.strip()))
                else:
                    rules[key].append(_RULE_PARSERS[key](item))
            except ValueError as exc:
                scope.malformed.append('%s "%s" %s' % (label, shown, exc))
    return rules


def _host_rule_matches(rule, host):
    if rule.wildcard:
        return host.endswith("." + rule.base)
    return host == rule.base


def _exclusion_notes(scope):
    """Point out bare out-of-scope names whose subdomains stay in scope."""
    for out_rule in scope.out_of_scope["hostnames"]:
        if out_rule.wildcard:
            continue
        probe = "x." + out_rule.base
        still_in = any(_host_rule_matches(rule, probe) for rule in scope.in_scope["hostnames"])
        covered = any(_host_rule_matches(rule, probe) for rule in scope.out_of_scope["hostnames"])
        if still_in and not covered:
            scope.notes.append(
                'out_of_scope.hostnames "%s" excludes that name only. Names under it '
                'stay in scope. Add "*.%s" to exclude them too.' % (out_rule.text, out_rule.base)
            )


def parse_scope(raw, scope=None):
    """Validate a decoded scope document and return a Scope."""
    scope = scope or Scope()
    if not isinstance(raw, dict):
        scope.malformed.append("scope file must be a JSON object")
        return scope
    raw = _strip_comments(raw)
    top_keys = {
        "schema_version",
        "engagement",
        "authorization",
        "window",
        "in_scope",
        "out_of_scope",
        "techniques",
        "data_handling",
        "stop_conditions",
        "emergency_contact",
    }
    for name in sorted(raw):
        if name not in top_keys:
            scope.malformed.append(
                'unknown top-level key "%s". Comment keys must start with an underscore.' % name
            )
    if raw.get("schema_version") != 1:
        scope.malformed.append("schema_version must be 1")

    engagement = _dict_section(raw, "engagement", {"name", "description"}, "engagement", scope)
    scope.name = _text_field(engagement, "name", "engagement", scope, True)
    _text_field(engagement, "description", "engagement", scope, False)

    authorization = _dict_section(
        raw, "authorization", {"authorized_by", "reference"}, "authorization", scope
    )
    authorizer = _dict_section(
        authorization,
        "authorized_by",
        {"name", "role", "contact"},
        "authorization.authorized_by",
        scope,
    )
    for field in ("name", "role", "contact"):
        _text_field(authorizer, field, "authorization.authorized_by", scope, True)
    _text_field(authorization, "reference", "authorization", scope, True)

    window = _dict_section(raw, "window", {"start", "end"}, "window", scope)
    moments = {}
    for field in ("start", "end"):
        value = window.get(field)
        if value is not None and not isinstance(value, str):
            scope.malformed.append("window.%s must be a string" % field)
        elif _blank(value):
            scope.incomplete.append("window.%s is empty" % field)
        else:
            try:
                moments[field] = parse_time(value)
            except ValueError as exc:
                scope.malformed.append('window.%s "%s" %s' % (field, value, exc))
    if "start" in moments and "end" in moments:
        if moments["end"] <= moments["start"]:
            scope.malformed.append("window.end must be later than window.start")
        else:
            scope.window_start = moments["start"]
            scope.window_end = moments["end"]

    in_section = _dict_section(raw, "in_scope", set(TARGET_LISTS), "in_scope", scope)
    out_section = _dict_section(raw, "out_of_scope", set(TARGET_LISTS), "out_of_scope", scope)
    scope.in_scope = _parse_target_lists(in_section, "in_scope", scope)
    scope.out_of_scope = _parse_target_lists(out_section, "out_of_scope", scope)
    if not any(scope.in_scope[key] for key in TARGET_LISTS):
        scope.incomplete.append("in_scope lists no targets")

    techniques = _dict_section(raw, "techniques", {"allowed", "forbidden"}, "techniques", scope)
    scope.allowed = _text_list(techniques, "allowed", "techniques", scope, True)
    scope.forbidden = _text_list(techniques, "forbidden", "techniques", scope, False)
    for name in scope.allowed:
        if name in NEVER:
            scope.malformed.append(
                'techniques.allowed lists "%s", which cannot be allowed: %s' % (name, NEVER[name])
            )
        elif name not in TECHNIQUES:
            scope.malformed.append('techniques.allowed lists an unknown class "%s"' % name)
    for name in scope.forbidden:
        if name not in TECHNIQUES and name not in NEVER:
            scope.malformed.append('techniques.forbidden lists an unknown class "%s"' % name)
    for name in sorted(set(scope.allowed) & set(scope.forbidden)):
        scope.notes.append(
            'technique class "%s" is both allowed and forbidden. Forbidden wins.' % name
        )

    data = _dict_section(
        raw, "data_handling", {"rules", "evidence_location", "retention"}, "data_handling", scope
    )
    _text_list(data, "rules", "data_handling", scope, True)
    _text_field(data, "evidence_location", "data_handling", scope, False)
    _text_field(data, "retention", "data_handling", scope, True)

    _text_list(raw, "stop_conditions", "", scope, True)

    contact = _dict_section(
        raw, "emergency_contact", {"name", "contact"}, "emergency_contact", scope
    )
    for field in ("name", "contact"):
        _text_field(contact, field, "emergency_contact", scope, True)

    _exclusion_notes(scope)
    return scope


def load_scope(path):
    """Read and validate a scope file. Raises ScopeFileError if unreadable."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ScopeFileError("cannot read scope file %s: %s" % (path, exc.strerror or exc))
    scope = Scope()
    scope.path = path
    scope.sha256 = hashlib.sha256(data).hexdigest()
    try:
        raw = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_no_duplicate_keys)
    except UnicodeDecodeError:
        raise ScopeFileError("scope file %s is not UTF-8 text" % path)
    except ValueError as exc:
        raise ScopeFileError("scope file %s is not valid JSON: %s" % (path, exc))
    return parse_scope(raw, scope)


# ---------------------------------------------------------------------------
# Reading the target
# ---------------------------------------------------------------------------


class Target(object):
    def __init__(self, kind, text):
        self.kind = kind
        self.text = text
        self.host = None
        self.ip = None
        self.url = None
        self.repo = None
        self.key = None
        self.notes = []


def _ip_target(address, mapped, original):
    target = Target("ip", str(address))
    target.ip = address
    if mapped:
        target.notes.append(
            "%s is an IPv4-mapped IPv6 address. It was checked as %s." % (original, address)
        )
    return target


def _identifier_target(text, scope):
    """Match a cloud account or hardware identifier that the scope file lists."""
    folded = " ".join(text.split()).casefold()
    for group in (scope.in_scope, scope.out_of_scope):
        for rule in group["hardware"]:
            if rule.key == folded:
                target = Target("hardware", rule.text)
                target.key = folded
                return target
        for rule in group["cloud_accounts"]:
            if rule.key == folded:
                target = Target("cloud-account", rule.text)
                target.key = folded
                return target
    return None


def _host_target(text):
    parsed = _parse_ip(text)
    if parsed is not None:
        return _ip_target(parsed[0], parsed[1], text)
    port = None
    host_text = text
    if text.startswith("["):
        match = re.match(r"^\[([0-9A-Fa-f:.]+)\](?::([0-9]{1,5}))?$", text)
        parsed = _parse_ip(match.group(1)) if match and ":" in match.group(1) else None
        if parsed is None:
            raise TargetError('cannot read "%s" as a bracketed IPv6 address' % text)
        return _ip_target(parsed[0], parsed[1], match.group(1))
    if text.count(":") == 1:
        host_text, _, port = text.partition(":")
        if not _PORT.match(port) or int(port) > 65535:
            raise TargetError('the port in "%s" is not a number from 0 to 65535' % text)
        parsed = _parse_ip(host_text)
        if parsed is not None:
            return _ip_target(parsed[0], parsed[1], host_text)
    elif ":" in text:
        raise TargetError('"%s" is not a valid host name, address, URL or SHA-256 value' % text)
    host = normalize_hostname(host_text)
    target = Target("hostname", host)
    target.host = host
    if host != host_text:
        target.notes.append('"%s" was compared as "%s".' % (host_text, host))
    if port is not None:
        target.notes.append("host name rules cover every port. Port %s was not compared." % port)
    return target


def _url_target(text):
    url = parse_url(text)
    target = Target("url", url.text)
    target.url = url
    target.host = url.host
    target.ip = url.ip
    try:
        target.repo = normalize_repo(text)
    except TargetError:
        target.repo = None
    if url.userinfo:
        target.notes.append(
            'the text before "@" is user info. The host is "%s".'
            % (url.host if url.host is not None else url.ip)
        )
    return target


def parse_target(raw, forced, scope):
    """Work out what kind of target this is. Raises TargetError."""
    text = raw.strip()
    if not text:
        raise TargetError("target is empty")
    if forced == "hardware":
        target = Target("hardware", " ".join(text.split()))
        target.key = target.text.casefold()
        return target
    if forced == "auto":
        listed = _identifier_target(text, scope)
        if listed is not None:
            return listed
    if _has_space_or_control(text):
        raise TargetError("target contains white space or control characters")
    if forced == "cloud-account":
        if ":" not in text:
            raise TargetError('a cloud account target has the form "provider:id"')
        target = Target("cloud-account", text)
        target.key = text.casefold()
        return target
    digest = text.lower()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    if forced == "artifact" or (forced == "auto" and _SHA256.match(digest)):
        if not _SHA256.match(digest):
            raise TargetError("an artifact target is a SHA-256 value (64 hexadecimal characters)")
        target = Target("artifact", digest)
        target.key = digest
        return target
    if forced == "repository":
        host, segments = normalize_repo(text)
        target = Target("repository", _repo_text(host, segments))
        target.repo = (host, segments)
        return target
    if forced == "url" or (forced == "auto" and "://" in text):
        if "://" not in text:
            raise TargetError("a URL target needs a scheme, as in https://example.com/")
        return _url_target(text)
    if "/" in text:
        try:
            network = _parse_network(text)
        except ValueError as exc:
            network = None
            network_error = str(exc)
        if network is not None:
            if forced not in ("auto", "ip"):
                raise TargetError('"%s" is an address range, not a %s' % (text, forced))
            target = Target("ip", str(network))
            target.ip = network
            return target
        if forced == "auto":
            try:
                host, segments = normalize_repo(text)
            except TargetError:
                host = None
            if host is not None:
                for group in (scope.in_scope, scope.out_of_scope):
                    for rule in group["repositories"]:
                        if rule.host == host and [s.casefold() for s in segments[: len(rule.segments)]] == [
                            s.casefold() for s in rule.segments
                        ]:
                            target = Target("repository", _repo_text(host, segments))
                            target.repo = (host, segments)
                            return target
        if re.match(r"^[0-9A-Fa-f:.]+/[0-9]+$", text):
            raise TargetError('address range "%s" %s' % (text, network_error))
        raise TargetError(
            '"%s" has a path but no scheme. Give a full URL, or the host name alone.' % text
        )
    if forced == "auto" and _SCP.match(text) and "@" in text:
        host, segments = normalize_repo(text)
        target = Target("repository", _repo_text(host, segments))
        target.repo = (host, segments)
        return target
    target = _host_target(text)
    if forced == "ip" and target.kind != "ip":
        raise TargetError('"%s" is not an IP address or CIDR range' % text)
    if forced == "hostname" and target.kind != "hostname":
        raise TargetError('"%s" is an address, not a host name' % text)
    return target


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def _ip_inside(network, item):
    if item.version != network.version:
        return False
    if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return item.subnet_of(network)
    return item in network


def _ip_touches(network, item):
    if item.version != network.version:
        return False
    if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return item.overlaps(network)
    return item in network


def _starts_with(target_segments, rule_segments, loose):
    if len(target_segments) < len(rule_segments):
        return False
    head = target_segments[: len(rule_segments)]
    if loose:
        return [part.casefold() for part in head] == [part.casefold() for part in rule_segments]
    return head == rule_segments


def _same_host(rule_url, url):
    if rule_url.host is not None:
        return rule_url.host == url.host
    return url.ip is not None and rule_url.ip == url.ip


def _loose_port(rule_port, port):
    return rule_port == port or (rule_port in (80, 443) and port in (80, 443))


def _out_of_scope_reasons(scope, target):
    out = scope.out_of_scope
    reasons = []
    if target.host is not None:
        for rule in out["hostnames"]:
            if _host_rule_matches(rule, target.host):
                reasons.append(
                    'host "%s" is listed in out_of_scope.hostnames ("%s")' % (target.host, rule.text)
                )
                break
    if target.ip is not None:
        for rule in out["ip_ranges"]:
            if _ip_touches(rule.network, target.ip):
                if isinstance(target.ip, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                    wording = "range %s overlaps out_of_scope.ip_ranges"
                else:
                    wording = "address %s is inside out_of_scope.ip_ranges"
                reasons.append((wording + ' ("%s")') % (target.ip, rule.text))
                break
    if target.url is not None:
        for rule in out["urls"]:
            if not _same_host(rule.url, target.url):
                continue
            if not _loose_port(rule.url.port, target.url.port):
                continue
            if _starts_with(target.url.segments, rule.url.segments, True):
                reasons.append('URL is under out_of_scope.urls ("%s")' % rule.text)
                break
            if target.url.ambiguous:
                reasons.append(
                    'URL path is ambiguous and the host has an out_of_scope.urls rule ("%s"). '
                    "Give the path decoded and without ';'." % rule.text
                )
                break
    if target.repo is not None:
        host, segments = target.repo
        for rule in out["repositories"]:
            if rule.host == host and _starts_with(segments, rule.segments, True):
                reasons.append(
                    'repository is listed in out_of_scope.repositories ("%s")' % rule.text
                )
                break
    if target.kind == "artifact":
        if any(rule.sha256 == target.key for rule in out["artifacts"]):
            reasons.append("SHA-256 %s is listed in out_of_scope.artifacts" % target.key)
    if target.kind == "cloud-account":
        if any(rule.key == target.key for rule in out["cloud_accounts"]):
            reasons.append('cloud account "%s" is listed in out_of_scope.cloud_accounts' % target.text)
    if target.kind == "hardware":
        if any(rule.key == target.key for rule in out["hardware"]):
            reasons.append('hardware "%s" is listed in out_of_scope.hardware' % target.text)
    return reasons


def _collapsed_networks(rules):
    networks = []
    for version in (4, 6):
        group = [rule.network for rule in rules if rule.network.version == version]
        networks.extend(ipaddress.collapse_addresses(group))
    return networks


def _in_scope_matches(scope, target):
    """Return a list of (kind, description, rule) for matching in_scope rules."""
    inside = scope.in_scope
    hits = []
    if target.host is not None:
        for rule in inside["hostnames"]:
            if _host_rule_matches(rule, target.host):
                hits.append((target.kind, 'in_scope.hostnames "%s"' % rule.text, rule))
                break
    if target.ip is not None:
        for network in _collapsed_networks(inside["ip_ranges"]):
            if _ip_inside(network, target.ip):
                hits.append((target.kind, 'in_scope.ip_ranges "%s"' % network, None))
                break
    if target.url is not None and not target.url.ambiguous:
        for rule in inside["urls"]:
            if (
                rule.url.scheme == target.url.scheme
                and rule.url.port == target.url.port
                and _same_host(rule.url, target.url)
                and _starts_with(target.url.segments, rule.url.segments, False)
            ):
                hits.append(("url", 'in_scope.urls "%s"' % rule.text, rule))
                break
    if target.repo is not None:
        host, segments = target.repo
        for rule in inside["repositories"]:
            if rule.host == host and rule.segments == segments:
                hits.append(("repository", 'in_scope.repositories "%s"' % rule.text, rule))
                break
    if target.kind == "artifact":
        for rule in inside["artifacts"]:
            if rule.sha256 == target.key:
                hits.append(("artifact", "in_scope.artifacts %s" % rule.sha256, rule))
                break
    if target.kind == "cloud-account":
        for rule in inside["cloud_accounts"]:
            if rule.key == target.key:
                hits.append(("cloud-account", 'in_scope.cloud_accounts "%s"' % rule.text, rule))
                break
    if target.kind == "hardware":
        for rule in inside["hardware"]:
            if rule.key == target.key:
                hits.append(("hardware", 'in_scope.hardware "%s"' % rule.text, rule))
                break
    return hits


def _no_match_reason(scope, target):
    if target.kind == "hostname":
        reason = (
            'host "%s" matches no in_scope.hostnames rule. A name is matched as text. '
            "It is never in scope because of the address it resolves to." % target.host
        )
        if any(rule.url.host == target.host for rule in scope.in_scope["urls"]):
            reason += " in_scope.urls has rules on this host. Give the full URL."
        return reason
    if target.kind == "ip":
        if isinstance(target.ip, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            return "range %s is not fully inside in_scope.ip_ranges" % target.ip
        return "address %s is not inside in_scope.ip_ranges" % target.ip
    if target.kind == "url":
        shown = target.host if target.host is not None else target.ip
        reason = 'URL host is "%s", and neither the host nor the URL matches an in_scope rule' % shown
        if target.url.userinfo:
            reason += ' (the text before "@" is user info, not the host)'
        if target.url.ambiguous:
            reason += ". The path is ambiguous, so in_scope.urls rules were not applied"
        return reason
    if target.kind == "artifact":
        return "SHA-256 %s is not listed in in_scope.artifacts" % target.key
    if target.kind == "cloud-account":
        return 'cloud account "%s" is not listed in in_scope.cloud_accounts' % target.text
    if target.kind == "repository":
        return 'repository "%s" is not listed in in_scope.repositories' % target.text
    return 'hardware "%s" is not listed in in_scope.hardware' % target.text


class Decision(object):
    def __init__(self):
        self.in_scope = False
        self.reasons = []
        self.details = []
        self.matched = []
        self.target_text = None
        self.target_type = None

    @property
    def result(self):
        return "IN SCOPE" if self.in_scope else "NOT IN SCOPE"


def evaluate(scope, raw_target, technique, now, forced_type="auto"):
    """Decide whether one target and technique class are in scope at a time."""
    decision = Decision()
    reasons = decision.reasons
    for item in scope.malformed:
        reasons.append("scope file is malformed: %s" % item)
    for item in scope.incomplete:
        reasons.append("scope file is incomplete: %s" % item)

    target = None
    hits = []
    try:
        target = parse_target(raw_target, forced_type, scope)
    except TargetError as exc:
        reasons.append(str(exc))
    if target is not None:
        decision.target_text = target.text
        decision.target_type = target.kind
        reasons.extend(_out_of_scope_reasons(scope, target))
        hits = _in_scope_matches(scope, target)
        if not hits:
            reasons.append(_no_match_reason(scope, target))
        decision.matched = [description for _, description, _ in hits]
        if hits and all(kind == "repository" for kind, _, _ in hits):
            decision.target_type = "repository"

    if technique in NEVER:
        reasons.append(
            'technique class "%s" is never performed under this skill: %s'
            % (technique, NEVER[technique])
        )
    elif technique not in TECHNIQUES:
        reasons.append(
            'unknown technique class "%s". Run --list-techniques to see the known classes.'
            % technique
        )
    else:
        if technique in scope.forbidden:
            reasons.append('technique class "%s" is forbidden by the scope file' % technique)
        elif technique not in scope.allowed:
            reasons.append('technique class "%s" is not listed in techniques.allowed' % technique)
        kinds = sorted(set(kind for kind, _, _ in hits))
        fits = TECHNIQUES[technique][1]
        if kinds and not any(kind in fits for kind in kinds):
            reasons.append(
                'technique class "%s" does not apply to a %s target. It applies to: %s.'
                % (technique, " or ".join(kinds), ", ".join(fits))
            )
        if technique == "firmware-modification":
            for kind, _, rule in hits:
                if kind != "hardware":
                    continue
                if not rule.operator_owned:
                    reasons.append(
                        'hardware "%s" is not marked operator_owned in the scope file. '
                        "Firmware is not modified on a device the operator does not own."
                        % rule.text
                    )
                if not rule.restore_image_sha256:
                    reasons.append(
                        'hardware "%s" has no restore_image_sha256 in the scope file. '
                        "Record a verified restore image first." % rule.text
                    )

    state = scope.window_state(now)
    stamp = now.isoformat(timespec="seconds")
    if state == "not started":
        reasons.append(
            "testing window has not started (starts %s, now %s)"
            % (scope.window_start.isoformat(), stamp)
        )
    elif state == "closed":
        reasons.append(
            "testing window has closed (ended %s, now %s)" % (scope.window_end.isoformat(), stamp)
        )

    decision.in_scope = not reasons and target is not None and bool(hits)
    if decision.in_scope:
        details = decision.details
        details.append("target: %s (%s)" % (target.text, decision.target_type))
        for description in decision.matched:
            details.append("matched: %s" % description)
        details.append("technique: %s" % technique)
        details.append("window: open until %s" % scope.window_end.isoformat())
        if technique in GATED_NOTES:
            details.append("approval: %s" % GATED_NOTES[technique])
        for note in target.notes:
            details.append("note: %s" % note)
        if target.host is not None:
            details.append("note: %s" % DNS_NOTE)
            for rule in scope.out_of_scope["hostnames"]:
                if not rule.wildcard and target.host.endswith("." + rule.base):
                    details.append(
                        'note: this name is under "%s", which is out of scope by name. '
                        "The scope file excludes that one name only. Ask the operator "
                        "if names under it were meant to be excluded too." % rule.base
                    )
            paths = [
                rule.text for rule in scope.out_of_scope["urls"] if rule.url.host == target.host
            ]
            if paths:
                details.append(
                    "note: this host has out-of-scope paths: %s. Stay away from them."
                    % ", ".join(paths)
                )
    return decision


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _count(number, word):
    return "%d %s%s" % (number, word, "" if number == 1 else "s")


def report_validation(scope, now):
    for item in scope.malformed:
        print("malformed: %s" % item)
    for item in scope.incomplete:
        print("incomplete: %s" % item)
    for item in scope.notes:
        print("note: %s" % item)
    if scope.malformed:
        print(
            "SCOPE MALFORMED: %s, %s"
            % (_count(len(scope.malformed), "error"), _count(len(scope.incomplete), "empty field"))
        )
        return EXIT_ERROR
    if scope.incomplete:
        print("SCOPE INCOMPLETE: %s to fill" % _count(len(scope.incomplete), "field"))
        return EXIT_REFUSED
    print("SCOPE VALID")
    print("engagement: %s" % scope.name)
    print(
        "window: %s to %s (%s now)"
        % (scope.window_start.isoformat(), scope.window_end.isoformat(), scope.window_state(now))
    )
    for label, group in (("in scope", scope.in_scope), ("out of scope", scope.out_of_scope)):
        counts = ["%d %s" % (len(group[key]), key) for key in TARGET_LISTS if group[key]]
        print("%s: %s" % (label, ", ".join(counts) if counts else "nothing listed"))
    print("techniques allowed: %s" % ", ".join(scope.allowed))
    print("scope sha256: %s" % scope.sha256)
    return EXIT_IN_SCOPE


def list_techniques():
    print("Technique classes this skill can perform:")
    for name in TECHNIQUES:
        description, kinds = TECHNIQUES[name]
        print("  %s" % name)
        print("      %s" % description)
        print("      target kinds: %s" % ", ".join(kinds))
        if name in GATED_NOTES:
            print("      operator approval: %s" % GATED_NOTES[name])
    print("Classes that are always refused. A scope file may list them as forbidden:")
    for name in NEVER:
        print("  %s (%s)" % (name, NEVER[name]))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="scope_check.py",
        description="Check a target and a technique class against an engagement scope file.",
        epilog=(
            "Exit codes: 0 in scope or valid, 1 not in scope or incomplete, "
            "2 malformed scope file or bad usage. This tool never resolves host "
            "names. Resolve them yourself and check every resolved address too."
        ),
    )
    parser.add_argument("--scope", metavar="FILE", help="path to the engagement scope file (JSON)")
    parser.add_argument(
        "--target",
        metavar="TARGET",
        help="host name, IP address, CIDR range, URL, SHA-256 value or listed identifier",
    )
    parser.add_argument("--technique", metavar="CLASS", help="technique class, see --list-techniques")
    parser.add_argument(
        "--type",
        choices=TYPE_CHOICES,
        default="auto",
        help="kind of target, when it cannot be told from its form (default: auto)",
    )
    parser.add_argument(
        "--now",
        metavar="ISO",
        help="check against this time instead of the clock, for planning and tests "
        "(ISO 8601 with a time zone)",
    )
    parser.add_argument(
        "--validate", action="store_true", help="validate the scope file and exit"
    )
    parser.add_argument(
        "--list-techniques", action="store_true", help="list the known technique classes and exit"
    )
    return parser


def safe_output():
    """Never crash on a terminal that cannot show a character. Escape it."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    safe_output()
    try:
        return _run(argv)
    except Exception as exc:  # an internal fault must never look like a decision
        print("scope_check: error: internal error: %r" % (exc,), file=sys.stderr)
        return EXIT_ERROR


def _run(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_techniques:
        list_techniques()
        return EXIT_IN_SCOPE
    if not args.scope:
        parser.error("--scope is required")
    if args.validate and (args.target or args.technique):
        parser.error("--validate cannot be combined with --target or --technique")
    if not args.validate and not (args.target and args.technique):
        parser.error("--target and --technique are both required unless --validate is given")
    now = datetime.now(timezone.utc)
    if args.now:
        try:
            now = parse_time(args.now)
        except ValueError as exc:
            parser.error('--now "%s" %s' % (args.now, exc))
    try:
        scope = load_scope(args.scope)
    except ScopeFileError as exc:
        print("scope_check: error: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    if args.validate:
        return report_validation(scope, now)
    if scope.malformed:
        more = len(scope.malformed) - 1
        print(
            "scope_check: error: scope file is malformed: %s%s"
            % (scope.malformed[0], " (and %d more, run --validate)" % more if more else ""),
            file=sys.stderr,
        )
        return EXIT_ERROR
    decision = evaluate(scope, args.target, args.technique, now, args.type)
    if decision.in_scope:
        print("IN SCOPE")
        for line in decision.details:
            print(line)
        print("scope sha256: %s" % scope.sha256)
        if args.now:
            print("note: the time came from --now, not the clock. Do not act on this result.")
        return EXIT_IN_SCOPE
    print("NOT IN SCOPE: %s" % decision.reasons[0])
    for reason in decision.reasons[1:]:
        print("also: %s" % reason)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
