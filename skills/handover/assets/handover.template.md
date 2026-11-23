<!-- handover v1 -->
- Written: 2026-01-31T14:05:00Z
- Repository: path or URL
- Branch: name
- HEAD: full commit SHA
- Working tree: clean | dirty (N files)

<!--
Rules for this note. Keep the header above in that shape, because the checker reads it.

Header
- Written is UTC. Take it from the clock (date -u +%Y-%m-%dT%H:%M:%SZ), because a typed time drifts.
- Branch is the branch that is checked out. Write (detached) when none is.
- HEAD is the full id printed by: git rev-parse HEAD
- Working tree is the word clean, or dirty (N files). N is the number of lines printed by
  git status --porcelain --untracked-files=all, not counting this note.

Body
- Reference commits, issues and files by id or path. Do not copy their content,
  because a copy goes stale and the original does not.
- No secrets, tokens or personal data, because a note gets passed around.
- Real names for things. No private shorthand, because the reader was not there.
- A path in backticks is a claim that the file exists. Write it from the repository root.
  For a file that is not made yet or was removed, write (planned) or (gone) straight after it.
- Write a commit as its id in backticks, and a branch as "branch: NAME", so the checker finds them.
- Delete these comments or leave them. The checker ignores text inside comments.
-->

# Handover: short title of the work

## Goal

<!-- One or two sentences: what the person wants at the end. -->

## State right now

<!-- Each line is a fact, then the command that showed it. Only facts you saw in this session. -->

- Fact. Shown by: command

## Done and verified

<!-- What was done, and how it was checked. Only things you watched pass. -->

- What. Checked by: command or observation

## Done but not verified

<!-- What was done, and what would verify it. The next session treats these as not done. -->

- What. To verify: command

## In flight

<!-- Anything running or half-finished, and how to check on it. Write "Nothing" if nothing is. -->

- What. To check on it: command

## Next steps

<!-- In order. The first one is small and concrete, so the next session can start without deciding anything. -->

1. First step
2. Second step

## Traps

<!-- Things that cost time, and wrong turns already taken, so nobody takes them twice. -->

- Trap, and what to do instead

## Decisions and why

<!-- So the next session does not reopen them. One line each: the decision, then the reason. -->

- Decision, because reason

## Questions for the person

<!-- Things only the person can answer. Write "None" if there are none. -->

- Question

## How to check this note

<!-- The exact command, with real paths, so the reader can run it before trusting anything above. -->

```sh
python3 path/to/handover/scripts/handover_check.py path/to/this-note.md --repo path/to/repository
```

The checker confirms files, commits, branches and tree state. It cannot confirm
behaviour, such as passing tests. Rerun those before you rely on them.
