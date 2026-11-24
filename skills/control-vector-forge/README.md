# Control vector forge

Compile engineering rigor, anti-slop discipline, anti-sycophancy and concise coding into tiny drop-in `.gguf` control vectors for `llama.cpp`.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You want to steer a local model's behavior at zero prompt-token cost without fine-tuning, using built-in `mythos-engineering` and `slopchop` contrastive packs, binary `.gguf` inspection and perplexity + task-retention gates.

## Try it

```text
Use control-vector-forge. Export the mythos-engineering and slopchop presets, plan the layer range for my 48-layer GGUF, inspect the compiled control vector, and gate the strength sweep against perplexity and coding retention.
```

## What you get

Use the [control vector workbench](scripts/cvector_forge.py) with built-in [contrastive presets](assets/presets.json) and the [representation engineering craft guide](references/control-vector-craft.md).

## Pair it with

[Mythos Mode](../mythos-mode/README.md) · [Slopchop](../slopchop/README.md) · [Quant autopsy](../tensorfold/quant-autopsy/README.md) · [Local coder overclock](../local-coder-overclock/README.md)
