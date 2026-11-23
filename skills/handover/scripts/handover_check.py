#!/usr/bin/env python3
"""Check a handover note against the repository it describes.

Usage:
  handover_check.py NOTE.md [--repo DIR] [--json OUT] [--max-age-days DAYS]

A handover note is a set of claims about a repository at one moment. This
tool rechecks every claim it can, so a new session knows which parts of the
note still hold before it relies on them.

What is checked:
  Header    the note's age, the branch, the HEAD commit and the working tree
            state, each against the repository as it is now
  Files     every relative file path in backticks: does it exist
  Commits   every commit id in backticks (7 to 40 hex characters): is it in
            the repository
  Branches  every branch written as "branch: NAME": does it exist locally or
            on a remote that was already fetched
  Commands  the commands in fenced shell blocks are listed and never run

What is not checked: claims about behaviour, such as "tests pass" or "the
bug is fixed". Rerun those yourself. The Repository field is not checked
either. A wrong repository shows up as a HEAD commit that is not found.

Each result line starts with one of:
  OK       the claim still holds
  STALE    the claim no longer matches the repository
  UNKNOWN  the tool could not tell, and says why
  WARN     worth knowing but not counted as stale, such as an old note

Exit codes:
  0  HANDOVER: FRESH, no checked fact is stale
  1  HANDOVER: STALE (N facts)
  2  bad usage, a missing or unreadable note, or no handover header

How the note is read:
  - The header is the line "<!-- handover v1 -->" and the list under it:
    Written, Repository, Branch, HEAD, Working tree. Branch may be
    "(detached)" when no branch is checked out.
  - Paths are relative to the repository root and use forward slashes. A
    backticked span is a path when it has no spaces and contains a slash or
    ends in a known file extension. Flags, commands, identifiers, URLs and
    patterns are skipped.
  - A missing path is STALE when it clearly was a path: it has a slash and
    a file extension, it was in the note's HEAD commit, or its top folder
    exists. Otherwise it is UNKNOWN, because it may not be a path at all.
  - A path followed by (planned) or (gone) is expected to be absent, and is
    STALE if it exists.
  - The working tree count is the number of lines printed by
    "git status --porcelain --untracked-files=all", not counting the note.
  - Text inside HTML comments is ignored.

The repository is --repo if given, otherwise the git repository that
contains the note. With no git repository, only file paths are checked,
relative to --repo or to the note's folder, and the report says so.

This tool only reads. It never changes the repository, never fetches,
never contacts the network, and never runs a command taken from the note.

Standard library only. Needs the git executable for everything except the
file path checks. Works on Python 3.9 or later.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

EXIT_FRESH = 0
EXIT_STALE = 1
EXIT_ERROR = 2

OK, STALE, UNKNOWN, WARN = "OK", "STALE", "UNKNOWN", "WARN"
SECTIONS = (("header", "Header"), ("files", "Files"), ("commits", "Commits"), ("branches", "Branches"))
COMMANDS_TITLE = "Commands the note asks you to run (not executed)"

SUPPORTED_VERSION = 1
DEFAULT_MAX_AGE_DAYS = 7.0
MAX_NOTE_BYTES = 5 * 1024 * 1024
GIT_TIMEOUT = 120
MAX_LISTED = 5
HEADER_KEYS = ("written", "repository", "branch", "head", "working tree")

MARKER_RE = re.compile(r"^\s*<!--\s*handover\s+v(\d+)\s*-->\s*$", re.IGNORECASE)
FIELD_RE = re.compile(r"^\s*[-*]\s+([A-Za-z][A-Za-z ]*?)\s*:\s*(.*?)\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")  # any indent, so a block inside a list item counts
SPAN_RE = re.compile(r"`([^`\n]+)`")
MARK_RE = re.compile(r"\s*\((planned|gone)\)", re.IGNORECASE)
BRANCH_RE = re.compile(r"\bbranch:\s*`?([A-Za-z0-9][A-Za-z0-9._/+@-]*)", re.IGNORECASE)
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{7,40}|[0-9a-f]{64})$")
LINE_SUFFIX_RE = re.compile(r"(?::\d+(?::\d+)?|#L\d+(?:-L?\d+)?)$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|org|net|io|dev|ai|co|edu|gov|app)$", re.IGNORECASE)
TREE_RE = re.compile(r"^(clean|dirty)\b(?:\s*\(\s*(\d+)\s+files?\s*\))?\s*$", re.IGNORECASE)

SHELL_LANGS = frozenset(
    "sh bash zsh fish shell console terminal shell-session shellsession powershell pwsh ps1 bat cmd".split()
)
EXTENSIONS = frozenset(
    """
    md markdown txt rst adoc tex bib
    py pyi pyx ipynb js jsx mjs cjs ts tsx vue svelte
    c h cc cpp cxx hpp hh rs go zig java kt kts scala swift cs fs rb php pl lua jl dart ex exs erl hs ml nim
    sh bash zsh fish ps1 bat
    json jsonl json5 yaml yml toml ini cfg conf lock xml plist proto graphql sql
    html htm css scss sass less svg
    csv tsv parquet log diff patch
    png jpg jpeg gif webp ico pdf
    cmake mk gradle tf nix
    zip tar gz tgz whl wasm
    """.split()
)
# Names that end like a file but are not one.
NOT_FILES = frozenset("console.log node.js next.js nuxt.js vue.js d3.js three.js express.js".split())
MIME_TYPES = frozenset("application audio font image message model multipart text video".split())
# A span holding any of these is code, a pattern, a placeholder or a revision, not a plain path.
NOT_PATH_CHARS = frozenset("(){}<>|;&$=*?,\"'\\!%^#:~`")
# Variables that would point git at another repository or index.
GIT_SCRUB = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)
# Unicode controls that reorder text on a terminal.
_BIDI = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))


class UsageError(Exception):
    """Bad arguments or a note that cannot be used. Exit 2 with one line."""


class GitError(Exception):
    """One git call failed. The check that needed it becomes UNKNOWN."""


class GitMissing(GitError):
    """The git executable is not installed or not on the search path."""


def clean(text, limit=200):
    """Make note text safe to print: no control characters, no long runs."""
    out = []
    for ch in str(text):
        code = ord(ch)
        if code < 0x20 or code == 0x7F or code in _BIDI:
            out.append("\\x%02x" % code if code < 0x100 else "\\u%04x" % code)
        else:
            out.append(ch)
    shown = "".join(out)
    return shown if len(shown) <= limit else shown[: limit - 3] + "..."


def count(number, word):
    return "%d %s%s" % (number, word, "" if number == 1 else "s")


def first_line(text):
    for line in text.splitlines():
        if line.strip():
            return clean(line.strip())
    return "no message"


# ---------------------------------------------------------------------------
# Reading the note
# ---------------------------------------------------------------------------


class Span(object):
    def __init__(self, text, line, mark):
        self.text = text
        self.line = line
        self.mark = mark


class Note(object):
    def __init__(self):
        self.version = None
        self.fields = {}
        self.spans = []
        self.branches = []
        self.commands = []

    def field(self, key):
        return self.fields.get(key, "")


def strip_comments(line, inside):
    """Return (text outside HTML comments, still inside a comment)."""
    out = []
    pos = 0
    while True:
        if inside:
            end = line.find("-->", pos)
            if end < 0:
                return "".join(out), True
            pos = end + 3
            inside = False
        else:
            start = line.find("<!--", pos)
            if start < 0:
                out.append(line[pos:])
                return "".join(out), False
            out.append(line[pos:start])
            pos = start + 4
            inside = True


def commands_from(block):
    """Return (line number, command) for each command line of a fenced shell block."""
    found = []
    pending = None
    for number, raw in block:
        line = raw.strip()
        if pending is not None:
            pending[1] = (pending[1] + " " + line.rstrip("\\").strip()).strip()
            if not line.endswith("\\"):
                found.append((pending[0], pending[1]))
                pending = None
            continue
        if not line or line.startswith("#"):
            continue
        if line.startswith("$ "):
            line = line[2:].strip()
        if line.endswith("\\"):
            pending = [number, line.rstrip("\\").strip()]
            continue
        found.append((number, line))
    if pending is not None:
        found.append((pending[0], pending[1]))
    return found


def parse_note(text):
    """Split a note into its header fields, backticked spans, branches and commands."""
    note = Note()
    lines = text.splitlines()
    prose = []
    marker_line = None
    fence = None
    block = []
    in_comment = False
    for number, raw in enumerate(lines, 1):
        if fence is not None:
            closing = FENCE_RE.match(raw)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= fence[1] \
                    and not closing.group(2).strip():
                if fence[2] in SHELL_LANGS:
                    note.commands.extend(commands_from(block))
                fence = None
                block = []
            else:
                block.append((number, raw))
            continue
        if not in_comment:
            marker = MARKER_RE.match(raw)
            if marker and note.version is None:
                note.version = int(marker.group(1))
                marker_line = number
                continue
            opening = FENCE_RE.match(raw)
            if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
                info = opening.group(2).strip().split()
                lang = info[0].strip("{}.").lower() if info else ""
                fence = (opening.group(1)[0], len(opening.group(1)), lang)
                block = []
                continue
        visible, in_comment = strip_comments(raw, in_comment)
        prose.append((number, visible))
    if fence is not None and fence[2] in SHELL_LANGS:
        note.commands.extend(commands_from(block))

    header_lines = set()
    if marker_line is not None:
        index = marker_line
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                if header_lines:
                    break  # the header ends at the first blank line after it starts
                index += 1
                continue
            match = FIELD_RE.match(line)
            if not match:
                break
            key = " ".join(match.group(1).lower().split())
            if key in HEADER_KEYS and key not in note.fields:
                note.fields[key] = match.group(2).strip().strip("`").strip()
            header_lines.add(index + 1)
            index += 1

    for number, visible in prose:
        if number in header_lines:
            continue
        for match in SPAN_RE.finditer(visible):
            mark = MARK_RE.match(visible, match.end())
            note.spans.append(Span(match.group(1).strip(), number, mark.group(1).lower() if mark else None))
        for match in BRANCH_RE.finditer(visible):
            name = match.group(1).rstrip(".,;:")
            if name:
                note.branches.append((name, number))
    return note


def read_note(path):
    if not os.path.exists(path):
        raise UsageError("note not found: %s" % path)
    if os.path.isdir(path):
        raise UsageError("%s is a directory, not a handover note" % path)
    try:
        if os.path.getsize(path) > MAX_NOTE_BYTES:
            raise UsageError("%s is too large to be a handover note" % path)
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise UsageError("cannot read %s: %s" % (path, exc.strerror or exc))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UsageError("%s is not UTF-8 text" % path)
    note = parse_note(text)
    if note.version is None:
        raise UsageError('%s has no handover header (expected the line "<!-- handover v1 -->")' % path)
    if note.version != SUPPORTED_VERSION:
        raise UsageError(
            "%s uses handover v%d, and this checker reads v%d" % (path, note.version, SUPPORTED_VERSION)
        )
    if not note.fields:
        raise UsageError("%s has a handover marker but no header fields under it" % path)
    return note


def parse_time(text):
    """Parse an ISO 8601 time. A time with no zone is read as UTC. Returns None if unreadable."""
    value = text.strip()
    if value[-1:] in ("z", "Z"):
        value = value[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def describe_age(age):
    minutes = int(age.total_seconds()) // 60
    if minutes < 2:
        return "just now"
    if minutes < 120:
        return "%d minutes ago" % minutes
    hours = minutes // 60
    if hours < 48:
        return "%d hours ago" % hours
    return "%d days ago" % (hours // 24)


def path_candidate(span):
    """Return (path, has a known extension) when a backticked span reads as a file path, else None."""
    text = span.strip()
    if not text or len(text) > 400 or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    if text.startswith("-") or "://" in text or text.lower().startswith(("www.", "mailto:")):
        return None
    text = text.split("::", 1)[0]  # tests/test_app.py::test_name
    text = LINE_SUFFIX_RE.sub("", text)  # src/app.py:12 and src/app.py#L12
    while text.startswith("./"):
        text = text[2:]
    if not text or text.lower() in NOT_FILES:
        return None
    body = text[2:] if text.startswith("~/") else text
    if any(ch in NOT_PATH_CHARS for ch in body):
        return None
    segments = [segment for segment in body.split("/") if segment]
    if not segments or any(".." in segment and segment != ".." for segment in segments):
        return None
    has_slash = "/" in text
    stem, _, extension = segments[-1].rpartition(".")
    has_extension = bool(stem) and extension.lower() in EXTENSIONS
    if not has_slash and not has_extension:
        return None
    if ("[" in body or "]" in body) and not (has_slash and has_extension):
        return None
    if "@" in segments[0]:
        return None
    if has_slash and not has_extension:
        if all(segment.isdigit() for segment in segments):
            return None  # a date or a fraction
        if len(segments) == 2 and segments[0].lower() in MIME_TYPES:
            return None
    if len(segments) > 1 and DOMAIN_RE.match(segments[0]):
        return None  # a URL written without its scheme
    return text, has_extension


# ---------------------------------------------------------------------------
# Asking git, read only
# ---------------------------------------------------------------------------


class Git(object):
    """Read-only git calls in one repository. Nothing here writes or fetches."""

    def __init__(self, root):
        self.root = root
        env = dict((key, value) for key, value in os.environ.items() if key not in GIT_SCRUB)
        env.update(
            {
                "GIT_OPTIONAL_LOCKS": "0",  # status must not rewrite the index
                "GIT_NO_LAZY_FETCH": "1",  # a missing object must not be fetched
                "GIT_ALLOW_PROTOCOL": "none",  # no transport is allowed at all
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        self.env = env
        self._refs = None

    def run(self, *args):
        command = ["git", "-c", "core.fsmonitor=false", "-C", self.root] + list(args)
        try:
            done = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                timeout=GIT_TIMEOUT,
            )
        except FileNotFoundError:
            raise GitMissing("the git executable was not found")
        except subprocess.TimeoutExpired:
            raise GitError("git %s took longer than %d seconds" % (args[0], GIT_TIMEOUT))
        except (OSError, ValueError) as exc:  # ValueError: an argument git cannot be given
            raise GitError("git could not be started: %s" % (getattr(exc, "strerror", None) or exc))
        return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")

    def must(self, *args):
        code, out, err = self.run(*args)
        if code != 0:
            raise GitError("git %s failed: %s" % (args[0], first_line(err)))
        return out

    def has(self, spec):
        return self.run("cat-file", "-e", spec)[0] == 0

    def resolve_commit(self, name):
        code, out, _ = self.run("rev-parse", "--verify", "--quiet", name + "^{commit}")
        return out.strip() if code == 0 and out.strip() else None

    def head(self):
        return self.resolve_commit("HEAD")

    def current_branch(self):
        code, out, _ = self.run("symbolic-ref", "--quiet", "--short", "HEAD")
        return out.strip() if code == 0 and out.strip() else None

    def refs(self):
        if self._refs is None:
            out = self.must("for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes")
            self._refs = set(line.strip() for line in out.splitlines() if line.strip())
        return self._refs

    def branch_where(self, name):
        """Return ("local", name) or ("remote", remote/name) for a branch that exists, else None."""
        refs = self.refs()
        if "refs/heads/" + name in refs:
            return "local", name
        if "refs/remotes/" + name in refs:
            return "remote", name
        for ref in sorted(refs):
            if ref.startswith("refs/remotes/"):
                rest = ref[len("refs/remotes/"):]
                if rest.partition("/")[2] == name:
                    return "remote", rest
        return None

    def ahead_behind(self, old, new):
        """Return (commits only in new, commits only in old)."""
        out = self.must("rev-list", "--left-right", "--count", "%s...%s" % (old, new))
        parts = out.split()
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise GitError("git rev-list gave an unexpected answer")
        return int(parts[1]), int(parts[0])

    def changed_files(self):
        out = self.must("status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = out.split("\0")
        files = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if len(entry) < 4:
                continue
            if "R" in entry[:2] or "C" in entry[:2]:
                index += 1  # the next field is the path it was renamed or copied from
            files.append(entry[3:])
        return files

    def prefix_matches(self, prefix):
        code, out, _ = self.run("rev-parse", "--disambiguate=" + prefix)
        return len(out.split()) if code == 0 else 0


def find_git_root(start):
    """Walk up from start to the nearest folder that holds a .git entry. No git call is made."""
    current = os.path.abspath(start)
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def open_repository(start):
    """Return (Git or None, folder that paths are read from, why there is no git)."""
    found = find_git_root(start)
    if found is None:
        return None, start, "no git repository found"
    try:
        code, out, err = Git(found).run("rev-parse", "--show-toplevel")
    except GitError as exc:
        return None, found, str(exc)
    root = out.rstrip("\r\n")
    if code != 0 or not root:
        return None, found, "git could not read the repository at %s (%s)" % (found, first_line(err))
    return Git(root), root, None


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


class Report(object):
    def __init__(self):
        self.checks = []

    def add(self, section, status, detail, subject=None, line=None):
        self.checks.append(
            {"section": section, "status": status, "subject": subject, "detail": detail, "line": line}
        )

    def total(self, status):
        return sum(1 for check in self.checks if check["status"] == status)


def check_age(note, now, max_age_days, report):
    value = note.field("written")
    if not value:
        report.add("header", UNKNOWN, "the note has no Written time, so its age is not checked")
        return
    written = parse_time(value) or parse_time(value.split()[0])  # allow a remark after the time
    if written is None:
        report.add(
            "header", UNKNOWN,
            "Written time `%s` is not an ISO 8601 date and time, so the age is not checked" % clean(value),
        )
        return
    stamp = written.strftime("%Y-%m-%dT%H:%M:%SZ")
    age = now - written
    if age < timedelta(minutes=-5):
        report.add("header", WARN, "Written time %s is in the future. Check the clock or the note." % stamp)
    elif age > timedelta(days=max_age_days):
        report.add(
            "header", WARN,
            "written %s, %s. That is past the %g day limit, so recheck its claims before you rely on them."
            % (stamp, describe_age(age), max_age_days),
        )
    else:
        report.add("header", OK, "written %s, %s" % (stamp, describe_age(age)))


def check_header_branch(note, git, why_no_git, report):
    value = note.field("branch")
    if not value:
        report.add("header", UNKNOWN, "the note does not state a branch")
        return None
    detached = re.search(r"\bdetached\b", value, re.IGNORECASE)
    value = value.split()[0]  # a branch name has no spaces, so anything after it is a remark
    shown = clean(value)
    if git is None:
        report.add("header", UNKNOWN, "branch `%s` not checked: %s" % (shown, why_no_git))
        return value
    try:
        current = git.current_branch()
        if detached:
            if current is None:
                report.add("header", OK, "HEAD is detached, as the note says")
            else:
                report.add(
                    "header", STALE,
                    "the note says HEAD was detached, and the current branch is `%s`" % clean(current),
                )
            return None
        where = git.branch_where(value)
        now_on = "HEAD is detached now" if current is None else "the current branch is `%s`" % clean(current)
        if current == value:
            report.add("header", OK, "branch `%s` exists and is the current branch" % shown)
        elif where is None:
            report.add(
                "header", STALE,
                "branch `%s` does not exist locally or on a fetched remote (%s)" % (shown, now_on),
            )
        elif where[0] == "remote":
            report.add(
                "header", STALE,
                "branch `%s` exists only on a fetched remote (`%s`), and %s" % (shown, clean(where[1]), now_on),
            )
        else:
            report.add("header", STALE, "branch `%s` exists, but %s" % (shown, now_on))
    except GitError as exc:
        report.add("header", UNKNOWN, "branch `%s` not checked: %s" % (shown, exc))
    return value


def check_head(note, git, why_no_git, report):
    """Check the HEAD field. Returns the note's HEAD as a full id when it is in the repository."""
    value = note.field("head")
    if not value:
        report.add("header", UNKNOWN, "the note does not state a HEAD commit")
        return None
    shown = clean(value)
    value = value.split()[0]
    if not COMMIT_RE.match(value.lower()):
        report.add("header", STALE, "HEAD `%s` is not a commit id" % shown)
        return None
    shown = clean(value)
    if git is None:
        report.add("header", UNKNOWN, "HEAD `%s` not checked: %s" % (shown, why_no_git))
        return None
    try:
        noted = git.resolve_commit(value.lower())
        if noted is None:
            report.add(
                "header", STALE,
                "HEAD `%s` is not in this repository. It is the wrong repository, "
                "or the commit was rewritten or never fetched." % shown,
            )
            return None
        current = git.head()
        if current is None:
            report.add("header", STALE, "HEAD `%s` exists, but the current branch has no commits" % noted[:12])
        elif current == noted:
            report.add("header", OK, "HEAD `%s` is the current HEAD" % noted[:12])
        else:
            try:
                ahead, behind = git.ahead_behind(noted, current)
            except GitError:
                ahead = behind = 0  # the two commits still differ, even if they cannot be counted
            if ahead and behind:
                moved = "has diverged from it: %s ahead and %s behind" % (count(ahead, "commit"), behind)
            elif ahead:
                moved = "is %s ahead of it" % count(ahead, "commit")
            elif behind:
                moved = "is %s behind it" % count(behind, "commit")
            else:
                moved = "is a different commit"
            report.add(
                "header", STALE,
                "HEAD has moved: the note says `%s`, and the current HEAD `%s` %s"
                % (noted[:12], current[:12], moved),
            )
        return noted
    except GitError as exc:
        report.add("header", UNKNOWN, "HEAD `%s` not checked: %s" % (shown, exc))
        return None


def check_tree(note, git, why_no_git, note_in_repo, report):
    value = note.field("working tree")
    if not value:
        report.add("header", UNKNOWN, "the note does not state the working tree state")
        return
    match = TREE_RE.match(value)
    if not match:
        report.add(
            "header", UNKNOWN,
            "working tree `%s` is neither `clean` nor `dirty (N files)`, so it is not checked" % clean(value),
        )
        return
    said_clean = match.group(1).lower() == "clean"
    said_count = int(match.group(2)) if match.group(2) else None
    if git is None:
        report.add("header", UNKNOWN, "working tree state not checked: %s" % why_no_git)
        return
    try:
        changed = git.changed_files()
    except GitError as exc:
        report.add("header", UNKNOWN, "working tree state not checked: %s" % exc)
        return
    aside = ""
    if note_in_repo is not None and note_in_repo in changed:
        changed = [path for path in changed if path != note_in_repo]
        aside = ", not counting the note itself"
    listed = ", ".join("`%s`" % clean(path) for path in changed[:MAX_LISTED])
    if len(changed) > MAX_LISTED:
        listed += " and %d more" % (len(changed) - MAX_LISTED)
    have = count(len(changed), "file")
    if said_clean and not changed:
        report.add("header", OK, "working tree is clean, as the note says" + aside)
    elif said_clean:
        verb = "has" if len(changed) == 1 else "have"
        report.add("header", STALE, "the note says clean, but %s %s changes%s: %s" % (have, verb, aside, listed))
    elif not changed:
        said = "dirty" if said_count is None else "dirty (%s)" % count(said_count, "file")
        report.add("header", STALE, "the note says %s, but the working tree is clean now%s" % (said, aside))
    elif said_count is None:
        report.add("header", OK, "working tree is dirty, as the note says (%s now%s)" % (have, aside))
    elif said_count == len(changed):
        report.add("header", OK, "working tree is dirty with %s, as the note says%s" % (have, aside))
    else:
        report.add(
            "header", STALE,
            "the note says dirty (%s), and there are %s now%s: %s"
            % (count(said_count, "file"), len(changed), aside, listed),
        )


def locate(path, base):
    """Return where a note path points on disk, or None when it lies outside what is checked."""
    if path.startswith("~"):
        return None
    if os.path.isabs(path):
        real_base = os.path.realpath(base)
        real = os.path.realpath(path)
        if real != real_base and not real.startswith(real_base.rstrip(os.sep) + os.sep):
            return None
        return real
    return os.path.normpath(os.path.join(base, path))


def check_files(note, base, git, note_head, report):
    """Check each backticked path.

    Returns (spans that turned out to name a branch, whether an unmarked path was missing).
    """
    items = {}
    for span in note.spans:
        candidate = path_candidate(span.text)
        if candidate is None:
            continue
        path, has_extension = candidate
        if path in items:
            items[path]["mark"] = items[path]["mark"] or span.mark
        else:
            items[path] = {"ext": has_extension, "line": span.line, "mark": span.mark}
    as_branches = []
    missing_unmarked = False
    for path, item in items.items():
        shown, line, mark = clean(path), item["line"], item["mark"]
        full = locate(path, base)
        if full is None:
            report.add("files", UNKNOWN, "is outside the repository, so it is not checked", shown, line)
            continue
        exists = os.path.isdir(full) if path.endswith("/") else os.path.lexists(full)
        if exists:
            if mark:
                report.add("files", STALE, "exists, but the note marks it (%s)" % mark, shown, line)
            else:
                report.add("files", OK, "exists", shown, line)
            continue
        if mark:
            report.add("files", OK, "is absent, as the note says (%s)" % mark, shown, line)
            continue
        relative = not os.path.isabs(path) and ".." not in path.split("/")
        try:
            if git is not None and relative and git.branch_where(path.rstrip("/")):
                as_branches.append((path.rstrip("/"), line))
                continue
            was_tracked = bool(
                git is not None and note_head and relative and git.has("%s:%s" % (note_head, path.rstrip("/")))
            )
        except GitError:
            was_tracked = False
        top = path.split("/")[0]
        top_exists = "/" in path and relative and os.path.isdir(os.path.join(base, top))
        missing_unmarked = True
        if was_tracked:
            report.add("files", STALE, "does not exist. It was in the note's HEAD commit.", shown, line)
        elif "/" in path and item["ext"]:
            report.add("files", STALE, "does not exist", shown, line)
        elif top_exists:
            report.add("files", STALE, "does not exist under `%s/`" % clean(top), shown, line)
        elif "/" in path:
            report.add("files", UNKNOWN, "not found. It may not be a file path.", shown, line)
        else:
            report.add(
                "files", UNKNOWN,
                "is not at the repository root. If it is a file, write its path from the root.", shown, line,
            )
    return as_branches, missing_unmarked


def check_commits(note, git, why_no_git, report):
    seen = set()
    for span in note.spans:
        token = span.text
        if token in seen or not COMMIT_RE.match(token):
            continue
        seen.add(token)
        # All digits or all letters is more often a number or a word than a commit id.
        certain = any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token)
        if git is None:
            if certain:
                report.add("commits", UNKNOWN, "not checked: %s" % why_no_git, token, span.line)
            continue
        try:
            if git.has(token + "^{commit}"):
                report.add("commits", OK, "is a commit in this repository", token, span.line)
            elif git.has(token):
                report.add("commits", OK, "is in this repository, but it is not a commit", token, span.line)
            else:
                matches = git.prefix_matches(token)
                if matches > 1:
                    report.add(
                        "commits", UNKNOWN,
                        "is ambiguous: %d objects start with it. Write the full id." % matches, token, span.line,
                    )
                elif certain:
                    report.add("commits", STALE, "is not in this repository", token, span.line)
        except GitError as exc:
            if certain:
                report.add("commits", UNKNOWN, "not checked: %s" % exc, token, span.line)


def check_branches(note, git, why_no_git, header_branch, extra, report):
    seen = set([header_branch]) if header_branch else set()
    for name, line in list(note.branches) + list(extra):
        if name in seen:
            continue
        seen.add(name)
        shown = clean(name)
        if git is None:
            report.add("branches", UNKNOWN, "not checked: %s" % why_no_git, shown, line)
            continue
        try:
            where = git.branch_where(name)
        except GitError as exc:
            report.add("branches", UNKNOWN, "not checked: %s" % exc, shown, line)
            continue
        if where is None:
            report.add("branches", STALE, "does not exist locally or on a fetched remote", shown, line)
        elif where[0] == "local":
            report.add("branches", OK, "exists", shown, line)
        elif where[1] == name:
            report.add("branches", OK, "exists on a fetched remote", shown, line)
        else:
            report.add(
                "branches", OK, "exists on a fetched remote (`%s`), not locally" % clean(where[1]), shown, line
            )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def row_text(check):
    text = check["detail"]
    if check["subject"] is not None:
        text = "`%s` %s" % (check["subject"], text)
    if check["line"] is not None:
        text += " (line %d)" % check["line"]
    return text


def render(report, note, note_path, where, hint):
    print("note:        %s" % clean(note_path, 400))
    print("repository:  %s" % where)
    for key, title in SECTIONS:
        print("")
        print(title)
        rows = [check for check in report.checks if check["section"] == key]
        if not rows:
            print("  nothing in the note to check")
        for check in rows:
            print("  %-8s %s" % (check["status"], row_text(check)))
        if key == "files" and hint:
            print("  If a path is meant to be absent, write (planned) or (gone) straight after it.")
    print("")
    print(COMMANDS_TITLE)
    if not note.commands:
        print("  none")
    for line, command in note.commands:
        print("  line %d: %s" % (line, clean(command, 400)))
    print("")


def summary_line(report):
    ok, stale, unknown, warned = (report.total(status) for status in (OK, STALE, UNKNOWN, WARN))
    text = "Checked %s: %d ok, %d stale, %d unknown." % (count(ok + stale + unknown, "fact"), ok, stale, unknown)
    if warned:
        text += " %s." % count(warned, "warning")
    return text


def write_json(path, payload):
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        raise UsageError("cannot write %s: %s" % (path, exc.strerror or exc))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def age_limit(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("--max-age-days needs a number of days, not %r" % text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("--max-age-days needs a number of days that is zero or more")
    return value


def build_parser():
    # The usage line is printed by argparse, so drop the copy in the module text.
    description = re.sub(r"\nUsage:\n.*?\n\n", "\n", __doc__, count=1, flags=re.DOTALL)
    parser = Parser(
        prog="handover_check.py",
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="handover_check.py NOTE.md [--repo DIR] [--json OUT] [--max-age-days DAYS]",
    )
    parser.add_argument("note", metavar="NOTE.md", help="the handover note to check")
    parser.add_argument(
        "--repo", metavar="DIR",
        help="the repository the note describes (default: the git repository that contains the note)",
    )
    parser.add_argument(
        "--json", metavar="OUT",
        help="also write the result as JSON to this file. Keep it outside the repository, "
        "because a new file there changes the tree state the next check sees.",
    )
    parser.add_argument(
        "--max-age-days", metavar="DAYS", type=age_limit, default=DEFAULT_MAX_AGE_DAYS,
        help="warn when the note is older than this (default: %g)" % DEFAULT_MAX_AGE_DAYS,
    )
    return parser


def safe_output():
    """Never crash on a terminal that cannot show a character. Escape it."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def run(argv):
    args = build_parser().parse_args(argv)
    note = read_note(args.note)
    note_real = os.path.realpath(args.note)
    if args.json:
        if os.path.isdir(args.json):
            raise UsageError("--json %s is a directory" % args.json)
        if os.path.exists(args.json) and os.path.samefile(args.json, args.note):
            raise UsageError("--json would overwrite the note. Give another file.")
    if args.repo is not None:
        if not os.path.isdir(args.repo):
            raise UsageError("--repo %s is not a directory" % args.repo)
        start, source = os.path.abspath(args.repo), "from --repo"
    else:
        start, source = os.path.dirname(note_real), "contains the note"

    git, base, why_no_git = open_repository(start)
    note_in_repo = None
    if git is not None:
        where = "%s (%s)" % (base, source)
        try:
            inside = os.path.relpath(note_real, os.path.realpath(base))
        except ValueError:  # another drive
            inside = ".."
        if not inside.startswith(".."):
            note_in_repo = inside.replace(os.sep, "/")
    else:
        where = "none (%s). Only file paths are checked, relative to %s" % (why_no_git, base)

    report = Report()
    check_age(note, datetime.now(timezone.utc), args.max_age_days, report)
    header_branch = check_header_branch(note, git, why_no_git, report)
    note_head = check_head(note, git, why_no_git, report)
    check_tree(note, git, why_no_git, note_in_repo, report)
    as_branches, hint = check_files(note, base, git, note_head, report)
    check_commits(note, git, why_no_git, report)
    check_branches(note, git, why_no_git, header_branch, as_branches, report)

    stale = report.total(STALE)
    final = "HANDOVER: STALE (%s)" % count(stale, "fact") if stale else "HANDOVER: FRESH"
    render(report, note, args.note, where, hint)
    if git is None:
        print("No git repository was read, so commits, branches and tree state are UNKNOWN.")
        if args.repo is None:
            print("Pass --repo DIR to check them.")
    if not report.total(OK) and not stale:
        print("Nothing could be confirmed. FRESH here means only that nothing was found stale.")
    print(summary_line(report))
    print(final)

    if args.json:
        write_json(
            args.json,
            {
                "format": "handover_check v1",
                "note": args.note,
                "repository": base if git is not None else None,
                "git": git is not None,
                "paths_relative_to": base,
                "result": "STALE" if stale else "FRESH",
                "counts": dict((status.lower(), report.total(status)) for status in (OK, STALE, UNKNOWN, WARN)),
                "checks": report.checks,
                "commands": [{"line": line, "command": command} for line, command in note.commands],
            },
        )
    return EXIT_STALE if stale else EXIT_FRESH


def main(argv=None):
    safe_output()
    try:
        return run(argv)
    except UsageError as exc:
        print("handover_check: error: %s" % clean(exc, 400), file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_ERROR
    except Exception as exc:  # an internal fault must never look like a result
        print("handover_check: error: internal error: %r" % (exc,), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
