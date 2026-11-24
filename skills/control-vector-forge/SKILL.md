---
name: control-vector-forge
description: Builds, audits and gates native `llama.cpp` GGUF control vectors to steer local model behavior at inference time without fine-tuning or spending prompt tokens. Ships balanced contrastive presets (including `mythos-engineering`, `slopchop`, `anti-sycophancy`, `concise-coder` and `deep-reasoning`), a dataset bias/leakage linter, layer-band planners, a binary `.gguf` control-vector health inspector and a perplexity + task-retention sweep gate (`cvector_forge.py`). Use when creating GGUF control vectors, steering local models to follow engineering discipline or kill AI slop, auditing contrastive datasets, or tuning `--control-vector-scaled` ranges safely.
metadata:
  version: "1.0.0"
  hermes:
    tags: [local-models, steering, gguf, control-vectors]
    related_skills: [slopchop, mythos-mode, quant-autopsy]
---

# Control vector forge

Prompting a small local model with 2,500 tokens of engineering rules or anti-slop instructions eats KV cache on every turn, and full LoRA fine-tuning takes heavy VRAM and risks forgetting. Native `llama.cpp` GGUF control vectors steer hidden states directly inside middle transformer layers as reversible, composable ~2 MiB plugins. The catch is that sloppy datasets or wide layer bands quietly brain-damage the model. This skill builds, inspects and gates every vector scientifically.

## Ground rules

1. **Audit pairs before extracting.** Unbalanced lengths or one-sided topics make PCA extract a length or topic vector instead of the target behavior. Pass `lint-pairs` with zero errors first.
2. **Always run both controls.** Every scale sweep must include `scale=0.0` (identity baseline) and an inverted negative control (`scale=-0.5`). If the negative scale does not push behavior in the opposite direction, the vector is noise and must be rebuilt.
3. **Never lobotomize the base model.** Reject any `(layer_range, scale)` that raises perplexity above `+3.0%` or drops coding/logic/tool-calling below `97%` of the unsteered baseline.
4. **Inspect the binary `.gguf` before serving.** Check every `direction.<layer>` tensor for `NaN`/`Inf`, dead zero layers, hidden-dim mismatches and >3x L2 norm spikes.

## Step 1: Export or craft balanced contrastive pairs

List and export any built-in preset from `assets/presets.json` (`mythos-engineering` distilled from `mythos-mode`, `slopchop` distilled from `slopchop`, `anti-sycophancy`, `concise-coder`, `deep-reasoning`):

```bash
python3 scripts/cvector_forge.py presets --list
python3 scripts/cvector_forge.py presets --export mythos-engineering --out-dir work/mythos-cv
```

For production vectors, expand the exported `positive.txt` and `negative.txt` to 64-256 pairs matched across diverse neutral topics (`prompts_neutral.txt`), then lint before extraction:

```bash
python3 scripts/cvector_forge.py lint-pairs --pos work/mythos-cv/positive.txt --neg work/mythos-cv/negative.txt
```

Exit codes: `0` `PAIRS: CLEAN`, `1` `PAIRS: ISSUES` (count mismatch, length bias > 25%, duplicates, empty entries or topic leakage), `2` unreadable file.

## Step 2: Plan active layer bands and extract the `.gguf`

Compute tight, balanced and broad middle-layer bands for your checkpoint's layer count and print the exact `llama-cvector-generator` command:

```bash
python3 scripts/cvector_forge.py layer-plan \
  --layers 48 --behavior coding \
  --model-gguf model-Q8_0.gguf \
  --pos-file work/mythos-cv/positive.txt \
  --neg-file work/mythos-cv/negative.txt \
  --out-gguf work/mythos-engineering.gguf
```

Extract from the highest precision you can load (`BF16`, `F16` or `Q8_0`) so quantization noise does not corrupt PCA directions.

## Step 3: Inspect the compiled `.gguf` vector binary

Verify tensor integrity directly on the `.gguf` file using the stdlib binary inspector:

```bash
python3 scripts/cvector_forge.py inspect-gguf work/mythos-engineering.gguf
```

Checks every `direction.<layer>` tensor (`F32` / `F16`), confirms uniform `hidden_dim`, flags `NaN`/`Inf` or zero vectors, checks per-layer L2 norm spikes (> 3x median), and computes adjacent-layer cosine stability. Exits `0` (`CVECTOR: HEALTHY`) or `1` (`CVECTOR: CORRUPT`).

## Step 4: Sweep scales and gate against degradation

Measure perplexity (`llama-perplexity`), a fixed coding/logic/tool task score (such as `quant-autopsy`), and your target behavior metric across `scale = 0.0` (baseline), `scale = -0.5` (inverted negative control), and positive candidates (`0.25, 0.50, 0.75, 1.0, 1.25`) across your planned layer bands. Record the runs in `sweep.json` and run the gate:

```bash
python3 scripts/cvector_forge.py score-sweep work/sweep.json \
  --max-ppl-increase-pct 3.0 --min-task-retention 0.97
```

`score-sweep` rejects vectors whose negative control fails to reverse behavior (`SWEEP: FAILED_DIRECTION`), filters out any scale breaching perplexity or task-retention gates, picks the strongest safe `(layer_range, scale)`, and prints the exact `--control-vector-scaled` + `--control-vector-layer-range` flags for `llama-server`.

## Deep craft and multi-vector stacking

Read `references/control-vector-craft.md` for PCA vs mean-difference extraction, layer-band anatomy, PyTorch/`repeng` extraction steps, stacking multiple vectors (such as `mythos-engineering.gguf` + `slopchop.gguf`) without compounding perplexity drift, and troubleshooting repetition loops.
