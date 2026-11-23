---
name: shrink-model
description: Builds a smaller, independently runnable model out of an open-weight parent checkpoint's own weights, through structural pruning followed by recovery training with distillation from the parent. Works for dense and mixture-of-experts parents, including the case where the model running this skill is asked to make a smaller version of itself. Use this skill whenever the user asks to shrink, compress, prune, slim, or distill a model, to build a smaller child or descendant of a checkpoint, to hit a total or active parameter budget, or to count the parameters in a checkpoint, even if they only name a target size. It also names the requests it does not serve, such as quantization alone or distillation into an unrelated student model, and says what to offer in their place.
---

# Shrink a model into a smaller descendant

This skill takes a parent checkpoint and builds a smaller child from the parent's own tensors, then recovers quality by training. Treat it as an experiment. The child will lose capability, and the job is to measure how much and report it honestly.

Lineage is the point. Distilling the parent into an unrelated small model is a useful comparison. It does not count as the deliverable.

## Check that the request fits

This skill does one job. It removes structure from a parent and retrains what is left. The words "compress" and "distill" also cover jobs it does not do, so settle which job this is before phase 0.

| The person wants | What to do |
|---|---|
| Fewer parameters, built from the parent's own weights | Continue with this skill |
| A parameter count or an inventory, and nothing else | Run phases 0 and 1, report the counts, and stop |
| A smaller file or less memory, with the architecture and parameter count unchanged | That is quantization alone. Say that this skill does not serve it, and offer to quantize the checkpoint with the target runtime's own tool. Phase 9 quantizes, but only as the last step after pruning and recovery |
| The parent's behavior taught to a different small model | That is distillation into an unrelated student. Say that this skill does not build it, because the student does not descend from the parent's weights. Offer an ordinary distillation run, and offer this skill if a descendant would also serve |

If the request could be read more than one way, ask one question: must the result be built from the parent's own weights, with fewer parameters? Start the pipeline only on a yes.

## Inputs

Copy `assets/inputs.template.yaml` into the run folder and fill it in. Discover what you can from the machine and the checkpoint. Ask the person only for what you can neither discover nor default.

| Input | Default |
|---|---|
| Parent | A local path, or a repository id with a pinned revision. Required |
| Target | A cap on total parameters, a cap on active parameters for MoE, or a ratio. Required, and smaller than the parent |
| Priority capabilities | Coding, reasoning, instruction following |
| Deployment profile | Batch 1 and 4,096 total context tokens, generation included |
| Budgets | The limits under "Default bounds" |
| Paid or external compute | Off |
| Data and logs | Stay on this machine |

## When the parent is the model running this skill

Work out which case you are in before anything else.

1. **You are a hosted model.** You have no access to your own weights, so you cannot shrink yourself. Say so in one sentence and offer to shrink an open-weight checkpoint the person names.
2. **You run locally from weights on this machine.** The parent is that checkpoint. Find its path from the server configuration, or ask. Treat the files as read-only.
3. **The parent is some other checkpoint.** This is the ordinary case.

Case 2 has a resource conflict. The process serving you already holds the accelerator memory, so a second copy of the parent for calibration or teaching may not fit. Two ways through:

- Use your own serving endpoint as the teacher, if it returns token probabilities.
- Script the heavy phases to run unattended, stop the server, run them, and restart it. Get the person's agreement first, because this stops the model they are talking to.

## The pipeline

Follow this order. Each phase ends with evidence on disk, and four gates stop the run.

| Phase | What happens | Ends with | Read |
|---|---|---|---|
| 0. Doctor | Record tools and hardware for each machine role. Create the run folder | Environment report | `references/inspect.md` |
| 1. Inspect | Identity, license, architecture, and a parameter inventory, from metadata only | Inventory and counts | `references/inspect.md` |
| 2. Feasibility | Ratio, storage, memory, whether the parent can run, recovery budget | **Gate A** | `references/inspect.md` |
| 3. Route | Branch on architecture. Check maintained tools. Search child shapes | Two or three candidates with exact counts | `references/dense.md` or `references/moe.md` |
| 4. Fixtures | Run every transformation on a tiny model. Run the identity conversion | Passing tests | same |
| 5. Prune | Build the child and its manifest. Remove tensors physically | **Gate B** | same |
| 6. Pilot | One bounded recovery run | **Gate C** | `references/recover.md`, `references/evaluate.md` |
| 7. Recover | Full training inside the approved budget | Best checkpoint | `references/recover.md` |
| 8. Evaluate | Sealed test, baselines, resource measurements | **Gate D** | `references/evaluate.md` |
| 9. Export | Quantize, package, launch with nothing but the child | Independent launch verified | `references/export.md` |

Read a reference file when you reach its phase. Phase 3 reads one of `dense.md` or `moe.md`, chosen from the inventory. A hybrid parent reads both.

### Gate A: go or no-go

Before any pruning, write `reports/feasibility.md` and stop. The memo holds:

- the parent's identity, and whether its license permits the derivative the person intends
- measured parameter counts and the counting convention
- the compression ratio on total, and on active for MoE
- for MoE, the always-active floor set against the active budget
- storage and memory needed in each phase, set against what each machine has
- whether the parent can be run here, and what that does to calibration and teaching
- a recovery budget estimate and its basis, including the nearest published precedent
- the candidate child shapes, your recommendation, and the tier you expect to reach

Then wait. A ratio beyond precedent is the person's risk to accept, with the numbers in front of them. If the license forbids the intended use, or the facts rule the job out, the memo says so and the run ends at Tier 0.

### Gate B: the child is structurally sound

All of these hold:

- `scripts/check_manifest.py` passes, and its last line reports zero inherited values not compared. If a flag such as `--allow-dtype-change` or `--sample-per-index` left some uncompared, the gate report names the flag, gives the count from that line, and says how those values were verified
- the child's measured count equals the estimator's prediction. Use `scripts/budget.py` where its model is exact for the chosen shape, and an analytic estimator otherwise. See `references/moe.md`
- the identity conversion reproduces parent outputs within a stated tolerance
- a forward and a backward pass are finite
- routing indices and tensor shapes are valid
- saving and reloading preserves outputs

### Gate C: the pilot earns a full run

Freeze `gates.yaml` and record its hash before the pilot starts. The pilot passes when it clears the pilot thresholds in that file with no non-finite loss and healthy routing. Then stop again. Report the measured throughput and the projected cost of the full run, and wait for a budget.

### Gate D: the child is worth having

Run the sealed test once, on a frozen candidate. The child passes when it meets the retention thresholds against the parent and also matches or beats the strongest existing open model of similar size, measured in the same harness. If it loses to that baseline, say so plainly. The result is then a valid descendant that nobody should deploy in place of the baseline.

## Default bounds

Without a stated budget, stay inside these. Anything larger needs a number from the person, so state the size when you ask.

| Action | Limit |
|---|---|
| Downloads | 20 GB in total |
| New files on disk | 50 GB |
| Smoke test | 50 optimizer steps and 15 minutes |
| Estimator search | 100,000 candidates |
| Pilot, after a go at Gate A | 2,000 optimizer steps, 4 hours, 100 GB |
| Number of pilots | 3 |
| Full recovery | Only with a budget given at Gate C |

## Tiers of done

| Tier | Means |
|---|---|
| 0. Plan | Inventory, counts, feasibility memo, candidates |
| 1. Structural child | Gate B passed. It loads and runs. Quality is unknown or poor |
| 2. Recovered child | Full recovery finished and the development thresholds are met |
| 3. Validated child | Gate D passed, exported, and running alone |

Report the tier you reached and label nothing above it. A run that ends at Tier 0 with a precise blocker is a correct outcome.

## Scripts

All three use only the Python 3 standard library. Two read safetensors headers alone, and the third streams tensor data in bounded blocks, so all of them work on checkpoints larger than memory. Run each with `python3` and `--help` for its options. Each exits nonzero on failure.

- `scripts/count_params.py PATH --emit-inventory inventory.json` builds the component inventory. It reports each layer stack, lists by name the tensors it excluded as quantization data, and flags tensors whose stored size may not equal their parameter count.
- `scripts/budget.py count`, `search`, and `feasibility` compute total, active, and floor, search child shapes inside both budgets, and do the storage arithmetic. The expert-width scale in `search` is a linear model, and `--help` says when it is exact.
- `scripts/check_manifest.py --manifest M --parent P --child C` confirms that every child tensor is accounted for, and compares every inherited value with the parent. Its last line states how many values were compared and how many were not.

For a checkpoint in another format, convert it or write an equivalent header reader first. Loading a pickle checkpoint executes code, so do that only for files from a source the person trusts.

## Rules that hold in every phase

1. Label every number as an estimate, an implementation, an executed run, or a validated result.
2. Report measured counts. Never round an over-budget model down to its label.
3. Remove tensors physically. A mask or a lower top-k does not make a smaller stored model.
4. Never slice packed or block-quantized weights. Dequantize through a supported path first.
5. Parent files are read-only.
6. The sealed test split is used once, on a frozen candidate, and for nothing else.
7. The parent is never the only judge of the child, and its own outputs are checked before they become training data.
8. No paid compute, no external service, and no download or disk use past the bounds without a yes.
9. Run generated code in a sandbox, and keep secrets out of logs.
10. When blocked, say exactly what blocked you and finish what can still run. Never present fine-tuning of some other model as the result.

## Run folder

Create it in the current project directory, or wherever the person says. Never put it inside the parent checkpoint's folder or this skill's folder.

```
runs/<name>/
  inputs.yaml
  gates.yaml        frozen before the pilot
  inventory/        count_params output, inventory.json, copies of configs
  reports/          environment.md, feasibility.md, completion.md
  src/              pipeline code and tests
  data/             manifests, hashes, splits
  checkpoints/
  export/
  logs/
```

Finish every run, at whatever tier, by filling in `assets/completion.template.md` as `reports/completion.md`.

## Reference files

| File | Covers |
|---|---|
| `references/inspect.md` | Phases 0 to 2: environment, identity, license, counting conventions, memory arithmetic, the feasibility memo |
| `references/dense.md` | Phases 3 to 5 for dense parents: depth, width, heads, consistency rules, fixtures |
| `references/moe.md` | Phases 3 to 5 for MoE parents: the floor, the search, expert utility, slimming, removal, router repair |
| `references/recover.md` | Phases 6 and 7: teacher options, data, losses, what to train, monitoring |
| `references/evaluate.md` | Phase 8: splits, frozen gates, baselines, measurements |
| `references/export.md` | Phase 9: packaging, quantization, independent launch, naming |
