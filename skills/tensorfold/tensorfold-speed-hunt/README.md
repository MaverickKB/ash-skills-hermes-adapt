# TensorFold speed hunt

Run the combined 7-gate closed-loop TensorFold development workflow from served trace to fresh-session referee confirmation without skipping a gate.

[Agent instructions](SKILL.md) · [All skills](../../../README.md#skill-directory)

## When to use it

You are doing an open-ended local TensorFold speedup hunt on Metal, CUDA or Zig and want one stateful driver chaining `critical-path-xray`, `optimization-ledger`, specialist fixes, `sass-and-air-diff`, `output-parity` and `speedup-referee`.

## Try it

```text
Use tensorfold-speed-hunt. Initialize a 7-gate hunt run in ../tf-notes/, trace our served decode request, run ledger preflight, fix the top bottleneck, clear binary, parity and mirrored A/B gates, and check landing readiness.
```

## What you get

Use the [7-gate closed-loop driver](scripts/loop_driver.py) with tamper-checked receipt hashing, backed by the [specialist routing and combination guide](references/loop-routing.md).

## Pair it with

[Critical path X-ray](../critical-path-xray/README.md) · [SASS and AIR diff](../sass-and-air-diff/README.md) · [Optimization ledger](../optimization-ledger/README.md) · [Graph and dispatch surgeon](../graph-and-dispatch-surgeon/README.md) · [Speedup referee](../speedup-referee/README.md)
