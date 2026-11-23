#!/usr/bin/env python3
"""Check every skill in a skills directory before it is published.

Each rule exists because a real skill broke it.

  validate.py                  check ./skills without executing bundled scripts
  validate.py PATH             check another skills directory
  validate.py --strict-prose   also refuse em dashes in prose
  validate.py --run-scripts    also run trusted bundled Python scripts with --help

A skill fails when its name differs from its folder, its description does not
say when to use it, it points at a file it does not ship, it carries a home
path, an email address or a public IP address, or it ships stray files.
Descriptions that share most of their trigger words are listed as notes,
because two skills that claim the same request give an agent two sets of orders.

Exit 0 when clean, 1 when any rule fails, 2 on bad usage.
"""
import argparse
import ipaddress
import os
import re
import subprocess
import sys

NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_BODY_LINES = 400
CONTENTS_AFTER_LINES = 100
STRAY_NAMES = {".DS_Store", "Thumbs.db"}
STRAY_SUFFIXES = (".skill", ".pyc", ".zip")
STRAY_DIRS = {".claude", "__pycache__", ".git"}
TEXT_SUFFIXES = (".md", ".py", ".sh", ".json", ".yaml", ".yml", ".txt")
LOCAL_REF_RE = re.compile(r"`((?:references|scripts|assets)/[A-Za-z0-9_./-]+)`")
# Lowercase names only: AGENTS.md, CLAUDE.md and README.md are other projects' files, not siblings.
SIBLING_REF_RE = re.compile(r"`([a-z0-9][a-z0-9_-]*\.md)`")
HOME_PATH_RE = re.compile(r"(/Users/[A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+|[A-Z]:\\Users\\)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")
SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
SAFE_NETWORKS = [ipaddress.ip_network(n) for n in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.0/8", "0.0.0.0/32", "10.0.0.0/8")]
EM_DASH = "—"


def split_frontmatter(text):
    """Return (dict, body) for a file that opens with a --- block, else (None, text)."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, text
    fields, parent = {}, None
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[0] in " \t"
        key, sep, value = line.strip().partition(":")
        if not sep:
            return None, text
        value = value.strip().strip('"').strip("'")
        if indented and parent:
            fields[parent][key.strip()] = value
        else:
            parent = key.strip()
            fields[parent] = value if value else {}
    return fields, text[end + 5:]


def prose_lines(text):
    """Yield (line number, line) outside fenced code blocks, with inline code removed."""
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            yield number, re.sub(r"`[^`]*`", "", line)


def is_public_ip(text):
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return not any(address in network for network in SAFE_NETWORKS)


def check_text_file(path, rel, problems, strict_prose=False):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except UnicodeDecodeError:
        problems.append(f"{rel}: not valid UTF-8 text")
        return None
    for number, line in enumerate(text.splitlines(), 1):
        if HOME_PATH_RE.search(line):
            problems.append(f"{rel}:{number}: home directory path")
        for email in EMAIL_RE.findall(line):
            if not email.lower().endswith(SAFE_EMAIL_DOMAINS):
                problems.append(f"{rel}:{number}: email address")
        for candidate in IPV4_RE.findall(line):
            if is_public_ip(candidate):
                problems.append(f"{rel}:{number}: IP address {candidate} (use a documentation range)")
    if strict_prose and rel.endswith(".md"):
        for number, line in prose_lines(text):
            if EM_DASH in line:
                problems.append(f"{rel}:{number}: em dash in prose")
    return text


STOPWORDS = set("""about above after again also another before being between both cannot could does doing during each
every from have having here into itself just more most must need needs other over same should some such than that the
their them then there these they this those through under until very were what when where which while will with within
without would your yours user users asks asked says said wants want when whenever use used uses using skill skills work
works working task tasks any all and are but can for has its not one our out the was who why you""".split())


def trigger_words(description):
    return {word for word in re.findall(r"[a-z][a-z-]{3,}", description.lower()) if word not in STOPWORDS}


def overlap_notes(descriptions, threshold=0.34):
    """Pairs of skills whose descriptions share a large part of their words."""
    notes = []
    names = sorted(descriptions)
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            a, b = trigger_words(descriptions[first]), trigger_words(descriptions[second])
            if len(a) < 5 or len(b) < 5:
                continue
            shared = a & b
            if len(shared) / min(len(a), len(b)) >= threshold:
                notes.append(f"note: {first} and {second} may claim the same requests "
                             f"(shared words: {', '.join(sorted(shared)[:8])})")
    return notes


def check_skill(folder, problems, run_scripts=False, strict_prose=False, descriptions=None):
    name = os.path.basename(folder.rstrip(os.sep))
    skill_md = os.path.join(folder, "SKILL.md")
    if not os.path.isfile(skill_md):
        problems.append(f"{name}: no SKILL.md")
        return
    texts = {}
    for root, dirs, files in os.walk(folder):
        for stray in sorted(set(dirs) & STRAY_DIRS):
            problems.append(f"{os.path.relpath(os.path.join(root, stray), os.path.dirname(folder))}: stray directory")
        dirs[:] = [d for d in dirs if d not in STRAY_DIRS]
        for filename in files:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, os.path.dirname(folder))
            if filename in STRAY_NAMES or filename.endswith(STRAY_SUFFIXES):
                problems.append(f"{rel}: stray file")
            elif filename.endswith(TEXT_SUFFIXES):
                texts[rel] = check_text_file(path, rel, problems, strict_prose)

    text = texts.get(os.path.join(name, "SKILL.md")) or ""
    fields, body = split_frontmatter(text)
    if fields is None:
        problems.append(f"{name}/SKILL.md: missing or malformed frontmatter")
        return
    for key in sorted(set(fields) - ALLOWED_KEYS):
        problems.append(f"{name}/SKILL.md: unknown frontmatter key '{key}'")
    declared = fields.get("name", "")
    if declared != name:
        problems.append(f"{name}/SKILL.md: name '{declared}' does not equal the folder name")
    if not isinstance(declared, str) or not NAME_RE.match(declared) or len(declared) > MAX_NAME:
        problems.append(f"{name}/SKILL.md: name must be lowercase words joined by hyphens, at most {MAX_NAME} characters")
    description = fields.get("description", "")
    if descriptions is not None and isinstance(description, str):
        descriptions[name] = description
    if not isinstance(description, str) or not description:
        problems.append(f"{name}/SKILL.md: no description")
    else:
        if len(description) > MAX_DESCRIPTION:
            problems.append(f"{name}/SKILL.md: description is {len(description)} characters, limit {MAX_DESCRIPTION}")
        if not re.search(r"\bUse\b", description):
            problems.append(f"{name}/SKILL.md: description does not say when to use the skill")
    body_lines = body.count("\n")
    if body_lines > MAX_BODY_LINES:
        problems.append(f"{name}/SKILL.md: body is {body_lines} lines, limit {MAX_BODY_LINES}")

    for rel, content in sorted(texts.items()):
        if content is None or not rel.endswith(".md"):
            continue
        base = os.path.dirname(os.path.join(os.path.dirname(folder), rel))
        for target in LOCAL_REF_RE.findall(content):
            if not os.path.exists(os.path.join(folder, target.rstrip("/"))):
                problems.append(f"{rel}: points at `{target}`, which is not in the skill")
        if os.sep + "references" + os.sep in os.sep + rel:
            for target in SIBLING_REF_RE.findall(content):
                if target != "SKILL.md" and not os.path.exists(os.path.join(base, target)):
                    problems.append(f"{rel}: points at `{target}`, which is not beside it")
            if content.count("\n") > CONTENTS_AFTER_LINES and "## Contents" not in content:
                problems.append(f"{rel}: over {CONTENTS_AFTER_LINES} lines with no '## Contents' list")

    scripts = os.path.join(folder, "scripts")
    if run_scripts and os.path.isdir(scripts):
        for filename in sorted(os.listdir(scripts)):
            if not filename.endswith(".py"):
                continue
            result = subprocess.run([sys.executable, os.path.join(scripts, filename), "--help"],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode != 0 or not result.stdout.strip():
                problems.append(f"{name}/scripts/{filename}: --help did not exit 0 with usage text")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     allow_abbrev=False)
    parser.add_argument("skills_dir", nargs="?", default="skills", help="directory of skill folders (default: ./skills)")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--run-scripts", action="store_true", help="run trusted bundled Python scripts with --help")
    execution.add_argument("--no-run", dest="run_scripts", action="store_false", help="use static checks only (default)")
    parser.set_defaults(run_scripts=False)
    parser.add_argument("--strict-prose", action="store_true", help="also refuse em dashes in prose")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.skills_dir):
        print(f"error: {args.skills_dir} is not a directory", file=sys.stderr)
        return 2
    folders = sorted(
        os.path.join(args.skills_dir, entry) for entry in os.listdir(args.skills_dir)
        if os.path.isdir(os.path.join(args.skills_dir, entry)) and not entry.startswith("."))
    if not folders:
        print(f"error: no skill folders in {args.skills_dir}", file=sys.stderr)
        return 2
    problems, descriptions = [], {}
    for folder in folders:
        check_skill(folder, problems, run_scripts=args.run_scripts, strict_prose=args.strict_prose,
                    descriptions=descriptions)
    for problem in problems:
        print(problem)
    notes = overlap_notes(descriptions)
    for note in notes:
        print(note)
    print(f"{len(folders)} skills checked, {len(problems)} problems, {len(notes)} notes")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
