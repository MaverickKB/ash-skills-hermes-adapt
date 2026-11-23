# Kernel contract

Compares kernel outputs bit for bit or against a specified floating-point tolerance.

## When to use it

You're fusing, replacing, or tuning a GPU or CPU inference kernel.

Test odd tensor sizes and special floating-point values, then deliberately change an output to see whether the check catches it.

## What's included

`scripts/kernel_gate.py` compares JSON tensors or raw binary tensors and runs a negative control.

Open [SKILL.md](SKILL.md) for the full instructions.
