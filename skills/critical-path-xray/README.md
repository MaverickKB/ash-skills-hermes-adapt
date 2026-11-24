# Critical path X-ray

Parse served TensorFold traces (`nsys`, `FZ_ROUND_TIMES`, JSON step dumps) into GPU/host/idle shares, hardware rooflines, dispatch verification and non-overlapping Amdahl bounds.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

You are starting a TensorFold performance hunt and need to know exactly where wall time goes, which kernels are bandwidth- vs launch- vs host-bound, and whether a new kernel actually dispatched in a served request.

## Try it

```text
Use critical-path-xray. Audit this served TensorFold trace against my chip roofline, verify our new kernel dispatched, and screen our top candidate fixes by non-overlapping Amdahl bounds.
```

## What you get

Use the [trace and roofline auditor](scripts/xray_trace.py) for `nsys` CSV, `FZ_ROUND_TIMES` and JSON trace audits, roofline classification and Amdahl screening, backed by the [trace and roofline guide](references/trace-and-roofline.md).

## Pair it with

[TensorFold speed hunt](../tensorfold-speed-hunt/README.md) · [Graph and dispatch surgeon](../graph-and-dispatch-surgeon/README.md) · [Kernel contract](../kernel-contract/README.md) · [Optimization ledger](../optimization-ledger/README.md)
