#!/usr/bin/env python3
"""Package each skill for people and models that cannot read a skill folder.

For every skill folder under ./skills this writes, into ./dist:

  <name>.skill   a zip of the folder, ready to upload or unpack
  <name>.md      one file: SKILL.md followed by every reference, for pasting
                 into a system prompt

Both are built from the folder, so they cannot drift from it. Each package is
read back and compared with the folder before the script reports success.

  build.py              build every skill
  build.py NAME ...     build the named skills
  build.py --check      build to a temp folder and compare with ./dist
"""
import argparse
import filecmp
import os
import re
import sys
import tempfile
import zipfile

SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_DIRS = {".claude", "__pycache__", ".git"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def skill_files(folder):
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for filename in sorted(files):
            if filename not in SKIP_NAMES and not filename.endswith((".pyc", ".skill")):
                found.append(os.path.relpath(os.path.join(root, filename), folder))
    return sorted(found)


def write_zip(folder, name, target):
    files = skill_files(folder)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in files:
            info = zipfile.ZipInfo(f"{name}/{rel.replace(os.sep, '/')}", FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(folder, rel), "rb") as handle:
                archive.writestr(info, handle.read())
    with zipfile.ZipFile(target) as archive:
        packed = sorted(entry[len(name) + 1:] for entry in archive.namelist())
        for rel in files:
            with open(os.path.join(folder, rel), "rb") as handle:
                if archive.read(f"{name}/{rel.replace(os.sep, '/')}") != handle.read():
                    raise RuntimeError(f"{name}: {rel} differs inside the package")
    if packed != [rel.replace(os.sep, "/") for rel in files]:
        raise RuntimeError(f"{name}: package file list differs from the folder")
    return len(files)


def demote(markdown, levels):
    """Push headings down so a reference nests under the main document."""
    out, fenced = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("#"):
            line = "#" * levels + line
        out.append(line)
    return "\n".join(out)


def reference_order(skill_text, references):
    """References in the order SKILL.md first mentions them, then the rest."""
    position = {}
    for rel in references:
        found = skill_text.find(rel.replace(os.sep, "/"))
        position[rel] = found if found >= 0 else len(skill_text)
    return sorted(references, key=lambda rel: (position[rel], rel))


def write_flat(folder, name, target):
    with open(os.path.join(folder, "SKILL.md"), encoding="utf-8") as handle:
        skill_text = handle.read()
    body = re.sub(r"\A---\n.*?\n---\n", "", skill_text, count=1, flags=re.S).strip()
    extras = [rel for rel in skill_files(folder)
              if rel.endswith(".md") and rel != "SKILL.md" and rel.split(os.sep)[0] in ("references", "assets")]
    parts = [body]
    if extras:
        parts.append("---\n\n# Reference files\n\nThe sections below are the files the text above points to, in one place.")
    for rel in reference_order(skill_text, extras):
        with open(os.path.join(folder, rel), encoding="utf-8") as handle:
            content = handle.read().strip()
        label = rel.replace(os.sep, "/")
        parts.append(f"## File: `{label}`\n\n{demote(content, 2)}")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("\n\n".join(parts) + "\n")
    with open(target, encoding="utf-8") as handle:
        flat = handle.read()
    for rel in extras:
        with open(os.path.join(folder, rel), encoding="utf-8") as handle:
            for line in handle.read().splitlines():
                if line.strip() and not line.startswith("#") and line.strip() not in flat:
                    raise RuntimeError(f"{name}: a line of {rel} is missing from the single-file copy")
    return len(extras)


def build(skills_dir, dist, names):
    os.makedirs(dist, exist_ok=True)
    built = []
    for name in names:
        folder = os.path.join(skills_dir, name)
        if not os.path.isfile(os.path.join(folder, "SKILL.md")):
            raise RuntimeError(f"{name}: no SKILL.md in {folder}")
        files = write_zip(folder, name, os.path.join(dist, f"{name}.skill"))
        references = write_flat(folder, name, os.path.join(dist, f"{name}.md"))
        built.append((name, files, references))
    return built


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="skills to build (default: all)")
    parser.add_argument("--skills-dir", default="skills", help="default: ./skills")
    parser.add_argument("--dist", default="dist", help="default: ./dist")
    parser.add_argument("--check", action="store_true", help="fail if dist/ is missing or out of date")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.skills_dir):
        print(f"error: {args.skills_dir} is not a directory", file=sys.stderr)
        return 2
    names = args.names or sorted(
        entry for entry in os.listdir(args.skills_dir)
        if os.path.isfile(os.path.join(args.skills_dir, entry, "SKILL.md")))
    try:
        if args.check:
            with tempfile.TemporaryDirectory() as fresh:
                build(args.skills_dir, fresh, names)
                expected = set(os.listdir(fresh))
                stale = [f for f in sorted(expected)
                         if not os.path.isfile(os.path.join(args.dist, f))
                         or not filecmp.cmp(os.path.join(fresh, f), os.path.join(args.dist, f), shallow=False)]
            orphans = []
            if not args.names and os.path.isdir(args.dist):
                with os.scandir(args.dist) as entries:
                    orphans = sorted(entry.name for entry in entries
                                     if entry.name.endswith((".skill", ".md")) and entry.name not in expected
                                     and (entry.is_symlink() or entry.is_file()))
            for filename in stale:
                print(f"{os.path.join(args.dist, filename)} is missing or out of date")
            for filename in orphans:
                print(f"{os.path.join(args.dist, filename)} has no corresponding source skill")
            print(f"{len(names)} skills checked, {len(stale) + len(orphans)} stale files")
            return 1 if stale or orphans else 0
        for name, files, references in build(args.skills_dir, args.dist, names):
            print(f"{name}: {files} files packaged, {references} reference files in the single-file copy")
    except (RuntimeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
