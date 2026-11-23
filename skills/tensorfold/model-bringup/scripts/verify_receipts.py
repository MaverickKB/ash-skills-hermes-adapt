#!/usr/bin/env python3
"""Verify paired or solo-versus-burst inference token-hash receipts offline.

Every reply must carry a full 64-hex `token_sha256` field. A missing or
truncated hash is rejected as an invalid receipt (exit 2), never silently skipped.
`text_sha256` and token counts are also verified when present on both sides.

Subcommands:
  paired CONTROL.json CANDIDATE.json   Compare two receipts reply by reply.
  bursts RECEIPT.json                  Compare concurrent burst streams against solo references.

Exit codes: 0 exact match, 1 mismatch, 2 invalid receipt or usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


def read_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def normalize_rows(doc: object) -> Dict[str, dict]:
    if isinstance(doc, list):
        doc = {str(i + 1): row for i, row in enumerate(doc)}
    if not isinstance(doc, dict) or not doc or not all(isinstance(v, dict) for v in doc.values()):
        raise ValueError("expected a non-empty list of replies or mapping from fixture name to reply")
    return doc


def require_sha256(row: dict, key: str) -> str:
    val = row.get(key)
    if not isinstance(val, str) or SHA256_RE.fullmatch(val) is None:
        raise ValueError(f"missing or invalid {key} (expected full 64-hex SHA-256)")
    return val.lower()


def extract_count(row: dict, keys: Sequence[str]) -> Optional[int]:
    for k in keys:
        if type(row.get(k)) is int:
            return row[k]
    return None


def compare_pair(label: str, left: dict, right: dict) -> List[str]:
    diffs: List[str] = []
    for field in ("token_sha256", "text_sha256"):
        if field == "text_sha256" and field not in left and field not in right:
            continue
        try:
            if require_sha256(left, field) != require_sha256(right, field):
                diffs.append(field)
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc
    for names in (("prompt_tokens", "n"), ("completion_tokens", "output", "tokens")):
        a, b = extract_count(left, names), extract_count(right, names)
        if a is not None and b is not None and a != b:
            diffs.append(names[0])
    return diffs


def build_paired(control: object, candidate: object) -> List[Tuple[str, dict, dict]]:
    before, after = normalize_rows(control), normalize_rows(candidate)
    if before.keys() != after.keys():
        raise ValueError("control and candidate have different fixture names or reply counts")
    return [(name, before[name], after[name]) for name in before]


def build_bursts(doc: object) -> List[Tuple[str, dict, dict]]:
    if not isinstance(doc, dict) or "reference" not in doc or "bursts" not in doc:
        raise ValueError("bursts receipt requires 'reference' and 'bursts' keys")
    reference = normalize_rows(doc["reference"])
    pairs: List[Tuple[str, dict, dict]] = []
    for burst in doc["bursts"]:
        level, rep = burst["level"], burst["rep"]
        if level == 1:
            continue
        streams = burst["streams"]
        if len(streams) != level:
            raise ValueError(f"C{level} rep {rep + 1} has {len(streams)} streams, expected {level}")
        for idx, stream in enumerate(streams, 1):
            name = stream["name"]
            if name not in reference:
                raise ValueError(f"C{level} rep {rep + 1} stream {idx}: fixture {name!r} missing from reference")
            pairs.append((f"C{level} rep {rep + 1} stream {idx} ({name})", reference[name], stream))
    if not pairs:
        raise ValueError("no concurrent burst streams to compare")
    return pairs


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    p_pair = sub.add_parser("paired", help="compare a control receipt with a candidate receipt")
    p_pair.add_argument("control", type=Path)
    p_pair.add_argument("candidate", type=Path)
    p_burst = sub.add_parser("bursts", help="compare concurrent burst streams against solo references")
    p_burst.add_argument("receipt", type=Path)
    args = parser.parse_args(argv)

    try:
        pairs = build_paired(read_json(args.control), read_json(args.candidate)) if args.mode == "paired" else build_bursts(read_json(args.receipt))
        mismatches: Dict[str, List[str]] = {}
        for label, left, right in pairs:
            diffs = compare_pair(label, left, right)
            if diffs:
                mismatches[label] = diffs
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"verify_receipts.py: invalid receipt: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"compared": len(pairs), "matched": len(pairs) - len(mismatches), "mismatches": mismatches}, indent=2))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
