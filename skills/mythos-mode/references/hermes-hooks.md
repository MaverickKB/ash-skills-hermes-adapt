# Hermes hooks for the Mythos gate

Use this when installing Mythos Mode in Hermes and wiring its reply gate into shell hooks.

## Hook config

Add to the active profile `config.yaml` under `hooks:`. Use the absolute path to this skill's `scripts/gate.py`; hooks run without a shell, so `~` is not reliable.

```yaml
hooks:
  pre_llm_call:
    - command: "python3 <skill-dir>/scripts/gate.py --hook session --client hermes"
      timeout: 20
  pre_tool_call:
    - matcher: "terminal"
      command: "python3 <skill-dir>/scripts/gate.py --hook pre-tool --client hermes"
      timeout: 10
      fail_closed: true
  post_tool_call:
    - matcher: "terminal|patch|write_file"
      command: "python3 <skill-dir>/scripts/gate.py --hook post-tool --client hermes"
      timeout: 10
  pre_verify:
    - command: "python3 <skill-dir>/scripts/gate.py --hook pre-verify --client hermes"
      timeout: 20
```

- `pre_llm_call` injects the laws on the first turn and queued tool advice on later turns.
- `pre_tool_call` blocks catastrophic commands and escalates irreversible ones. Set `fail_closed: true` when the profile's `approvals.mode` is off; a crashed gate must not allow a destructive command. Keep a config backup and an explicit removal path.
- `post_tool_call` tracks edit-and-fail cycles and injection text. Its return is ignored; advice is queued in a per-session state file and delivered by the next `pre_llm_call`.
- `pre_verify` checks the draft reply once, gated on `attempt`. It fires only when the agent edited code.

## Tool mapping

| Claude Code | Hermes |
| --- | --- |
| Bash | terminal |
| Edit, Write, MultiEdit | patch, write_file |
| Read | read_file |
| Task / subagent | delegate_task |

## Wire and verify

1. Install the skill. If the hub refuses it as dangerous because the gate contains destructive-command patterns, copy the folder into the profile skills directory.
2. Back up the profile `config.yaml`.
3. Add the hook entries. `patch` and `write_file` refuse `~/.hermes` config paths, so use a script that writes the block and re-parses YAML; restore the backup on failure.
4. Run `hermes -p <profile> hooks list` and confirm all four entries are allowed. With `hooks_auto_accept: true` they run without approval; run `hermes -p <profile> --accept-hooks chat -q ...` once to record allowlist entries.
5. Test each channel with `hermes -p <profile> hooks test <event> --for-tool <tool> --payload-file <json>`. Use a payload file for the destructive-command and unsupported-reply cases; never run a real destructive command to test the gate. The file is merged into the hook kwargs, and Hermes builds `tool_input` only from `args`, so a `pre_tool_call` payload must carry the command as `{"tool_name": "terminal", "args": {"command": "rm -rf /"}}`. A payload that puts the command under `tool_input` fires the gate but produces no directive and prints `parsed: none`. `pre_verify` fields such as `final_response`, `changed_paths` and `attempt` need no wrapper; they land under `extra`.
6. Run a live turn that uses `terminal`. For first-turn laws injection, check `messages.api_content` in the profile `state.db`; a session that started before wiring has `is_first_turn` false and will not receive the laws.

## Limits

- `pre_tool_call` matches `terminal` only. `execute_code` and `browser_exec` are not inspected.
- Post-tool advice arrives on the next turn, not in the same tool result.
- `pre_verify` is bounded by `agent.max_verify_nudges` and one-shot via `attempt`. It also fires only after a code edit, so a false "it works" claim with no edit in that turn is not gated.
- A registration or auto-approve line in `agent.log` is not proof the gate ran. After wiring, confirm a later tool call actually invoked `gate.py`. A desktop session that was already open when the hooks were added may keep running without them until that process restarts.
