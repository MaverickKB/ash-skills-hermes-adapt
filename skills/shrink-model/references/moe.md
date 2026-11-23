# Shrinking a mixture-of-experts parent

Phases 3 to 5 for a parent with routed experts. Dense parts of the same model follow `dense.md`.

## Contents

- Two budgets, different levers
- Find the floor first
- Search child shapes
- Compatibility checks before you commit to a shape
- Measuring which experts matter
- Slimming experts
- Removing experts
- Merging experts
- Removing layers
- Specialized attention
- Auxiliary modules
- Check maintained tools first
- Phases 4 and 5
- Routing health
- Training cost follows total size

## Two budgets, different levers

An MoE child usually has two caps: total parameters stored, and parameters active for one token. They respond to different changes.

| Lever | Mainly reduces | Notes |
|---|---|---|
| Routed experts stored per layer | Total | Router rows and biases must be remapped |
| Experts selected per token, top-k | Active | Changes the scale of the layer output. Needs recovery |
| Routed-expert width | Total and active | Every expert survives, each one narrower |
| Layers | Total and active | Indices and cross-layer dependencies need repair |
| Shared experts and dense MLP width | The always-active floor | |
| Residual width | Everything | The most damage. See `dense.md` |
| Auxiliary modules | Total | Drop only what inference never uses |

## Find the floor first

The floor is what stays active with zero routed experts selected: attention, norms, routers, shared experts, and whatever the convention counts from embeddings and head.

```
python3 scripts/budget.py count inventory.json
```

If the floor already exceeds the active budget, no change to routed experts can reach the target. Show that arithmetic in the feasibility memo, then look at depth, shared components, and residual width.

## Search child shapes

```
python3 scripts/budget.py search inventory.json --max-total 35B --max-active 6B \
    --layers 78,60,48 --experts 256,128,64 --top-k 8,4 --expert-width 1,0.5
```

The search is exact for layer count, experts kept, and top-k, because it adds up the inventory's own numbers.

Expert width is a linear model. An expert at width scale w is counted as `params_each` times w, rounded. That is exact only when both of these hold:

- every tensor in an expert has the intermediate width as exactly one of its dimensions
- w times the intermediate width is a whole number

A bias on the down projection, a per-expert norm, or any other term that does not scale with the intermediate width makes the count an estimate. The search marks a row `rounded` when the scale does not land on a whole count. It cannot detect the other cases, so read the expert's tensor shapes in the inventory report.

The search cannot see changes to residual width or attention dimensions at all, because those alter every tensor.

Where the search is not exact, write a small analytic estimator from the model's shapes, prove that it reproduces the parent's measured count exactly, then use it on candidates. Gate B compares the child's measured count with the prediction, so the prediction must come from whichever of the two is exact for the chosen shape.

The search spaces the kept layers evenly, which is good for counting only. Once sensitivity is measured, pass the layers to drop with `--drop-order`, as positions counted from 0. The list must cover every layer that the smallest `--layers` value drops. A shorter list stops the search with an error that says how many more positions it needs.

Return three candidates with exact predicted counts: the closest design inside both budgets, a more conservative one that should recover more easily, and a smaller one where that is useful. For each, give runtime compatibility, memory estimates, and the main recovery risk. Label alternates clearly so nobody mistakes one for the primary target.

## Compatibility checks before you commit to a shape

- Top-k is no larger than the experts kept.
- The runtime supports the new expert count and expert size. Fused or grouped expert kernels often require every expert in a layer to be the same size.
- Routing normalization still behaves. When top-k changes, check how the model scales and renormalizes routing weights.
- Latent or low-rank attention projections, sparse-attention indexers, and positional mechanisms keep their constraints.
- Dependencies between layers survive, such as an indexer one layer shares with others, or a rule that the first few layers are dense.
- Quantization group sizes still divide the new dimensions.

## Measuring which experts matter

Run the parent on calibration data that covers the priority capabilities and general text. For each expert record:

- how often it is selected, and with how much routing weight
- the size of its contribution to the layer output
- the change in loss when it is ablated, which is the strongest signal
- how similar its behavior is to other experts in its layer

Break these down by domain. An expert that is rarely selected overall can carry a specialized capability, and it only shows up in the slice where it matters. Selection frequency alone is not enough to remove an expert.

If the parent cannot be run, only weights are available: router row norms, expert weight norms, similarity between experts. Label any choice made this way as unmeasured. In that situation, slimming every expert evenly is the safer default than removing experts blind, because it keeps each specialization. Test that default in the pilot.

## Slimming experts

Pick one set of intermediate channels for each expert. Apply it to the rows of the gate and up projections and to the columns of the down projection. Every expert in a layer ends at the same width if the runtime requires uniform experts.

Published precedent favors this lever. SlimMoE (arXiv:2506.18349) compressed a 41.9B MoE to 7.6B and 3.8B by slimming experts in stages with distillation between stages, using 400B training tokens. Look for newer work before relying on that figure.

## Removing experts

Everything that refers to an expert by position has to move together:

- expert ids and checkpoint keys, renumbered without gaps
- the matching router rows
- router correction biases
- group metadata, where experts are grouped
- the expert count in the config
- top-k, normalization, and scaling

Write the old-to-new id map into the manifest. Recovery needs it to align teacher and child routing.

## Merging experts

Merge only with a method that aligns them first, such as matching channels by permutation or fitting the merged expert to the pair's combined outputs on calibration data. Averaging two unaligned experts destroys both. A merged tensor is a `transform` in the manifest.

## Removing layers

Renumber the layers, update the config, and repair every cross-layer dependency. Shared indexers and layer-specific rules are the usual casualties.

## Specialized attention

Latent projections and sparse attention with an indexer are not ordinary multi-head attention. Apply only transformations you have validated on a fixture of the same design. Ordinary head pruning does not carry over.

## Auxiliary modules

A module that inference never calls, such as a multi-token prediction head used only in training, can be dropped. Record the decision. If it stays in the package, it counts toward the total. To keep it out of the active count, add `aux_modules` to `convention.inactive` in the inventory.

## Check maintained tools first

NVIDIA Model Optimizer supports some MoE models. Per its changelog, its Minitron pruner accepts an active-parameter constraint and prunes one shared expert width across experts, and whole-expert removal lives in a separate pruner named Puzzletron. Confirm against the current documentation that the exact parent architecture and the transformations you need are supported. Support for inference or training does not imply support for pruning.

## Phases 4 and 5

Follow the fixture and pruning steps in `dense.md`, on a tiny MoE of the same design, and add these tests:

- removing experts leaves router rows, biases, and ids aligned, checked by routing a fixed input before and after and comparing which surviving experts are chosen
- slimming keeps the selected channels' values identical to the parent
- a slimmed expert gives the same output as the original expert with the dropped channels set to zero, which shows that its gate, up, and down projections used one index set
- the identity conversion reproduces routing decisions as well as outputs
- every routing index in a forward pass is within range

## Routing health

Watch these after pruning and throughout recovery:

- utilization of each expert
- entropy of the routing distribution
- load imbalance across experts
- experts that collapse to never or always selected
- dropped tokens, where the router enforces capacity

## Training cost follows total size

Within one batch, different tokens select different experts, so a batch touches most of them. Training memory and time scale with total parameters. The active count describes one token at inference, and it does not describe a training step.
