---
name: tensorfold-speed-hunt
description: Orchestrates the 7-gate closed-loop TensorFold engine performance workflow locally across Metal, CUDA and Zig. Chains `critical-path-xray`, `optimization-ledger`, specialist fixes (`kernel-contract`, `graph-and-dispatch-surgeon`, `one-stream-lanes`, `prefill-and-kv`, `serving-concurrency`, `drafter-forge`, `moe-xray`), `sass-and-air-diff`, `output-parity` and fresh-session `speedup-referee` inside a tamper-checked state machine (`loop_driver.py`) that hashes every receipt and blocks landing until all gates pass. Use for an end-to-end local TensorFold speedup hunt across decode, prefill, TTFT or concurrency.
metadata:
  version: "1.0.0"
---

# TensorFold speed hunt

Isolated microbenchmarks, unlogged dead ends, silent untouched-kernel spills and skipped parity checks burn weeks of local engine development. `tensorfold-speed-hunt` combines every TensorFold specialist into one executable 7-gate closed loop driven by `scripts/loop_driver.py` so every iteration finds the true #1 bottleneck, proves zero regressions and compounds verified speedups across backends and model families.

## Ground rules

1. **Follow all seven gates in order.** `loop_driver.py` refuses to advance a stage until every prior stage passes and SHA-256 hashes every attached JSON receipt on disk.
2. **Store notes beside the checkout.** Keep `run.json`, traces, SASS dumps and receipts in a persistent notes folder outside the repository tree and outside `/tmp`.
3. **Exact output, clean room, inside authority.** Preserve 64-hex `token_sha256` parity across greedy, seeded, cached, drafted and concurrent paths, write clean-room engine code, work on an isolated branch and never push, merge or touch shared hardware without maintainer approval.
4. **Reprofile after every win.** Once `check-land` passes, start the next loop from a fresh `critical-path-xray` trace on the faster binary and work down the `OWED_PORT` queue.

## The 7-gate closed loop

Initialize a run once per optimization candidate:

```bash
python3 scripts/loop_driver.py init \
  --run ../tf-notes/hunt-01/run.json \
  --commit "$(git rev-parse --short HEAD)" \
  --hardware rtx5090 --backend cuda --family qwen3_5 \
  --target-cell decode_solo_code --baseline-tps 82.4
```

Then clear each gate in order (`status` always prints the next exact command):

1. **`xray` (`critical-path-xray`):** Parse the real served trace, confirm GPU vs host vs idle wall split and Amdahl ceiling, and attach `notes/xray.json` (`loop_driver.py` routes you automatically to `graph-and-dispatch-surgeon`, `one-stream-lanes`, or `kernel-contract`).
2. **`preflight` (`optimization-ledger`):** Check `optimization-ledger.jsonl` and built-in dead-end archetypes so you never repeat a recorded loss without a changed assumption; attach `notes/preflight.json`.
3. **`build` (routed specialist):** Implement on an isolated branch using the routed specialist (`kernel-contract`, `graph-and-dispatch-surgeon`, `one-stream-lanes`, `prefill-and-kv`, `serving-concurrency`, `drafter-forge` or `moe-xray`) and record `--branch`, `--mechanism` and `--cand-binary-sha256`.
4. **`binary_gate` (`sass-and-air-diff`):** Diff compiled `ptxas`/`cuobjdump` SASS or Metal AIR snapshots to prove untouched kernels stayed identical with zero new spills; attach `notes/binary_gate.json` (or `--not-applicable --notes ...` only if no GPU kernels/headers changed).
5. **`parity_gate` (`output-parity`):** Prove identical replies and 64-hex token hashes across greedy, seeded, cached, drafted and concurrent cells; attach `notes/parity.json`.
6. **`bench_gate` (`optimization-ledger score-ab`):** Score mirrored `B, C, C, B` rounds with base-vs-base noise floor, thermal/clock throttling drops and zero regressed served cells; attach `notes/ab_verdict.json`.
7. **`referee` (`speedup-referee`):** Open a fresh session to review the claim independently, record the outcome in `optimization-ledger`, attach `--verdict CONFIRMED --referee-session <id> --owed-ports ...`, and run `check-land`:

```bash
python3 scripts/loop_driver.py check-land --run ../tf-notes/hunt-01/run.json
```

`check-land` re-hashes every attached receipt on disk, exits `0` (`SPEED HUNT: READY_TO_LAND`) with a concise maintainer closing summary, or exits `1` (`SPEED HUNT: BLOCKED`) naming every missing, failed or tampered gate.

## Routing and cross-layer stacks

Read `references/loop-routing.md` for the full evidence-to-specialist routing table, multiplying cross-layer combinations, revert/reprofile rules and maintainer closing format.
