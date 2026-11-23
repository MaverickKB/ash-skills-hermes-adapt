#!/usr/bin/env python3
"""Find mechanical AI-writing tells in Markdown or plain text files."""

from __future__ import annotations

import argparse
import bisect
import re
import sys
from typing import Iterator, List, NamedTuple, NoReturn, Pattern, Sequence, Tuple

PROG = "scan"

RULES = {
    "em-dash": "em dash",
    "en-dash": "en dash, unless it joins two numbers as a range",
    "curly-quote": "curly single or double quote (reported once per line)",
    "ai-vocabulary": "stock word that generated text overuses",
    "chatbot-phrase": "chatbot opener, closer or announcement",
    "inline-header": "list item whose bold label and colon are restated by the text",
    "title-case-heading": "heading in title case",
    "emoji": "emoji in a heading or list item",
    "not-just-but": "'not just X, but Y' construction",
}

EPILOG = (
    "rules:\n"
    + "\n".join(f"  {name:<20}{text}" for name, text in RULES.items())
    + "\n\nskipped: fenced code blocks, inline code, URLs, link targets, blockquotes\n"
    "exit status: 0 clean, 1 findings, 2 bad input"
)

FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
BLOCKQUOTE = re.compile(r"^\s*>")
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
LINK_TARGET = re.compile(r"(?<=\])\([^()\s]*(?:\s+\"[^\"]*\")?\)")
REF_DEFINITION = re.compile(r"^(\s{0,3}\[[^\]]+\]:\s*)(\S+)")
URL = re.compile(r"(?:https?://|ftp://|mailto:|www\.)[^\s<>\"']+", re.IGNORECASE)

LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
INLINE_HEADER = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?"
    r"(\*\*|__)(?P<label>[^*_]+?)(?::\1|\1\s*:)\s*(?P<body>.*)$"
)
PLAIN_WORD = re.compile(r"^[A-Za-z][a-z]*(?:[-'][A-Za-z][a-z]*)*$")

# A single line break inside a paragraph counts as a space; a new block does not.
SOFT_BREAK = r"\n(?![ \t]*(?:\n|[-*+][ \t]|#|\||\d+[.)][ \t]))"
GAP = rf"(?:[ \t]|{SOFT_BREAK})+"

VOCABULARY = (
    r"additionally",
    r"crucial(?:ly)?",
    r"delv(?:e|es|ed|ing)",
    r"enduring",
    r"enhanc(?:e|es|ed|ing|ement|ements)",
    r"foster(?:s|ed|ing)?(?!\s+(?:care|carer|child|children|parent|parents|family|home))",
    r"garner(?:s|ed|ing)?",
    r"interplay",
    r"intricate(?:ly)?",
    r"intricac(?:y|ies)",
    r"(?<!portrait and )(?<!portrait or )landscapes?"
    r"(?!\s+(?:mode|orientation|format|layout|view|photo|photograph|photography"
    r"|painting|paintings|architect|architecture|design|gardening|gardener))",
    r"pivotal",
    r"showcas(?:e|es|ed|ing)",
    r"tapestr(?:y|ies)",
    r"(?<!old )(?<!new )(?<!will and )testament",
    r"underscor(?:ed|ing)",
    r"underscores?(?=\s+(?:the|a|an|that|how|why|its|their|our|this|these|those|just)\b)",
    r"vibrant(?:ly)?",
    # The verb only. "Utilization" is a literal term for how busy a resource is.
    r"utiliz(?:e|es|ed|ing)",
    r"leverag(?:es|ed|ing)",
    r"facilitat(?:e|es|ed|ing)",
    r"seamless(?:ly)?",
    r"furthermore",
    r"moreover",
    r"holistic(?:ally)?",
    r"game[- ]chang(?:er|ers|ing)",
    r"ever[- ]evolving",
)

CHATBOT_PHRASES = (
    rf"hope{GAP}this{GAP}helps",
    rf"let{GAP}me{GAP}know{GAP}if",
    rf"of{GAP}course[ \t]*!",
    r"certainly[ \t]*!",
    rf"smoking{GAP}gun",
    rf"great{GAP}question",
    rf"happy{GAP}to{GAP}help",
    rf"you(?:'|\u2019)?re{GAP}absolutely{GAP}right",
    rf"worth{GAP}noting{GAP}that",
    rf"important{GAP}to{GAP}note{GAP}that",
)

NOT_JUST_BUT = (
    rf"(?:\bnot|n't){GAP}(?:just|only|merely|simply)\b"
    rf"(?:[^.!?;:\n]|{SOFT_BREAK}){{1,100}}?\bbut\b"
)


def any_of(parts: Sequence[str]) -> Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(parts) + ")", re.IGNORECASE)


TEXT_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("em-dash", re.compile("\u2014")),
    ("en-dash", re.compile("(?<![0-9])\u2013|\u2013(?![0-9])")),
    ("curly-quote", re.compile("[\u2018\u2019\u201c\u201d]")),
    ("ai-vocabulary", re.compile(r"\b(?:" + "|".join(VOCABULARY) + r")\b", re.IGNORECASE)),
    ("chatbot-phrase", any_of(CHATBOT_PHRASES)),
    ("not-just-but", re.compile(NOT_JUST_BUT, re.IGNORECASE)),
)
ONCE_PER_LINE = frozenset({"curly-quote"})

SMALL_WORDS = frozenset(
    "a an and as at but by for from if in into nor of on onto or over per so than that "
    "the to up via vs with yet about after before between through without within under "
    "across against among around during until versus when while".split()
)

# Words that are never proper nouns, so one capital is enough to call the heading title case.
COMMON_HEADING_WORDS = frozenset(
    "changes checklist conclusion configuration defaults details example examples features "
    "format guide installation issues links notes options overview patterns practices "
    "principles process questions reference requirements resources results review rules "
    "settings setup start started steps structure summary testing tests thoughts tips "
    "usage workflow".split()
)

EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x23E9, 0x23FA),
    (0x231A, 0x231B),
)


class Finding(NamedTuple):
    line: int
    column: int
    rule: str
    excerpt: str


class Document(NamedTuple):
    text: str
    masked: str
    line_starts: List[int]
    structural: List[bool]


class InputError(Exception):
    pass


class OneLineParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, f"{self.prog}: error: {message}\n")


def blank(match: "re.Match[str]") -> str:
    return " " * (match.end() - match.start())


def blank_reference_target(match: "re.Match[str]") -> str:
    return match.group(1) + " " * len(match.group(2))


def mask_inline(line: str) -> str:
    line = CODE_SPAN.sub(blank, line)
    line = LINK_TARGET.sub(blank, line)
    line = REF_DEFINITION.sub(blank_reference_target, line)
    return URL.sub(blank, line)


def fence_opened_by(line: str) -> str:
    match = FENCE_OPEN.match(line)
    if not match:
        return ""
    run, info = match.groups()
    if run[0] == "`" and "`" in info:
        return ""
    return run


def closes_fence(line: str, fence: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= len(fence) and set(stripped) == {fence[0]}


def front_matter_length(lines: Sequence[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return index + 1
    return 0


def mask_lines(lines: Sequence[str]) -> Tuple[List[str], List[bool]]:
    masked: List[str] = []
    structural: List[bool] = []
    front = front_matter_length(lines)
    fence = ""
    for index, line in enumerate(lines):
        skip = True
        if fence:
            if closes_fence(line, fence):
                fence = ""
        elif not BLOCKQUOTE.match(line):
            fence = fence_opened_by(line)
            skip = bool(fence)
        masked.append(" " * len(line) if skip else mask_inline(line))
        structural.append(not skip and index >= front)
    return masked, structural


def load_document(text: str) -> Document:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    masked_lines, structural = mask_lines(lines)
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    return Document(text, "\n".join(masked_lines), starts, structural)


def line_index(doc: Document, offset: int) -> int:
    return bisect.bisect_right(doc.line_starts, offset) - 1


def line_end(doc: Document, index: int) -> int:
    if index + 1 < len(doc.line_starts):
        return doc.line_starts[index + 1] - 1
    return len(doc.text)


def excerpt(doc: Document, start: int, end: int, width: int = 64) -> str:
    end = min(end, start + width)
    floor = doc.line_starts[line_index(doc, start)]
    ceiling = line_end(doc, line_index(doc, max(end - 1, start)))
    pad = max((width - (end - start)) // 2, 12)
    low = max(start - pad, floor)
    high = min(end + pad, ceiling)
    snippet = " ".join(doc.text[low:high].split())
    return ("..." if low > floor else "") + snippet + ("..." if high < ceiling else "")


def make_finding(doc: Document, rule: str, start: int, end: int) -> Finding:
    index = line_index(doc, start)
    return Finding(index + 1, start - doc.line_starts[index], rule, excerpt(doc, start, end))


def text_findings(doc: Document) -> Iterator[Finding]:
    for rule, pattern in TEXT_RULES:
        seen_lines = set()
        for match in pattern.finditer(doc.masked):
            index = line_index(doc, match.start())
            if rule in ONCE_PER_LINE and index in seen_lines:
                continue
            seen_lines.add(index)
            yield make_finding(doc, rule, match.start(), match.end())


def stem(word: str) -> str:
    word = word.lower()
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            break
    return word[:5]


def restates_label(label: str, body: str) -> bool:
    label_stems = {stem(word) for word in re.findall(r"[A-Za-z]{4,}", label)}
    opening = re.findall(r"[A-Za-z]+", body)[:6]
    return any(len(word) >= 4 and stem(word) in label_stems for word in opening)


def heading_content_words(heading: str) -> List[str]:
    words: List[str] = []
    at_start = True
    for token in heading.split():
        bare = token.strip("\"'()[]{}*_,.;!?")
        word = bare.rstrip(":")
        if any(char.isalpha() for char in word):
            if not at_start and PLAIN_WORD.match(word) and word.lower() not in SMALL_WORDS:
                words.append(word)
            at_start = False
        if bare.endswith(":"):
            at_start = True
    return words


def is_title_case(heading: str) -> bool:
    words = heading_content_words(heading)
    capitalised = [word for word in words if word[0].isupper()]
    if not capitalised or len(capitalised) != len(words):
        return False
    return len(capitalised) >= 2 or capitalised[0].lower() in COMMON_HEADING_WORDS


def is_emoji(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in EMOJI_RANGES)


def first_emoji(line: str) -> int:
    for column, char in enumerate(line):
        if is_emoji(char):
            return column
    return -1


def structural_findings(doc: Document) -> Iterator[Finding]:
    for index, line in enumerate(doc.masked.split("\n")):
        if not doc.structural[index]:
            continue
        base = doc.line_starts[index]
        heading = HEADING.match(line)
        if heading and is_title_case(heading.group(1)):
            yield make_finding(doc, "title-case-heading", base + heading.start(1), base + heading.end(1))
        header = INLINE_HEADER.match(line)
        if header and restates_label(header.group("label"), header.group("body")):
            yield make_finding(doc, "inline-header", base + header.start(1), base + len(line))
        column = first_emoji(line) if heading or LIST_ITEM.match(line) else -1
        if column >= 0:
            yield make_finding(doc, "emoji", base + column, base + column + 1)


def scan_text(text: str, ignored: Sequence[str] = ()) -> List[Finding]:
    doc = load_document(text)
    findings = list(text_findings(doc)) + list(structural_findings(doc))
    return sorted(finding for finding in findings if finding.rule not in ignored)


def decode(data: bytes, name: str) -> str:
    if b"\x00" in data:
        raise InputError(f"{name}: looks like a binary file")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InputError(f"{name}: not valid UTF-8 text") from None


def read_input(path: str) -> Tuple[str, str]:
    if path == "-":
        return "<stdin>", decode(sys.stdin.buffer.read(), "<stdin>")
    try:
        with open(path, "rb") as handle:
            return path, decode(handle.read(), path)
    except OSError as error:
        raise InputError(f"{path}: {error.strerror or error}") from None


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = OneLineParser(
        prog=PROG,
        description="Report mechanical AI-writing tells as 'file:line: rule: excerpt'. "
        "It finds mechanical tells only; the judgment rules still need a read.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="FILE",
        help="Markdown or text file; '-' or no file reads stdin",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="RULE",
        help="skip a rule; repeat to skip several",
    )
    args = parser.parse_args(argv)
    unknown = [rule for rule in args.ignore if rule not in RULES]
    if unknown:
        parser.error(f"unknown rule: {unknown[0]}")
    return args


def configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def summary(total: int) -> str:
    if total == 0:
        return "clean"
    return "1 finding" if total == 1 else f"{total} findings"


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    configure_output()
    total = 0
    failed = False
    for path in args.paths or ["-"]:
        try:
            name, text = read_input(path)
        except InputError as error:
            print(f"{PROG}: error: {error}", file=sys.stderr)
            failed = True
            continue
        for finding in scan_text(text, args.ignore):
            print(f"{name}:{finding.line}: {finding.rule}: {finding.excerpt}")
            total += 1
    if failed:
        return 2
    print(f"{PROG}: {summary(total)}", file=sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
