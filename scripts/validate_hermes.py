"""Validate this fork against Hermes Agent skill conventions.

Checks:
  - every skill is one level under skills/ and has a SKILL.md
  - frontmatter name matches the folder name
  - description exists and is at most 1024 characters
  - metadata.hermes.tags exists and is a non-empty list
  - metadata.hermes.related_skills point at real skills
  - skills.sh.json lists every skill once, with no unknown names
  - relative markdown links resolve
  - no leftover CLAUDE.md references or upstream install commands

Run: python3 scripts/validate_hermes.py
Exit code 0 when clean, 1 when an error is found.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT, "skills")
GROUPINGS_FILE = os.path.join(ROOT, "skills.sh.json")
MAX_DESCRIPTION = 1024

LINK_PATTERN = re.compile(r"\]\(([^)]+)\)")
LEFT_OVER_PATTERNS = [
    ("CLAUDE.md reference", re.compile(r"CLAUDE\.md")),
    ("upstream install command", re.compile(r"npx skills add ashhart/skills")),
]

try:
    import yaml as yaml_module
    HAVE_YAML = True
except ImportError:
    yaml_module = None
    HAVE_YAML = False


def split_frontmatter(text):
    """Return (frontmatter_text, body) or (None, text) when the fence is missing."""
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    return text[3:end], text[end + 4:]


def parse_simple_frontmatter(frontmatter_text):
    """Fallback reader for the single-line keys this fork uses."""
    data = {}
    for line in frontmatter_text.split("\n"):
        if line.startswith("name:"):
            data["name"] = line[len("name:"):].strip()
        elif line.startswith("description:"):
            data["description"] = line[len("description:"):].strip()
    metadata = {}
    if "\n  hermes:" in "\n" + frontmatter_text:
        metadata["hermes"] = {"present": True}
    data["metadata"] = metadata
    return data


def read_frontmatter(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    frontmatter_text, body = split_frontmatter(text)
    if frontmatter_text is None:
        return None, body, "missing frontmatter fence"
    if yaml_module is None:
        return parse_simple_frontmatter(frontmatter_text), body, None
    try:
        data = yaml_module.safe_load(frontmatter_text)
    except yaml_module.YAMLError as error:
        return None, body, "frontmatter YAML error: " + str(error).split("\n")[0]
    if not isinstance(data, dict):
        return None, body, "frontmatter is not a mapping"
    return data, body, None


def hermes_tags(frontmatter):
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return None
    hermes = metadata.get("hermes")
    if not isinstance(hermes, dict):
        return None
    return hermes.get("tags")


def hermes_related(frontmatter):
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return []
    hermes = metadata.get("hermes")
    if not isinstance(hermes, dict):
        return []
    related = hermes.get("related_skills")
    if isinstance(related, list):
        return related
    return []


def check_skill(skill_name, errors, warnings, known_names):
    skill_dir = os.path.join(SKILLS_DIR, skill_name)
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        errors.append(skill_name + ": missing SKILL.md")
        return
    frontmatter, body, error = read_frontmatter(skill_md)
    if error:
        errors.append(skill_name + ": " + error)
        return
    if not isinstance(frontmatter, dict):
        errors.append(skill_name + ": frontmatter did not parse to a mapping")
        return

    name = frontmatter.get("name")
    if name != skill_name:
        errors.append(skill_name + ": frontmatter name is " + repr(name))

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(skill_name + ": missing description")
    elif len(description) > MAX_DESCRIPTION:
        errors.append(skill_name + ": description is " + str(len(description)) + " chars")

    tags = hermes_tags(frontmatter)
    if not isinstance(tags, list) or not tags:
        errors.append(skill_name + ": metadata.hermes.tags missing or empty")

    for related in hermes_related(frontmatter):
        if related not in known_names:
            warnings.append(skill_name + ": related skill not found: " + str(related))

    for label, pattern in LEFT_OVER_PATTERNS:
        if pattern.search(body):
            warnings.append(skill_name + ": leftover " + label)


def check_layout(errors):
    names = []
    for entry in sorted(os.listdir(SKILLS_DIR)):
        path = os.path.join(SKILLS_DIR, entry)
        if not os.path.isdir(path):
            continue
        names.append(entry)
        nested = []
        for base, dirs, files in os.walk(path):
            if "SKILL.md" in files and os.path.abspath(base) != os.path.abspath(path):
                nested.append(os.path.relpath(base, ROOT))
        if nested:
            errors.append(entry + ": nested skill folders: " + ", ".join(nested))
    return names


def check_groupings(known_names, errors):
    if not os.path.isfile(GROUPINGS_FILE):
        errors.append("skills.sh.json: missing")
        return
    try:
        with open(GROUPINGS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        errors.append("skills.sh.json: " + str(error))
        return
    groupings = data.get("groupings")
    if not isinstance(groupings, list):
        errors.append("skills.sh.json: groupings is not a list")
        return

    listed = {}
    for group in groupings:
        if not isinstance(group, dict):
            errors.append("skills.sh.json: grouping is not an object")
            continue
        title = group.get("title")
        skills = group.get("skills")
        if not isinstance(title, str) or not title.strip():
            errors.append("skills.sh.json: grouping has no title")
        if not isinstance(skills, list):
            errors.append("skills.sh.json: grouping has no skills list")
            continue
        for skill in skills:
            listed[skill] = listed.get(skill, 0) + 1

    for skill_name in known_names:
        count = listed.get(skill_name, 0)
        if count == 0:
            errors.append(skill_name + ": missing from skills.sh.json")
        elif count > 1:
            errors.append(skill_name + ": listed " + str(count) + " times in skills.sh.json")
    for skill_name in listed:
        if skill_name not in known_names:
            errors.append("skills.sh.json: unknown skill " + str(skill_name))


def check_links(errors):
    broken = 0
    for base, dirs, files in os.walk(ROOT):
        if ".git" in base.split(os.sep):
            continue
        for filename in files:
            if not filename.endswith(".md"):
                continue
            path = os.path.join(base, filename)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for target in LINK_PATTERN.findall(text):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                clean_target = target.split("#")[0]
                resolved = os.path.normpath(os.path.join(base, clean_target))
                if not os.path.exists(resolved):
                    broken += 1
                    errors.append(os.path.relpath(path, ROOT) + ": broken link " + target)
    return broken


def main():
    errors = []
    warnings = []

    if not os.path.isdir(SKILLS_DIR):
        print("ERROR: skills/ is missing")
        return 1

    known_names = check_layout(errors)
    if len(known_names) != 40:
        warnings.append("expected 40 skills, found " + str(len(known_names)))

    for skill_name in known_names:
        check_skill(skill_name, errors, warnings, known_names)

    check_groupings(known_names, errors)
    check_links(errors)

    print("Hermes skill validation")
    print("  skills: " + str(len(known_names)))
    print("  yaml parser: " + ("PyYAML" if HAVE_YAML else "built-in fallback"))
    for warning in warnings:
        print("  WARN: " + warning)
    for error in errors:
        print("  ERROR: " + error)

    if errors:
        print("FAIL: " + str(len(errors)) + " error(s)")
        return 1
    print("PASS: " + str(len(known_names)) + " skills valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
