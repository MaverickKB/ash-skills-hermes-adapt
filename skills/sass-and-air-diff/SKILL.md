---
name: sass-and-air-diff
description: Compares compiled GPU kernel binaries (`cuobjdump --dump-sass`, `ptxas -v` logs and Metal AIR/pipeline snapshots) before and after a change to prove untouched neighbor kernels stayed bit-identical and caught zero register bloat or local-memory spills (`STL`/`LDL`). Ships a stdlib SASS/ptxas parser and binary diff gate (`binary_gate.py`). Use after editing CUDA, Metal or shared GPU headers in TensorFold before benchmarking or landing a kernel change.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, kernels, sass, metal-air]
    related_skills: [critical-path-xray, kernel-contract]
---

# SASS and AIR diff

Shared `.cuh`, `.metal` and Zig kernel headers hide one of the nastiest traps in inference engine development: editing one helper silently bumps register counts or introduces local-memory spills in untouched neighbor kernels, cutting occupancy on another model family without failing any unit test. Diff the compiled binaries before you benchmark or land.

## Ground rules

1. **Name every kernel you meant to touch.** Pass `--allow-changed <kernel>` explicitly; every other compiled kernel must remain untouched.
2. **Treat new inner-loop spills as blockers.** Pass `--forbid-new-spills` so moving from `0` to `> 0` spill bytes or `STL`/`LDL` ops fails the gate immediately.
3. **Never substitute source review for binary inspection.** Compiler inlining and register allocation are non-local; inspect compiled `ptxas`, `cuobjdump` or Metal AIR output directly.

## Step 1: Snapshot baseline and candidate builds

For CUDA `ptxas -v` compiler logs:

```bash
python3 scripts/binary_gate.py parse-ptxas notes/ptxas_base.log --out notes/ptxas_base.json
python3 scripts/binary_gate.py parse-ptxas notes/ptxas_cand.log --out notes/ptxas_cand.json
```

For `cuobjdump --dump-sass` disassemblies (computes normalized per-kernel instruction hashes, `STL`/`LDL` spill-op counts, tensor-core ops and barriers):

```bash
python3 scripts/binary_gate.py parse-sass notes/sass_base.txt --out notes/sass_base.json
python3 scripts/binary_gate.py parse-sass notes/sass_cand.txt --out notes/sass_cand.json
```

For Metal, emit per-pipeline JSON (`{"kernels": {"name": {"sha256": "...", "registers": N, "spill_stores": 0, "smem_bytes": M}}}`) using the exact same schema.

## Step 2: Run the untouched-kernel binary gate

```bash
python3 scripts/binary_gate.py diff notes/sass_base.json notes/sass_cand.json \
  --allow-changed qmm_group_fp4 \
  --forbid-new-spills \
  --json notes/binary_gate.json
```

Exits `0` (`BINARY GATE: PASS`) when every untouched kernel kept identical `sha256`, registers, spills and shared memory (and allowed kernels introduced zero new spills); exits `1` (`BINARY GATE: REGRESSION`) listing every affected untouched kernel, missing kernel or new spill.

## Step 3: Hand off to correctness and served parity

Once `binary_gate.py` passes, verify numerical contracts with `kernel-contract`, full token-hash parity with `output-parity`, and mirrored served throughput with `optimization-ledger`.

## Reference guide

Read `references/compiler-and-occupancy.md` for CUDA `nvcc`/`cuobjdump` capture commands, Metal pipeline stat capture, and debugging register/occupancy cliffs.
