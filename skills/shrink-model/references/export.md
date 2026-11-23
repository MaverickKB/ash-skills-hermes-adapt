# Exporting a child that runs alone

Phase 9. The deliverable is a package that a fresh process can load and run with no trace of the parent.

## What the package holds

- the native trainable checkpoint
- the tokenizer and the chat template
- the config that describes the child's real shape
- the inheritance manifest
- license notices and provenance carried over from the parent
- a model card

## Count what ships

Run the counter on the export itself.

```
python3 scripts/count_params.py export/child
```

Count every unique parameter inference needs. That includes shared experts, routed experts that are rarely selected, adapters that were not merged, and any auxiliary module left in the package.

Report total and active separately, with the convention, and with mean and worst case if routing varies.

## Quantize last

Quantize after recovery, never before it. Use the quantization calibration partition. Budget for the conversion peak, which can exceed both the input and the output in size.

The quantized artifact is a different model from the trainable checkpoint. Evaluate it again, on the same harness.

## Runtime support

Confirm that the chosen runtime loads the child's actual configuration: its layer count, its expert count and width, its top-k, its attention dimensions. A runtime that supports the parent does not automatically support a pruned descendant with a shape nobody has shipped before. If a config class or loader patch is needed, write it, test it, and include it in the package.

## Prove it runs alone

1. Stop every parent process and make the parent files unavailable to the new process.
2. Start a fresh process from the child package only.
3. Run with the network off, or monitor it, so an undeclared remote dependency shows up.
4. Run representative tasks at the requested context length.
5. Record memory and latency.

Inference that falls back to the parent in any way fails this phase.

## Naming and the model card

Use a name that is clearly an unofficial derivative and implies no endorsement by the parent's authors. Check the license for naming conditions.

Apply a size label only when the measured counts support it. A label such as "35B-A6B" requires a total at or under 35.0B and an active count at or under 6.0B, under the stated convention. A model at 36.2B is a 36B model.

The model card states:

- the parent, its revision, and its license
- how the child was derived, with the share of parameters inherited, transformed, and newly initialized
- measured total and active counts and the convention
- the evaluation results, including the comparison with the existing baseline
- what was lost relative to the parent
- the limits of the measurements

Do not describe the active count as a latency or FLOP equivalent of a dense model that size.

## The completion manifest

Fill in `assets/completion.template.md` as `reports/completion.md`. It separates what passed, what failed, what is only estimated, and what blocked the run. Fill it in at whatever tier the run ended.
