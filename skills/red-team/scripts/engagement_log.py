#!/usr/bin/env python3
"""Append-only, hash-chained engagement log (JSON Lines).

Usage:
  engagement_log.py log --log FILE --scope scope.json --target TARGET
                        --technique CLASS --command "COMMAND LINE"
                        [--output FILE] [--approval TEXT] [--note TEXT]
  engagement_log.py note --log FILE --message TEXT [--label LABEL]
  engagement_log.py verify --log FILE [--anchor HASH] [--check-files]

"log" records one action: the time, the target, the technique class, the
command line, the scope decision and, if an output file is given, its
SHA-256. The scope decision is worked out here, from the scope file and the
clock. It is not taken from the caller.

"note" records an event that is not a command: a stop, an approval from the
operator, a refusal, or a plain note.

"verify" checks that every line chains to the line before it by hash.

Each entry holds the hash of the entry before it (prev_hash) and a hash of
its own contents (entry_hash). Editing, removing, adding or moving a line
breaks the chain. The chain cannot show that lines were cut from the end, or
that the whole file was rewritten. To catch that, give the operator the head
hash that "log" and "verify" print, and later run "verify --anchor HASH".

Exit codes:
  log     0 entry written and the action was in scope
          1 entry written, but the action was NOT in scope. Stop and tell
            the operator.
          2 bad usage, unreadable file, or the existing log fails
            verification. Nothing was written.
  note    0 entry written, 2 error
  verify  0 chain intact, 1 chain broken or a check failed, 2 error

Standard library only. Tested on Python 3.9 to 3.14.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# Leave no cache files in the skill folder. It may be read-only.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scope_check  # noqa: E402

try:
    import fcntl
except ImportError:  # not available on every platform
    fcntl = None

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2

GENESIS = "0" * 64
LABELS = ("note", "stop", "approval", "refusal")


class LogError(Exception):
    """The log cannot be read or written."""


def canonical(entry):
    """Return the text that entry_hash is computed over."""
    body = dict((key, value) for key, value in entry.items() if key != "entry_hash")
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_hash(entry):
    return hashlib.sha256(canonical(entry).encode("ascii")).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_chain(text):
    """Check a whole log. Return (entries, problems)."""
    entries = []
    problems = []
    if text and not text.endswith("\n"):
        problems.append("the last line has no line ending, so the final entry may be cut short")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    previous = GENESIS
    for number, line in enumerate(lines, 1):
        if not line.strip():
            problems.append("line %d: blank line" % number)
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            problems.append("line %d: not valid JSON" % number)
            previous = None
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("entry_hash"), str):
            problems.append("line %d: not a log entry" % number)
            previous = None
            continue
        if entry.get("seq") != number:
            problems.append(
                "line %d: seq is %r, expected %d (a line was removed, added or moved)"
                % (number, entry.get("seq"), number)
            )
        if previous is not None and entry.get("prev_hash") != previous:
            problems.append(
                "line %d: prev_hash does not match the entry before it" % number
            )
        if compute_hash(entry) != entry["entry_hash"]:
            problems.append(
                "line %d: contents do not match entry_hash (the entry was edited)" % number
            )
        previous = entry["entry_hash"]
        entries.append(entry)
    return entries, problems


def read_log(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise LogError("cannot read log %s: %s" % (path, exc.strerror or exc))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise LogError("log %s is not UTF-8 text" % path)


def append_entry(path, fields):
    """Verify the existing chain, then append one entry. Return the entry."""
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError as exc:
        raise LogError("cannot open log %s: %s" % (path, exc.strerror or exc))
    with os.fdopen(descriptor, "r+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        try:
            text = handle.read().decode("utf-8")
        except UnicodeDecodeError:
            raise LogError("log %s is not UTF-8 text. Nothing was written." % path)
        entries, problems = check_chain(text)
        if problems:
            raise LogError(
                "the existing log fails verification (%s). Nothing was written. "
                "Stop and tell the operator." % problems[0]
            )
        entry = {
            "seq": len(entries) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prev_hash": entries[-1]["entry_hash"] if entries else GENESIS,
        }
        entry.update(fields)
        entry["entry_hash"] = compute_hash(entry)
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        handle.seek(0, os.SEEK_END)
        handle.write((line + "\n").encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    return entry


def _log_folder(log_path):
    return os.path.realpath(os.path.dirname(os.path.abspath(log_path)))


def stored_path(path, log_path):
    """Return (path to store, inside). Relative to the log folder when inside it."""
    full = os.path.realpath(path)
    try:
        relative = os.path.relpath(full, _log_folder(log_path))
    except ValueError:
        return full, False
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return full, False
    return relative.replace(os.sep, "/"), True


def cmd_log(args):
    output_file = None
    output_sha256 = None
    if args.output:
        try:
            output_sha256 = sha256_file(args.output)
        except OSError as exc:
            raise LogError(
                "cannot read output file %s: %s. Nothing was written."
                % (args.output, exc.strerror or exc)
            )
        output_file, inside = stored_path(args.output, args.log)
        if not inside:
            print(
                "warning: output file %s is outside the engagement folder. "
                "Keep evidence next to the log." % args.output
            )
    scope_sha256 = None
    matched = []
    target_checked_as = None
    target_type = None
    try:
        scope = scope_check.load_scope(args.scope)
    except scope_check.ScopeFileError as exc:
        result = "NO VALID SCOPE"
        reasons = [str(exc)]
    else:
        scope_sha256 = scope.sha256
        decision = scope_check.evaluate(
            scope, args.target, args.technique, datetime.now(timezone.utc), args.type
        )
        result = decision.result
        reasons = decision.reasons
        matched = decision.matched
        target_checked_as = decision.target_text
        target_type = decision.target_type
    entry = append_entry(
        args.log,
        {
            "kind": "action",
            "target": args.target,
            "target_checked_as": target_checked_as,
            "target_type": target_type,
            "technique": args.technique,
            "command": args.command,
            "scope_decision": {
                "result": result,
                "reasons": reasons,
                "matched": matched,
                "scope_sha256": scope_sha256,
            },
            "output_file": output_file,
            "output_sha256": output_sha256,
            "approval": args.approval or None,
            "note": args.note or None,
        },
    )
    if result != "IN SCOPE":
        print("logged entry %d: %s: %s" % (entry["seq"], result, reasons[0]))
        print("This action was not in scope. Stop and tell the operator.")
        print("head: %s" % entry["entry_hash"])
        return EXIT_FAILED
    print("logged entry %d: IN SCOPE" % entry["seq"])
    if args.technique in scope_check.GATED_ALWAYS and not args.approval:
        print(
            'warning: no approval recorded for a "%s" action. This class needs the '
            "operator's yes for each action. Record it with --approval." % args.technique
        )
    print("head: %s" % entry["entry_hash"])
    return EXIT_OK


def cmd_note(args):
    if not args.message.strip():
        raise LogError("--message is empty")
    entry = append_entry(
        args.log, {"kind": "note", "label": args.label, "message": args.message}
    )
    print("logged entry %d: %s" % (entry["seq"], args.label))
    print("head: %s" % entry["entry_hash"])
    return EXIT_OK


def cmd_verify(args):
    text = read_log(args.log)
    entries, problems = check_chain(text)
    warnings = []
    known = set(entry["entry_hash"] for entry in entries)
    for anchor in args.anchor or []:
        if anchor.strip().lower() not in known:
            problems.append(
                "anchor %s is not in the log (entries were removed or the log was rewritten)"
                % anchor
            )
    last_scope = None
    for entry in entries:
        if entry.get("kind") != "action":
            continue
        seq = entry.get("seq")
        decision = entry.get("scope_decision") or {}
        if decision.get("result") != "IN SCOPE":
            warnings.append("entry %s was logged as %s" % (seq, decision.get("result")))
        if entry.get("technique") in scope_check.GATED_ALWAYS and not entry.get("approval"):
            warnings.append(
                "entry %s (%s) has no recorded approval" % (seq, entry.get("technique"))
            )
        scope_sha256 = decision.get("scope_sha256")
        if last_scope is not None and scope_sha256 != last_scope:
            warnings.append("the scope file changed before entry %s" % seq)
        last_scope = scope_sha256
        if args.check_files and entry.get("output_file"):
            path = entry["output_file"]
            if not os.path.isabs(path):
                path = os.path.join(_log_folder(args.log), path)
            try:
                current = sha256_file(path)
            except OSError:
                problems.append(
                    "entry %s: output file %s is missing" % (seq, entry["output_file"])
                )
                continue
            if current != entry.get("output_sha256"):
                problems.append(
                    "entry %s: output file %s has changed since it was logged"
                    % (seq, entry["output_file"])
                )
    for warning in warnings:
        print("warning: %s" % warning)
    if problems:
        for problem in problems:
            print("FAIL: %s" % problem)
        print("LOG NOT VERIFIED: %d problem%s" % (len(problems), "" if len(problems) == 1 else "s"))
        return EXIT_FAILED
    print("OK: %d entr%s, chain intact" % (len(entries), "y" if len(entries) == 1 else "ies"))
    print("head: %s" % (entries[-1]["entry_hash"] if entries else GENESIS))
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="engagement_log.py",
        description="Append-only, hash-chained engagement log (JSON Lines).",
        epilog=(
            "Exit codes: 0 success, 1 the logged action was not in scope or "
            "verification failed, 2 error. Redact passwords, keys and tokens "
            "from the command line before you log it."
        ),
    )
    commands = parser.add_subparsers(dest="subcommand", metavar="{log,note,verify}")

    log = commands.add_parser("log", help="record one action and its scope decision")
    log.add_argument("--log", required=True, metavar="FILE", help="path to the log file")
    log.add_argument("--scope", required=True, metavar="FILE", help="path to the scope file")
    log.add_argument("--target", required=True, help="the target the command touched")
    log.add_argument("--technique", required=True, metavar="CLASS", help="technique class")
    log.add_argument(
        "--command", required=True, help="the command line that was run, with secrets redacted"
    )
    log.add_argument("--output", metavar="FILE", help="output file to record by SHA-256")
    log.add_argument(
        "--approval", metavar="TEXT", help="the operator's approval for this specific action"
    )
    log.add_argument("--note", metavar="TEXT", help="free text to store with the entry")
    log.add_argument(
        "--type",
        choices=scope_check.TYPE_CHOICES,
        default="auto",
        help="kind of target, as for scope_check.py (default: auto)",
    )
    log.set_defaults(handler=cmd_log)

    note = commands.add_parser("note", help="record a stop, an approval, a refusal or a note")
    note.add_argument("--log", required=True, metavar="FILE", help="path to the log file")
    note.add_argument("--message", required=True, help="what happened")
    note.add_argument(
        "--label", choices=LABELS, default="note", help="kind of event (default: note)"
    )
    note.set_defaults(handler=cmd_note)

    verify = commands.add_parser("verify", help="check that the hash chain is intact")
    verify.add_argument("--log", required=True, metavar="FILE", help="path to the log file")
    verify.add_argument(
        "--anchor",
        action="append",
        metavar="HASH",
        help="fail unless an entry with this hash is in the log. May be repeated.",
    )
    verify.add_argument(
        "--check-files",
        action="store_true",
        help="also check that logged output files still match their SHA-256",
    )
    verify.set_defaults(handler=cmd_verify)
    return parser


def main(argv=None):
    scope_check.safe_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.error("a subcommand is required: log, note or verify")
    try:
        return args.handler(args)
    except LogError as exc:
        print("engagement_log: error: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # an internal fault must never look like success
        print("engagement_log: error: internal error: %r" % (exc,), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
