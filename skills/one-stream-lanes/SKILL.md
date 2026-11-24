---
name: one-stream-lanes
description: Closes the throughput gap between one serial LLM request and N concurrent requests on the same silicon, token-for-token exact. Frames decode and prefill in lanes (rows a pass, landed a pass, passes a second, and single-stream share of N-stream total), prices dense vs MoE row cost against calibrated proposer landing odds with `lane_math.py`, and designs exact GPU-side acceptance, overlapped late drafting, parallel grammar windows and Recall copies without declaring bandwidth ceilings. Use when making single-request decode or prefill approach multi-stream throughput, tuning draft window widths or trees, or asking why one stream leaves the GPU idle.
metadata:
  version: "2.0.0"
  hermes:
    tags: [tensorfold, decode, throughput, batching]
    related_skills: [serving-concurrency, speedup-referee]
---

# One stream, all the lanes

Every time N requests run at once, the chip shows what it can actually deliver. One serial request usually gets a fraction of that total and leaves most of the machine idle. The engineering job is to give N-stream throughput to one request, with every token identical to plain serial decoding.

Know the exact split between dense and routed-expert models before designing a window:

- **Dense projections** read weights once for the whole window, so 8 to 32 rows cost close to 1 row until compute saturates.
- **Routed-expert MoE, per-stream KV reads and recurrent state** can pull fresh bytes for every extra row. On MoE models, adding low-odds tree rows activates extra experts and drops `passes a second` fast. Always price marginal row cost on your exact cell rather than assuming wide windows are free.

## Think and report in lanes

Measure every stream in three numbers:

- **Rows a pass.** How many window slots are dispatched in one forward step.
- **Landed a pass.** How many of those slots become kept tokens.
- **Passes a second.** Combined rate is `tok/s = landed a pass * passes a second`.

The scoreboard number is **share**: one stream's `tok/s` divided by the total `tok/s` of N concurrent streams (each with its own prompt) on the same build, chip, checkpoint and sampling settings. Raise share by raising one stream. A change that lowers the N-stream total does not count.

Use milliseconds, bytes and bandwidth in working notes to pick candidates. Never conclude that a target is out of reach because of a bandwidth model: bound a single change before you build it, never bound the goal.

## Ground rules

1. **Exact.** Drafted equals `"draft": false` token for token. Resumed equals fresh. Concurrent equals solo. Output never depends on window width, draft depth, chunk size, stream order or timing. Compare full 64-hex token SHA-256 hashes, greedy and seeded sampled.
2. **One stream.** Start an experiment only if it can raise landed a pass, passes a second, or prompt rows a pass for one request.
3. **One lane core.** Extend the unified lane scheduler and proposer interface. Do not add a second serial fallback scheduler, a per-family fork, or a drafts-off workaround.
4. **Clean room.** Write from your own engine and the model's definition. Do not copy or translate another engine's source.
5. **Served or it did not happen.** A kernel timed alone proves possibility. Only a served request on the default path (with default sampling and limits) proves a win.
6. **No regressions.** Any served cell slower by more than the base-against-base noise floor in every round holds the change.

## Closed-loop lane architecture

Push single-stream decode through four coupled subsystems rather than ad-hoc heuristics:

1. **Unified proposer interface and landing calibration.** Every source (context copy, Recall cross-session graft, client edit prediction, grammar-forced span, n-gram index, multi-token head) feeds one window planner with calibrated per-position landing odds for the active workload (prose, code, edits, tool calls).
2. **Row-cost planner.** Prices each extra row as a function of window width, context length and dense-vs-MoE expert fan-out, and stops widening the moment marginal expected landed tokens fall below the break-even pass-time cost. Check whether the planner's row price matches the real measured width curve.
3. **Zero-stall pass overlap.** Move greedy and seeded-sampled acceptance onto the GPU so the host does not stall between passes; draft the next window (`late_draft`) beside the in-flight verify pass; run grammar masking in parallel (`grammar_parallel`) instead of serially on the critical path; reuse work instead of discarding in-flight passes.
4. **Wide kernels that preserve narrow prose.** Any wide-window kernel (8 to 32 rows) must read the live row count on the GPU and never slow narrow 1-to-4-row prose windows.

## The loop

1. **Measure the baseline table.** Measure 1 stream (default and serial/no-draft) and 2, 4, 8, 16, 32 streams with a distinct prompt per stream across prose, code, copy-heavy edits, tool calls, thinking and cold prompts (2k to 64k). Never retreat from novel prose to easy copy cells.
2. **Draw two curves and locate the bottleneck.**
   - **Landing curve:** `landed a pass` vs `rows a pass`. If it goes flat as width grows, proposals are the limit.
   - **Width curve:** `passes a second` vs `rows a pass` (1-stream vs N-stream). If 1-stream drops faster than N-stream, row cost at width is the limit.
   - If both look healthy and share is still low, time is leaking **between passes** (host round trips, synchronous acceptance, serialized proposers, idle CPU/NPU units).
3. **Pick one mechanism from the row-source map below**, write the expected lane delta before coding, build behind a flag in the served path, verify exact token hashes across forced widths and depths, measure with noise floor + mirrored order (`B, C, C, B`), and hand proven wins to `speedup-referee` in a fresh session.

## Map of exact row sources (certain to hedged)

- **Certain (always land):** prompt tokens; chat-template fixed tokens; tool-call wrappers; every position where a grammar or JSON schema leaves one legal token; cached repeats for identical request and seed.
- **High odds:** copies from prompt context, earlier reply lines, or earlier sessions (Recall grafts/copy indexes); client-supplied edit predictions; structural tokens (closing brackets, indentation, repeated identifiers); native multi-token heads or companion drafters; n-gram/phrase tables.
- **Hedged:** draft trees with sibling branches where row cost is cheap (dense layers; avoid bushy prose trees on routed-expert MoE unless expert reuse is high); multi-proposer budgets weighted by measured landing odds.
- **Prompt chunking and KV.** Hand deep cold prefill, equal-work prompt comparisons and KV residency to `prefill-and-kv`.

## Script: `scripts/lane_math.py`

Stdlib Python 3.9+ analyzer for lane share, curve diagnosis, row-cost width planning and receipt comparison. Run with `--help` for all options.

```bash
python3 scripts/lane_math.py share --single-tps 85 --multi-tps 640 --streams 16 --rows 8 --landed 3.2 --passes-per-sec 26.5
python3 scripts/lane_math.py diagnose sweep.csv
python3 scripts/lane_math.py plan-width width_costs.csv --accept-probs 0.85,0.65,0.45,0.25
python3 scripts/lane_math.py compare base_lanes.json cand_lanes.json
```

`diagnose` classifies a width sweep as `PROPOSALS_FLAT`, `ROW_COST_HEAVY`, or `BETWEEN_PASS_OVERHEAD`. `plan-width` computes break-even landed tokens at each width and flags MoE row-cost cliffs where widening drops `tok/s`. `compare` rejects `token_sha256` mismatches (`REJECTED_EXACTNESS`) and N-stream drops (`REJECTED_MULTI_REGRESSION`).

Every compared cell needs a 64-character hexadecimal `token_sha256` string in both receipts. A missing or malformed digest is an input error (exit 2). Different valid digests fail the comparison (exit 1).

Both receipts must supply finite, positive `single_tps` and `multi_tps` measurements for every cell. Invalid rates or regression tolerances exit 2 before a performance verdict.
