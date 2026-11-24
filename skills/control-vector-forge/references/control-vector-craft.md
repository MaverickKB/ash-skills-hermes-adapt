# Control vector craft and representation engineering

How native `llama.cpp` GGUF control vectors work, why contrastive pair symmetry makes or breaks them, and how to tune and compose vectors without damaging intelligence.

## Contents

1. How GGUF control vectors steer a model at zero prompt-token cost
2. Designing clean contrastive datasets
3. Layer-band anatomy
4. Extraction workflows (llama-cvector-generator and Python repeng)
5. Composing multiple vectors safely
6. Failure modes and how to spot them

## 1. How GGUF control vectors steer a model at zero prompt-token cost

A control vector stores one `hidden_size` float vector (`direction.<layer>`) per transformer block in a tiny `.gguf` file (typically 0.5 to 4 MiB). At inference time, `llama-server` adds `scale * direction[layer]` to the residual stream inside the chosen layer range (`--control-vector-layer-range`).

Compared to long system prompts or LoRA fine-tuning:

- **Zero context cost:** A 2,500-token engineering discipline prompt (`mythos-mode`) or anti-slop guide (`slopchop`) burns KV cache and attention compute on every turn. A compiled control vector nudges the same behavior inside hidden states using zero prompt tokens.
- **Instant and reversible:** Dial strength up, down, or off (`scale=0.0`) per server instance without modifying or merging base weights.
- **Architecture-specific:** A vector is tied to the exact model checkpoint and `hidden_size` it was extracted from (and usually transfers cleanly across quants of that same checkpoint, such as `Q8_0` -> `Q4_K_M`, though always re-check scale on lower quants).

## 2. Designing clean contrastive datasets

PCA extracts the single highest-variance direction separating positive activations from negative activations. Anything that accidentally differs between the two sets ends up baked into the vector:

- **Match lengths tightly.** If positive prompts average 15 words and negative prompts average 45 words, PCA extracts a "short vs long" vector rather than "verified vs unverified" or "clean prose vs slop". Keep `abs(pos_len / neg_len - 1.0) <= 0.25`.
- **Keep topics neutral and shared.** Pair the exact same set of everyday tasks, domains and technical subjects on both sides so only the target behavior contrasts.
- **Pair with diverse neutral stems.** Feed each positive/negative persona statement before 20 to 50 varied neutral user prompts (code review, debugging, architecture trade-offs, prose explanations) so PCA averages out prompt-specific activations.
- **Minimum size:** 12-24 pairs works for a quick smoke extraction; expand to 64-256 diverse pairs before publishing a vector or deploying it daily.

## 3. Layer-band anatomy

Applying a control vector across every layer (`0..L-1`) almost always causes token gibberish or repetition loops. Transformer layers specialize cleanly:

- **Early layers (`0` to ~25%):** Tokenization, local syntax, lexical lookup. Leave untouched.
- **Core reasoning middle (~28% to ~72%):** Truth tracking, sycophancy resistance, verification rigor, problem decomposition. Best band for `mythos-engineering`, `anti-sycophancy` and `deep-reasoning`.
- **Upper-middle style band (~38% to ~82%):** Voice, register, conciseness, cliché suppression. Best band for `slopchop` and `concise-coder`.
- **Final layers (~85% to `L-1`):** Direct logit/vocabulary projection. Steering here skews next-token probabilities harshly and spikes perplexity.

## 4. Extraction workflows

### Native `llama.cpp` (`llama-cvector-generator`)

```bash
llama-cvector-generator \
  -m model-Q8_0.gguf \
  --positive-file work/positive.txt \
  --negative-file work/negative.txt \
  --pca-iter 1000 \
  --pca-batch 100 \
  -o work/mythos-engineering.gguf
```

Prefer extracting from `BF16`, `F16` or `Q8_0` weights so quantization noise does not pollute the principal component, then deploy the resulting `.gguf` control vector on your daily `Q6_K` or `Q4_K_M` quant.

### Python activation extraction (PyTorch / `repeng`)

When you need custom chat-template wrapping, multi-turn sycophancy activations, or mean-difference vs PCA comparisons, harvest last-token residual-stream activations in PyTorch across `(positive_prefix + stem)` vs `(negative_prefix + stem)`, compute the first principal component (or normalized mean difference) per layer, orient the sign using positive-minus-negative dot product, and serialize each layer as `direction.<layer>` (`F32`) in GGUF v3. Always validate the resulting file with `cvector_forge.py inspect-gguf`.

## 5. Composing multiple vectors safely

`llama-server` accepts multiple `--control-vector-scaled` flags at once, for example stacking `mythos-engineering.gguf` and `slopchop.gguf`:

```bash
llama-server -m model-Q4_K_M.gguf \
  --control-vector-scaled mythos-engineering.gguf 0.45 \
  --control-vector-scaled slopchop.gguf 0.35 \
  --control-vector-layer-range 14 34
```

When composing two vectors, reduce each individual scale by roughly 25-35% and re-run the perplexity + task-retention gate on the **combined** configuration. Two vectors that each pass at `+1.8%` perplexity alone can combine into `+6%` degradation if their directions partially align.

## 6. Failure modes and how to spot them

| Symptom | Cause | Fix |
| --- | --- | --- |
| Inverted scale (`-0.5`) does not reverse behavior | Pairs extracted noise or topic bias instead of the target trait | Run `lint-pairs`, rebalance lengths, double diverse stems |
| Repetition loops or broken JSON at low scales (`0.4`) | Active layer range reaches too close to layer 0 or layer `L-1`, or one layer has a norm spike | Run `inspect-gguf`, narrow `--control-vector-layer-range` to middle layers |
| Model becomes terse but loses coding accuracy | Scale too high (`ppl` rose > 3% or task retention < 97%) | Drop scale or switch from `broad_upper` to `tight_core` band |
| Vector works on `Q8_0` but loops on `Q4_K_M` | Lower quant has smaller residual margin | Lower scale by 0.15-0.25 and re-verify with `quant-autopsy` |
