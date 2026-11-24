# Compiler resource cliffs, SASS identity and Metal AIR checks

Why editing one shared header or inline helper silently slows neighbor kernels on CUDA and Apple Silicon Metal, and how to gate compiled kernel binaries automatically before benchmarking.

## Contents

1. Why shared kernel headers cause silent regressions
2. Capturing CUDA `ptxas` and `cuobjdump` snapshots
3. Capturing Apple Metal compiler and pipeline snapshots
4. Interpreting register and spill cliffs

## 1. Why shared kernel headers cause silent regressions

GPU compilers allocate registers aggressively across inlined helpers. Adding one branch, widening an accumulator, or editing a shared dequantization template in a header frequently shifts register pressure in kernels you never meant to touch:

- **Occupancy cliffs.** Crossing a warp-register threshold (for example 32 -> 34 or 64 -> 68 registers per thread on CUDA) can cut active warps per SM by 25-50% instantaneously.
- **Local-memory spills (`STL`/`LDL`).** Once a kernel exceeds its register cap, values spill into L1/L2 local memory. Even 16 bytes of inner-loop spill inside a quantized GEMV/QMM kernel adds hidden memory traffic that destroys decode speed.
- **Unintended arithmetic changes.** A header edit that changes instruction order in an untouched kernel can silently break bit-exact parity on another model family.

## 2. Capturing CUDA `ptxas` and `cuobjdump` snapshots

Capture both resource usage and disassembled instructions on `base` and `candidate` builds:

```bash
# Resource log during compilation (registers, spill stores/loads, smem)
nvcc --ptxas-options=-v ... 2> notes/ptxas_base.log
python3 scripts/binary_gate.py parse-ptxas notes/ptxas_base.log --out notes/ptxas_base.json

# Normalized SASS instruction stream and spill-op counts
cuobjdump --dump-sass build/libtensorfold_cuda.so > notes/sass_base.txt
python3 scripts/binary_gate.py parse-sass notes/sass_base.txt --out notes/sass_base.json
```

Then gate the candidate build naming only the kernel(s) you intentionally changed:

```bash
python3 scripts/binary_gate.py diff notes/sass_base.json notes/sass_cand.json \
  --allow-changed qmm_group_fp4 --forbid-new-spills --json notes/sass_diff.json
```

## 3. Capturing Apple Metal compiler and pipeline snapshots

On Apple Silicon Metal, record compiled pipeline metadata (`maxTotalThreadsPerThreadgroup`, threadgroup memory length, shader binary SHA-256 and register/spill stats from Xcode Metal shader diagnostics or `metal-objdump`) into the same `{"kernels": {"<name>": {"sha256": "...", "registers": N, "spill_stores": 0, "smem_bytes": M}}}` JSON format and run `binary_gate.py diff` identically.

## 4. Interpreting register and spill cliffs

- **`[UNTOUCHED]` findings.** Never ignore an untouched kernel that changed SASS or registers. Split the helper, specialize the new path under a separate inline function, or pin `__launch_bounds__` before benchmarking.
- **`[SPILL]` findings.** A kernel moving from `0` to `> 0` spill bytes or `STL`/`LDL` ops inside an inner decode loop is almost always a regression; shrink live tile accumulators or split the fusion before trusting microbench numbers.
