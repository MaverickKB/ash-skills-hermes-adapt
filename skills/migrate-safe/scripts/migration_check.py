#!/usr/bin/env python3
"""Lint SQL migrations for destructive hazards and test round-trip rollback in SQLite.

Subcommands:
  lint FILE ...        Static check for DROP TABLE/COLUMN, TRUNCATE, NOT NULL
                       without DEFAULT, non-concurrent Postgres indexes, and
                       UPDATE/DELETE without WHERE.
  sqlite-test          Run seed -> up -> repeat up (idempotency) -> down (rollback)
                       -> up again in an isolated temporary SQLite database.

Exit codes: 0 SAFE/CLEAN, 1 UNSAFE/FINDINGS, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from typing import Dict, Iterator, List, NamedTuple, Optional, Sequence, Tuple, Union

SQL_TOKEN = re.compile(r"--[^\r\n]*|/\*|\b[Ee]'|(?P<dollar>\$(?:[A-Za-z_]\w*)?\$)|[\w$]+|\s+|.", re.S)
COMMENT_EDGE = re.compile(r"/\*|\*/")
ALLOW_DROP_RE = re.compile(r"--\s*migrate-safe:\s*allow-drop\b", re.I)

DROP_TABLE_RE = re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.I)
TRUNCATE_RE = re.compile(r"\bTRUNCATE\s+(?:TABLE\s+)?\w+", re.I)
DROP_COLUMN_RE = re.compile(r"\bALTER\s+TABLE\b[^;]*\bDROP\s+(?:COLUMN\s+)?\w+", re.I)
ADD_NOT_NULL_RE = re.compile(r"\bALTER\s+TABLE\b[^;]*\bADD\s+(?:COLUMN\s+)?[^;]*\bNOT\s+NULL\b", re.I)
DEFAULT_RE = re.compile(r"\bDEFAULT\b", re.I)
CREATE_INDEX_RE = re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\b", re.I)
CONCURRENTLY_RE = re.compile(r"\bCONCURRENTLY\b", re.I)
QUERY_COMMANDS = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "VALUES", "REPLACE"}
WRITES_REQUIRING_WHERE = {"UPDATE", "DELETE"}


def quoted_end(sql: str, pos: int, close: str, backslash: bool) -> int:
    while pos < len(sql):
        if sql[pos] == close:
            pos += 1
            if pos == len(sql) or sql[pos] != close:
                return pos
        elif backslash and sql[pos] == "\\":
            pos += 1
        pos += 1
    return len(sql)


def comment_end(sql: str, pos: int, nested: bool) -> int:
    depth = 1
    for edge in COMMENT_EDGE.finditer(sql, pos):
        if edge[0] == "*/":
            depth -= 1
            if depth == 0:
                return edge.end()
        elif nested:
            depth += 1
    return len(sql)


def statement_views(sql: str, dialect: str) -> Iterator[Tuple[str, str]]:
    """Yield executable SQL and the same statement with literal and identifier contents masked."""
    original: List[str] = []
    code: List[str] = []
    pos = 0
    while pos < len(sql):
        match = SQL_TOKEN.match(sql, pos)
        assert match is not None
        token = match[0]
        start, pos = match.span()
        if token.startswith("--") or token == "/*" or (token == "#" and dialect == "mysql"):
            if token == "/*":
                pos = comment_end(sql, pos, dialect in ("generic", "postgres"))
            elif token == "#":
                newline = sql.find("\n", pos)
                pos = len(sql) if newline < 0 else newline
            original.append(" ")
            code.append(" ")
        elif token in ("'", '"', "`", "E'", "e'") or (token == "[" and dialect in ("generic", "sqlite")):
            close = "]" if token == "[" else token[-1]
            pos = quoted_end(sql, pos, close, token in ("E'", "e'") or dialect == "mysql")
            original.append(sql[start:pos])
            code.append("?" if close == "'" else "_")
        elif match.lastgroup == "dollar" and dialect in ("generic", "postgres"):
            end = sql.find(token, pos)
            pos = len(sql) if end < 0 else end + len(token)
            original.append(sql[start:pos])
            code.append("?")
        elif token == ";":
            if (statement := "".join(original).strip()):
                yield statement, "".join(code)
            original.clear()
            code.clear()
        else:
            original.append(token)
            code.append(token)
    if (statement := "".join(original).strip()):
        yield statement, "".join(code)


def split_statements(sql: str, dialect: str = "generic") -> List[str]:
    return [statement for statement, _ in statement_views(sql, dialect)]


def unguarded_write(code: str) -> bool:
    commands = [""]
    for token in re.findall(r"[:@]?[\w$]+|[()]", code.upper()):
        if token == "(":
            commands.append("")
        elif token == ")":
            if len(commands) > 1 and commands.pop() in WRITES_REQUIRING_WHERE:
                return True
        elif not commands[-1] or (commands[-1] == "WITH" and token in QUERY_COMMANDS):
            commands[-1] = token
        elif token == "WHERE" and commands[-1] in WRITES_REQUIRING_WHERE:
            commands[-1] = "FILTERED"
    return any(command in WRITES_REQUIRING_WHERE for command in commands)


def lint_sql(sql: str, filename: str, dialect: str) -> List[Dict[str, object]]:
    allow_drop = bool(ALLOW_DROP_RE.search(sql))
    findings: List[Dict[str, object]] = []
    for idx, (original, stmt) in enumerate(statement_views(sql, dialect), 1):
        snippet = " ".join(original.split())[:90]
        if DROP_TABLE_RE.search(stmt) and not allow_drop:
            findings.append({"file": filename, "stmt": idx, "severity": "blocker", "kind": "drop-object", "detail": snippet})
        if TRUNCATE_RE.search(stmt) and not allow_drop:
            findings.append({"file": filename, "stmt": idx, "severity": "blocker", "kind": "truncate", "detail": snippet})
        if DROP_COLUMN_RE.search(stmt) and not allow_drop:
            findings.append({"file": filename, "stmt": idx, "severity": "high", "kind": "drop-column", "detail": snippet})
        if ADD_NOT_NULL_RE.search(stmt) and not DEFAULT_RE.search(stmt):
            findings.append({
                "file": filename,
                "stmt": idx,
                "severity": "blocker",
                "kind": "not-null-no-default",
                "detail": f"ADD NOT NULL without DEFAULT breaks existing rows: {snippet}",
            })
        if dialect == "postgres" and CREATE_INDEX_RE.search(stmt) and not CONCURRENTLY_RE.search(stmt):
            findings.append({
                "file": filename,
                "stmt": idx,
                "severity": "high",
                "kind": "blocking-index",
                "detail": f"CREATE INDEX without CONCURRENTLY locks writes in Postgres: {snippet}",
            })
        if unguarded_write(stmt):
            findings.append({
                "file": filename,
                "stmt": idx,
                "severity": "blocker",
                "kind": "unguarded-dml",
                "detail": f"UPDATE/DELETE with no WHERE clause: {snippet}",
            })
    return findings


SQLiteValue = Union[None, int, float, str, bytes]
TypedRow = Tuple[Tuple[type, SQLiteValue], ...]


class DatabaseState(NamedTuple):
    schema: Dict[Tuple[str, str], str]
    rows: Dict[str, Counter[TypedRow]]


def database_state(conn: sqlite3.Connection) -> DatabaseState:
    schema = {}
    tables = {}
    for kind, name, sql in conn.execute("SELECT type, name, sql FROM main.sqlite_master"):
        if name.startswith("sqlite_") and name != "sqlite_sequence":
            continue
        if name != "sqlite_sequence":
            schema[kind, name] = sql
        if kind == "table":
            quoted = '"' + name.replace('"', '""') + '"'
            rows = Counter(tuple((type(value), value) for value in row)
                           for row in conn.execute(f"SELECT * FROM main.{quoted}"))
            if rows:
                tables[name] = rows
    return DatabaseState(schema, tables)


def state_check(conn: sqlite3.Connection, expected: DatabaseState, step: str) -> Dict[str, object]:
    actual = database_state(conn)
    for label, left, right in (("schema", expected.schema, actual.schema), ("data", expected.rows, actual.rows)):
        if left != right:
            changed = sorted(str(key) for key in left.keys() | right.keys() if left.get(key) != right.get(key))
            return {"step": step, "ok": False, "detail": f"{label} drift after {step}: {', '.join(changed)}"}
    return {"step": step, "ok": True, "detail": "schema and rows match the expected state"}


def run_asserts(conn: sqlite3.Connection, sql: str) -> Tuple[bool, str]:
    for idx, stmt in enumerate(split_statements(sql, "sqlite"), 1):
        cur = conn.execute(stmt)
        rows = cur.fetchall()
        if not rows:
            return False, f"assert query {idx} returned no rows"
        val = rows[0][0]
        if not val:
            return False, f"assert query {idx} evaluated falsy ({val!r})"
    return True, "all assertions passed"


def authorize_sqlite_action(
    action: int,
    name: Optional[str],
    value: Optional[str],
    _database: Optional[str],
    _source: Optional[str],
) -> int:
    # SQLite uses an empty attachment target for an in-place VACUUM.
    if action == sqlite3.SQLITE_ATTACH and name != "":
        return sqlite3.SQLITE_DENY
    if (
        action == sqlite3.SQLITE_PRAGMA
        and name is not None
        and name.lower() in {"temp_store_directory", "data_store_directory"}
        and value is not None
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def apply_sql(conn: sqlite3.Connection, sql: str) -> None:
    conn.executescript(sql)
    violation = conn.execute("PRAGMA foreign_key_check").fetchone()
    if violation is not None:
        table, row_id, parent, key = violation
        raise sqlite3.IntegrityError(
            f"foreign key violation in {table!r}, rowid {row_id!r} (references {parent!r}, key {key})"
        )


def run_sqlite_test(
    up_path: str,
    down_path: Optional[str],
    seed_path: Optional[str],
    assert_path: Optional[str],
    allow_non_idempotent: bool,
) -> Dict[str, object]:
    up_sql = open(up_path, encoding="utf-8").read()
    down_sql = open(down_path, encoding="utf-8").read() if down_path else None
    seed_sql = open(seed_path, encoding="utf-8").read() if seed_path else None
    assert_sql = open(assert_path, encoding="utf-8").read() if assert_path else None

    steps: List[Dict[str, object]] = []
    ok = True

    with tempfile.TemporaryDirectory(prefix="migrate-safe-") as folder:
        db_path = os.path.join(folder, "sandbox.db")
        conn = sqlite3.connect(db_path)
        conn.text_factory = lambda value: value.decode("utf-8", errors="surrogateescape")
        try:
            conn.set_authorizer(authorize_sqlite_action)
            conn.execute("PRAGMA foreign_keys=ON")
            if seed_sql:
                try:
                    apply_sql(conn, seed_sql)
                except sqlite3.Error as exc:
                    steps.append({"step": "seed", "ok": False, "detail": str(exc)})
                    return {"verdict": "UNSAFE", "steps": steps}
                steps.append({"step": "seed", "ok": True, "detail": "applied seed fixture"})
            before = database_state(conn)

            try:
                apply_sql(conn, up_sql)
                steps.append({"step": "up-1", "ok": True, "detail": "forward migration succeeded"})
            except sqlite3.Error as exc:
                steps.append({"step": "up-1", "ok": False, "detail": str(exc)})
                return {"verdict": "UNSAFE", "steps": steps}

            after_up = database_state(conn)

            if assert_sql:
                try:
                    a_ok, a_detail = run_asserts(conn, assert_sql)
                except sqlite3.Error as exc:
                    a_ok, a_detail = False, str(exc)
                steps.append({"step": "assert", "ok": a_ok, "detail": a_detail})
                if not a_ok:
                    ok = False

            try:
                apply_sql(conn, up_sql)
            except sqlite3.Error as exc:
                if not allow_non_idempotent:
                    steps.append({"step": "up-idempotent", "ok": False, "detail": f"second run failed ({exc})"})
                    return {"verdict": "UNSAFE", "steps": steps}
                repeat = state_check(conn, after_up, "up-idempotent")
                repeat["detail"] = f"second run failed ({exc}) [allowed]; {repeat['detail']}"
            else:
                repeat = state_check(conn, after_up, "up-idempotent")
            steps.append(repeat)
            if not repeat["ok"]:
                return {"verdict": "UNSAFE", "steps": steps}

            if down_sql is not None:
                try:
                    apply_sql(conn, down_sql)
                    rollback = state_check(conn, before, "down")
                    steps.append(rollback)
                    if not rollback["ok"]:
                        ok = False
                except sqlite3.Error as exc:
                    steps.append({"step": "down", "ok": False, "detail": str(exc)})
                    ok = False

                try:
                    apply_sql(conn, up_sql)
                    reapplied = state_check(conn, after_up, "up-after-down")
                    steps.append(reapplied)
                    if not reapplied["ok"]:
                        ok = False
                except sqlite3.Error as exc:
                    steps.append({"step": "up-after-down", "ok": False, "detail": str(exc)})
                    ok = False
        finally:
            conn.close()

    return {"verdict": "SAFE" if ok else "UNSAFE", "steps": steps}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    lint_p = sub.add_parser("lint", help="static SQL migration hazard linter")
    lint_p.add_argument("files", nargs="+", help="SQL migration file(s) to check")
    lint_p.add_argument("--dialect", choices=("generic", "postgres", "mysql", "sqlite"), default="generic")
    lint_p.add_argument("--down", help="companion rollback SQL file (checked for existence and non-empty body)")
    lint_p.add_argument("--require-down", action="store_true", help="fail if --down is missing or empty")
    lint_p.add_argument("--json", dest="json_out", help="write structured report to JSON file")

    test_p = sub.add_parser("sqlite-test", help="live SQLite sandbox round-trip test")
    test_p.add_argument("--up", required=True, help="forward SQL migration file")
    test_p.add_argument("--down", help="rollback SQL migration file")
    test_p.add_argument("--seed", help="optional initial schema and data fixture SQL file")
    test_p.add_argument("--assert-sql", help="optional SQL assertion queries (each must return a truthy value)")
    test_p.add_argument("--allow-non-idempotent", action="store_true", help="do not fail if running --up twice errors")
    test_p.add_argument("--json", dest="json_out", help="write structured report to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "lint":
        findings: List[Dict[str, object]] = []
        for path in args.files:
            if not os.path.isfile(path):
                print(f"migration_check.py: error: file not found: {path}", file=sys.stderr)
                return 2
            findings.extend(lint_sql(open(path, encoding="utf-8", errors="replace").read(), path, args.dialect))
        if args.require_down:
            if not args.down or not os.path.isfile(args.down) or not split_statements(open(args.down, encoding="utf-8", errors="replace").read(), args.dialect):
                findings.append({"file": args.down or "(none)", "stmt": 0, "severity": "blocker", "kind": "missing-down", "detail": "rollback migration missing or empty"})
        for item in findings:
            print(f"  {item['severity'].upper():<8} {item['file']}#{item['stmt']} [{item['kind']}] {item['detail']}")
        verdict = "CLEAN" if not findings else f"FINDINGS ({len(findings)} hazard(s))"
        print(f"MIGRATION LINT: {verdict}")
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump({"verdict": "CLEAN" if not findings else "FINDINGS", "findings": findings}, handle, indent=2)
        return 0 if not findings else 1

    if args.command == "sqlite-test":
        for opt in (args.up, args.down, args.seed, args.assert_sql):
            if opt and not os.path.isfile(opt):
                print(f"migration_check.py: error: file not found: {opt}", file=sys.stderr)
                return 2
        data = run_sqlite_test(args.up, args.down, args.seed, args.assert_sql, args.allow_non_idempotent)
        for step in data["steps"]:
            tag = "ok  " if step["ok"] else "FAIL"
            print(f"  {tag}  {step['step']:<15} {step['detail']}")
        print(f"MIGRATION: {data['verdict']}")
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
        return 0 if data["verdict"] == "SAFE" else 1

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
