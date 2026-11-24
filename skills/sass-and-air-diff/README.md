# SASS and AIR diff

Verify that CUDA or Metal kernel edits leave untouched neighbor kernels bit-identical and introduce zero register cliffs or local-memory spills.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You edited a GPU kernel or shared CUDA/Metal header in TensorFold and want automated proof that untouched kernels did not change SASS, bump registers or start spilling before you benchmark or land.

## Try it

```text
Use sass-and-air-diff. Parse base and candidate ptxas and cuobjdump SASS dumps, verify untouched kernels stayed identical, and flag any new register spills.
```

## What you get

Use the [GPU binary regression gate](scripts/binary_gate.py) for `ptxas -v` parsing, normalized `cuobjdump` SASS hashing and untouched-kernel diffing, backed by the [compiler and occupancy guide](references/compiler-and-occupancy.md).

## Pair it with

[Kernel contract](../kernel-contract/README.md) · [Critical path X-ray](../critical-path-xray/README.md) · [TensorFold speed hunt](../tensorfold-speed-hunt/README.md)
