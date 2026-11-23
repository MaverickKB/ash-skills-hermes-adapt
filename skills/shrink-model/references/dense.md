# Shrinking a dense parent

Phases 3 to 5 for a parent with no routed experts. A hybrid parent uses this file for its dense parts and `moe.md` for the rest.

## The levers

Listed from least to most disruptive. Prefer the earlier ones, and combine them only as far as the target requires.

| Lever | What changes | What it costs |
|---|---|---|
| Depth | Whole blocks are removed | No tensor is reshaped. Layer indices and the config need repair |
| MLP intermediate width | Channels are dropped inside each MLP | Kept channels are untouched. The dropped ones lose their contribution |
| Attention heads | Whole heads are dropped | Only valid where heads are separable slices. See the checks below |
| Residual width | Every tensor that touches the residual stream shrinks | The most damage and the most recovery. Use it last |

## Choosing what to remove

Measure. Do not assume that any region of the network is redundant.

- For depth, ablate each block on calibration data and record the change in loss. Remove the blocks whose removal costs least, then check the combination, because the costs do not simply add.
- For channels and heads, score importance from activations or first-order gradient terms on calibration data, then confirm the ranking by ablating a sample.
- Weight magnitude alone is weak evidence. Use it only when the parent cannot be run, and label the result as unmeasured.

Calibration data comes from its own partition. See `evaluate.md`.

## Consistency rules

A dropped channel must disappear from every tensor it touches. One mismatch produces a model that loads and computes garbage.

- **MLP width.** Use one index set per MLP. Apply it to the rows of the up and gate projections, to their biases, and to the columns of the down projection.
- **Heads.** Apply the head selection to the query, key, and value projections on the output side, and to the output projection on the input side.
- **Residual width.** Use one index set for the whole model. Apply it to the embedding columns, every norm weight and bias, the residual side of every projection, and the output head. If embeddings and head are tied, keep them tied.
- **Depth.** Renumber the remaining blocks, update the layer count in the config, and repair anything that refers to a layer by index.

Selecting residual channels is not function-preserving even for the kept channels, because every norm now sees different statistics. Expect a large drop before recovery.

A learned projection, such as one fitted by principal components, is an alternative to channel selection. It turns every affected tensor into a transformed tensor in the manifest, with nothing left as a plain copy.

## Compatibility checks before you commit to a shape

- The head count still divides the attention width.
- With grouped-query attention, the number of query heads is still a multiple of the number of key-value heads.
- With rotary position embeddings, the head dimension is still even, and the channel pairing the implementation uses is intact. Dropping whole heads keeps this safe. Dropping channels inside a head usually does not.
- Learned absolute position embeddings get the same residual index set as everything else.
- Quantization group sizes and any alignment the target runtime requires still divide the new dimensions.

Read the parent's code for each of these. Attention variants differ, and a rule that holds for one does not hold for the next.

## Check maintained tools first

Before writing pruning code, look for a maintained tool that already supports this architecture. NVIDIA Model Optimizer is one, with depth and width pruning followed by distillation. Confirm against its current documentation that it supports this exact parent and the transformations you need. Support for running or training a model does not imply support for pruning it.

If no tool fits, write an adapter for this architecture and test it as below.

## Phase 4: fixtures

Build a tiny random model of the same class, a few layers at small width, and test on it before touching the parent.

- **Identity conversion.** Run the pipeline with nothing removed. Outputs must match the original within a tolerance you state, along with the dtype it was measured in.
- **Each transformation alone.** Depth, MLP width, heads, and residual width each get a test that checks shapes, that the kept values equal the source values, and that a forward and backward pass are finite.
- **Consistency across tensors.** For MLP width and heads, the pruned fixture must give the same outputs as the unpruned fixture with the dropped channels or heads set to zero, within a stated tolerance. A different index set on two tensors that should share one shows up here.
- **Counts.** The fixture child's measured parameter count equals the estimator's prediction exactly.
- **Round trip.** Save, reload, and compare outputs.

## Phase 5: prune the parent

1. Stream tensor by tensor. Do not hold parent and child in memory together unless the machine has room for both.
2. Write each child tensor and its manifest entry in the same step, so the manifest cannot drift from the weights.
3. Use these manifest operations: `copy` for an unchanged tensor, `index_select` for channel or row selection, `transform` for anything fitted or projected, and `new_init` for anything created fresh.
4. Run `scripts/check_manifest.py`, then the rest of Gate B.

The schema is in `assets/manifest.schema.json`.

## What the manifest check proves

`scripts/check_manifest.py` compares every inherited value with the parent. A pass proves that the child matches its manifest.

A pass does not prove that the manifest is consistent across tensors. Suppose the manifest keeps one index set for the rows of an up projection and a different set for the columns of the matching down projection. Each tensor still matches what the manifest says about it, so the check passes, and the model computes garbage. The phase 4 fixtures cover this. The consistency test there fails when two tensors disagree, and the identity conversion shows that the pipeline changes nothing it was not asked to change. A passing manifest check is no reason to skip them.

The check fails when an inherited value was not compared. Its last line gives both counts:

```
PASS: compared 3,552 of 3,552 inherited values, 0 not compared
```

A tensor whose dtype differs from the parent's cannot be compared byte for byte. Run the check against a parent copy in the child's dtype, such as the dequantized trainable copy. Where no such copy exists, pass `--allow-dtype-change`, record the count of uncompared values from the last line, and verify those values another way. `--sample-per-index N` is a faster partial check for work in progress. It touches every kept row and column but not every value, so it leaves values uncompared and does not satisfy Gate B.
