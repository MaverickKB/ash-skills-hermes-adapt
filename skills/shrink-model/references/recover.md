# Recovering capability

Phases 6 and 7. A child that passes Gate B loads and runs. It is not yet useful. Keep those two claims apart in every report.

## What the teacher can give you

Use the original parent as the teacher where it can be run. What it can supply sets what kind of recovery this is.

| Teacher access | Supervision available | Say this in reports |
|---|---|---|
| Local, with full outputs | Full token distributions, hidden states, routing | Full distillation |
| Top-k token probabilities | Truncated distributions | Approximate distillation. State the missing probability mass and how you handled it |
| Text only | Generated answers | Training on teacher text. It is not distribution distillation |
| None | Nothing from the parent | Continued training only |

Freeze the teacher. Cache its outputs when that is cheaper than rerunning it, and budget the storage first.

When the parent is the model running this skill, your own serving endpoint is the teacher. Check which row of the table it supports.

## Staged or direct

For a large ratio, compare two paths with bounded pilots: straight to the target, and through one or more intermediate sizes with recovery at each. Choose from the pilot results and the budget. Staging has published support at ratios near ten, and it also multiplies training cost, so do not assume it wins.

## The recovery corpus

Build it from licensed sources. Mix three kinds of material:

- representative general text
- tasks from the priority capabilities
- demonstrations from the parent, after checking

The parent's own answers are not proof of correctness. Check them before they become training data:

- run generated code against independent tests in a sandbox
- verify mathematics and structured outputs with a program
- compare factual material against reliable references

Keep a diverse base of seed and reference data. Recycling unchecked synthetic examples narrows the child and bakes in the parent's mistakes.

## Losses

Pick from these and write the choice into the config, with weights, masks, temperatures, and schedules.

- language-model cross-entropy on text
- divergence between teacher and child token distributions, at a stated temperature
- matching of aligned block outputs or activations, where shapes line up
- instruction tuning on verified demonstrations
- router recalibration with a load-balancing term

Two details that cause silent errors:

- **Width changes break activation matching.** When the child is narrower, the comparison needs a projection. That projection is training equipment and must not ship in the child.
- **Routing targets need a map.** After experts are removed, comparing router distributions requires the old-to-new expert map from the manifest, with the teacher's distribution renormalized over the survivors.

## Tokenizer and templates

Keep the parent's tokenizer unless measured evidence justifies a change. Check that teacher and child see identical token ids for the same text, and check the chat template, special tokens, end-of-sequence handling, sequence packing, and truncation. A mismatch in any of these looks like a quality problem and is a data problem.

## What to train

Choose from the damage done, not from convenience.

- After structural surgery, full-weight training is the usual requirement.
- Partial unfreezing suits light changes, such as a few removed layers.
- Adapters cannot rebuild missing structure. A short adapter tune will not restore a heavily pruned model. If adapters are used, merge them or count them in the total.

## Budget

Budget against all trainable parameters and measured throughput. Run a short probe, record tokens per second and peak memory, and project the full run from those numbers. For MoE, remember that a batch touches most experts, so cost follows total size.

Cap tokens, optimizer steps, wall time, storage, and the number of experiments before anything starts.

## Monitor

- training and development loss
- task metrics on the development split
- gradient norms, and any non-finite value
- for MoE, the routing health list in `moe.md`

## Halting rules

- **Out of memory.** Reduce the batch or accumulate gradients. Do not quietly change the model.
- **Non-finite loss or broken gradients.** Stop. Keep the last good checkpoint. Find the cause before resuming.
- **A failed gate.** No promotion. The previous best checkpoint stays the candidate.
- **Severe regression on the development split.** Stop and diagnose before spending more.

## Resuming

Long stages must restart cleanly. Save the optimizer state, the scheduler state, the position in the data, and the seeds with every checkpoint, and test a resume during the pilot.
