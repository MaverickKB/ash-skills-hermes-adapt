# Inspecting the parent and judging feasibility

Phases 0 to 2. Everything here works from metadata. Nothing in these phases needs the parent's weights in memory.

## Contents

- Phase 0: doctor
- Phase 1: identity and license
- Reading the architecture without loading it
- Counting conventions
- Memory and storage arithmetic
- Can the parent be run?
- The feasibility memo

## Phase 0: doctor

Record the facts for each machine role separately: running the parent, training the child, converting, and deploying. One machine can fill several roles at different times.

- CPU, RAM, and free disk
- accelerator model and count, and memory on each device
- driver, framework, and runtime versions
- which ways of splitting a model across devices the framework supports

Several GPUs are not one pool of memory. A tensor has to fit on one device, and sharding has its own overhead.

Pin package versions into a lock file, create the run folder, and write `reports/environment.md`.

## Phase 1: identity and license

Record the checkpoint path or repository id with its exact revision, the shard list with sizes, the config, the tokenizer, the chat template, and the runtime versions.

Read the license text itself. The weights and the source code repository can carry different licenses. Answer three questions:

1. Are derivative models allowed?
2. Is redistribution allowed, and under what name and notices?
3. Are there conditions on who may use it, such as revenue limits or a required review?

If the person's intended use is not permitted, that goes at the top of the feasibility memo and the run ends there. Keep the notices for the child package.

## Reading the architecture without loading it

A safetensors header is small and holds every tensor's name, dtype, and shape.

- For local files, `scripts/count_params.py` reads the headers.
- For a repository on the Hugging Face hub, `huggingface_hub.get_safetensors_metadata` fetches headers without downloading the weights. Check that the installed version has it.
- Read `config.json` and the model's own code at the pinned revision.

Do not download or load hundreds of gigabytes to learn the architecture.

Decide the class first: dense, mixture of experts, or hybrid. Then find each of these in the code:

- the attention design: standard multi-head, grouped-query, latent or low-rank projections, sparse attention with an indexer
- the MLP design and which layers are dense
- norms, positional scheme, and whether embeddings and head are tied
- for MoE: routed and shared experts, how the router scores and normalizes, any correction biases, top-k
- auxiliary modules, such as multi-token prediction heads, and whether inference uses them

Then build the inventory:

```
python3 scripts/count_params.py PARENT --json inventory/report.json --emit-inventory inventory/inventory.json
```

The component names come from regex rules on tensor names. Check the largest groups against the code, and pass `--rules` to fix anything misfiled. Nothing large should be left unclassified.

Read the whole report before trusting the inventory. Four parts of it need a decision:

- **Layer stacks.** Layers are grouped by the name text before `layers.N.`, so a vision tower or an encoder is its own stack and is never merged into the trunk. The stack with the most parameters becomes the trunk. Every other stack goes into `always.other_stacks` and counts as always active. Pass `--trunk PREFIX` if the trunk is a different stack.
- **Tensors excluded as quantization data.** These are listed by name and left out of the parameter estimate. If a real parameter is on the list, such as a norm weight with "scale" in its name, fix the rule with `--rules`.
- **Tied embeddings.** When the embeddings and the output head are stored with the same shape, the report warns that they may be tied. Check the config. If they are tied, subtract one copy from `always.output_head`.
- **Auxiliary modules.** When the checkpoint holds any, the inventory's note says so. Decide from the code whether inference uses them. If it does not, add `aux_modules` to `convention.inactive`.

The script refuses to write an inventory that does not add up to its own parameter estimate. That happens when experts in one layer differ in size, or when expert tensors cannot be split per expert. Fix the grouping with `--rules`, or write that layer's entry by hand.

Then fill in `top_k` for each MoE layer from the config, and count:

```
python3 scripts/budget.py count inventory/inventory.json
```

The total it prints must equal the parameter estimate from the first command. The count stops with an error while any `top_k` is still empty.

## Counting conventions

State the convention in every report that gives a count. The default:

- **Total** is every unique parameter that inference needs, counted once. Tied weights count once. Quantization scales are not parameters. A module that inference never uses is listed separately, and it is excluded from the total only if it is physically absent from the child package.
- **Active per token**, for MoE, is everything one token's forward pass multiplies through: all non-expert weights, the router, every shared expert, and top-k routed experts in each MoE layer.
- **Embeddings and output head.** Conventions differ here, so report active both with and without them.
- **Variable routing.** If the number of experts selected varies, report the mean and the worst case.

Three cautions:

- Never infer a parameter count from file size. In packed and block-quantized formats the stored element count also differs from the parameter count. `count_params.py` flags integer tensors, and lists the tensors it excluded as quantization data, for this reason.
- An active count is not a latency or FLOP figure. A model with 6B active parameters does not run like a dense 6B model.
- An active count is not a memory figure. Every stored expert stays loaded.

## Memory and storage arithmetic

Raw weight bytes are parameter count times bits per weight, divided by eight.

```
python3 scripts/budget.py feasibility --parent-total 753B --parent-bits 8 --child-total 35B --disk .
```

`--disk` takes any path on the volume that will hold the work.

Estimate each of these, then replace the estimate with a measurement from a small probe before projecting anything long:

| Quantity | What drives it |
|---|---|
| Running the parent | Weights, plus activations at the calibration batch size |
| Training the child | Weights, gradients, optimizer state, activations |
| Cached teacher outputs | Tokens times values kept per token. Full distributions over the vocabulary are very large |
| Surgery peak | Parent and child tensors alive together. Stream tensor by tensor to cut it |
| Dequantizing the parent | The trainable-precision copy, on disk and in memory |
| Deployment | Weights plus the key-value cache at the requested context and concurrency |

A quantized parent needs a supported dequantization path, or a separate checkpoint at trainable precision.

Splitting the parent's inference and the child's training into different phases or machines often turns an impossible job into a possible one.

## Can the parent be run?

This decides how good the pruning choices and the recovery can be.

| What is available | Calibration and identity test | Teaching |
|---|---|---|
| Local forward passes with logits and hidden states | Full | Full distributions and activation matching |
| A local server that returns top-k token probabilities | Outputs only | Truncated distributions. Record the missing mass |
| A remote endpoint | Needs authorization, since it is external | Usually text or top-k only |
| Nothing | Weight-only heuristics, labeled unmeasured | None. Recovery is plain continued training |

Write down which row applies. Every later report inherits its limits.

## The feasibility memo

Write `reports/feasibility.md` with the items listed under Gate A in `SKILL.md`, then stop and wait for the person.

For the recovery budget, give an order of magnitude with its basis, and label it an estimate. Use the nearest published precedent you can find and say how far this job sits from it. One anchor is SlimMoE (arXiv:2506.18349), which took a 41.9B MoE down by factors of 5.5 and 11 using 400B training tokens. A ratio well beyond what has been published means nobody knows whether recovery will work, and the memo should say that in those words.

Recommend one of four outcomes: go as specified, go with a smaller ratio, go on different hardware, or stop.
