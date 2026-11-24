# Hermes adaptation

This fork adapts [ashhart/skills](https://github.com/ashhart/skills) for Hermes Agent. The skill content stays upstream's; the packaging, install path, and hook integration are Hermes-native.

Fork: https://github.com/MaverickKB/skills

## What changed

| Area | Upstream | This fork |
| --- | --- | --- |
| Layout | 18 TensorFold skills under `skills/tensorfold/<name>/` | All 40 skills flat under `skills/<name>/`, so a Hermes tap (one level under `skills/`) discovers every skill |
| Frontmatter | `name`, `description`, `metadata.version` | Adds `metadata.hermes.tags` and `metadata.hermes.related_skills` for Hermes indexing and skill routing |
| Grouping | README sections | `skills.sh.json` groupings, so Hermes shows category pills for the tap |
| Install | Skills CLI (`npx skills add`) | Hermes tap and `hermes skills install` |
| Mythos gate hooks | Claude Code `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop` payloads | Hermes `pre_llm_call`, `pre_tool_call`, `post_tool_call`, `pre_verify` payloads |
| Agent references | `CLAUDE.md`, Claude tool names | `AGENTS.md` and `SOUL.md`, Hermes tool names |

## Install

```bash
hermes skills tap add MaverickKB/skills
hermes skills install MaverickKB/skills/skills/repo-map
```

Direct install without a tap:

```bash
hermes skills install MaverickKB/skills/skills/repo-map
```

### Hub scan

`hermes skills install` scans every skill before installing. The scanner rates `mythos-mode` dangerous because `scripts/gate.py` and its tests contain the destructive-command and prompt-injection patterns the gate is built to detect. The scanner cannot tell a defensive regex from a payload, and the hub refuses a dangerous verdict even with `--force`.

| Verdict | Skills | Hub install |
| --- | --- | --- |
| safe | most of the collection | yes |
| caution | break-it, breakthrough-architect, diagnosing-bugs, distill-foundry, handover, prove-it, ship-check, slopchop, vram-alchemist | yes, with `--force` |
| dangerous | mythos-mode | no; copy manually |

Install `mythos-mode` by copying its folder into the profile skills directory:

```bash
cp -R skills/mythos-mode ~/.hermes/profiles/<profile>/skills/
```

The installed copy loads through `skill_view` with its Hermes metadata, and the gate runs from the profile path.

List the tap and installed skills:

```bash
hermes skills tap list
hermes skills list
```

Manual install: copy `skills/<name>/` into the active profile's skills directory. Use `hermes config path` to find the profile home; skills live beside it under `skills/`.

## Tool mapping

The skills describe work in agent-neutral terms, but the Mythos gate reads tool names. Hermes names map like this:

| Upstream / Claude Code | Hermes |
| --- | --- |
| `Bash` | `terminal` |
| `Edit`, `Write`, `MultiEdit` | `patch`, `write_file` |
| `Read` | `read_file` |
| `Task` / subagent | `delegate_task` |
| `SessionStart` hook | `pre_llm_call` |
| `PreToolUse` hook | `pre_tool_call` |
| `PostToolUse` hook | `post_tool_call` |
| `Stop` hook | `pre_verify` |

## Mythos gate hooks

Add to the active profile `config.yaml`:

```yaml
hooks:
  pre_llm_call:
    - command: "python3 ~/.hermes/skills/mythos-mode/scripts/gate.py --hook session --client hermes"
  pre_tool_call:
    - matcher: "terminal"
      command: "python3 ~/.hermes/skills/mythos-mode/scripts/gate.py --hook pre-tool --client hermes"
      fail_closed: true
  post_tool_call:
    - matcher: "terminal|patch|write_file"
      command: "python3 ~/.hermes/skills/mythos-mode/scripts/gate.py --hook post-tool --client hermes"
  pre_verify:
    - command: "python3 ~/.hermes/skills/mythos-mode/scripts/gate.py --hook pre-verify --client hermes"
```

What each hook does:

- `pre_llm_call` injects the laws once, on the first turn.
- `pre_tool_call` blocks catastrophic deletes and asks for approval on irreversible commands. `fail_closed: true` keeps a crashed gate from silently allowing the command.
- `post_tool_call` tracks edit-and-fail cycles per session and alerts on instruction-like text in tool output.
- `pre_verify` sends an unsupported reply back with a `continue` directive.

Set `MYTHOS_GATE=warn` to report without blocking, or `MYTHOS_GATE=off` to disable the hooks. The CLI reply check works without hooks:

```bash
python3 skills/mythos-mode/scripts/gate.py REPLY.md --work
```

The gate reads Hermes hook payloads from stdin. Hermes sends `hook_event_name`, `tool_name`, `tool_input`, `session_id`, `cwd`, `profile`, and `extra`; event-specific values such as `final_response`, `changed_paths`, `attempt`, and `duration_ms` arrive in `extra`. The gate writes Hermes-native directives to stdout: `{"context": ...}`, `{"decision": "block", "reason": ...}`, and `{"action": "continue", "message": ...}`.

## Validate the fork

```bash
python3 scripts/validate_hermes.py
```

The validator checks that every skill folder has one `SKILL.md`, that `name` matches the folder, that frontmatter parses, that `metadata.hermes.tags` exists, and that `skills.sh.json` lists every skill. It exits non-zero on a failure.

## Upstream sync

```bash
git fetch upstream
git merge upstream/main
```

Expect conflicts where upstream edits a TensorFold skill's relative links: upstream keeps `skills/tensorfold/<name>/`, this fork keeps `skills/<name>/`. Resolve by keeping the flat path and re-running the validator.

## License

Upstream ships no license file. This fork preserves upstream content and adds Hermes adaptation files; treat the upstream repository's terms as governing the skill content.
