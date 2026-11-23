# One-stream lanes

Measures the gap between single-request and concurrent throughput and evaluates draft widths.

## When to use it

One request leaves the accelerator idle or a wider draft window stops improving decode speed.

Compare tokens accepted per pass and passes per second across draft widths, with matching token hashes for the measured cells.

## What's included

`scripts/lane_math.py` calculates single-stream share and compares throughput and token receipts.

Open [SKILL.md](SKILL.md) for the full instructions.
