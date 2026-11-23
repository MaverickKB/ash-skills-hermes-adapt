# Completion manifest

Fill this in at the end of every run, whatever tier it reached. Every number carries one of four labels: estimate, implemented, executed, validated.

- Run name:
- Date:
- Tier reached: 0 plan, 1 structural child, 2 recovered child, or 3 validated child
- One-sentence outcome:

## Parent

- Identity and revision:
- License, and whether it permits this derivative:
- Architecture class: dense, MoE, or hybrid
- Could the parent be run here, and how:

## Counts

Counting convention:

| | Target | Parent, measured | Child, measured |
|---|---|---|---|
| Total parameters | | | |
| Active per token | | | |
| Active without embeddings and head | | | |
| Always-active floor | | | |

Size label claimed, and whether the measured counts support it:

## Lineage

| Share of child parameters | Count | Percent |
|---|---|---|
| Inherited unchanged or by selection | | |
| Transformed | | |
| Newly initialized | | |

`check_manifest.py` last line, with its counts of values compared and not compared:

Any flag that left values uncompared, and how those values were verified:

Report path:

## Gates

| Gate | Status: passed, failed, or not reached | Evidence, as a file path |
|---|---|---|
| A. Go or no-go | | |
| B. Structural | | |
| C. Pilot | | |
| D. Quality and baseline | | |

Version of `gates.yaml` used, its SHA-256, and any change after freezing with the reason:

## What was executed

| Stage | Status | Command or config | Artifacts |
|---|---|---|---|
| | | | |

## Results

| System | Priority-task score | General score | Notes |
|---|---|---|---|
| Parent | | | |
| Pruned child before recovery | | | |
| Recovered child | | | |
| Exported child | | | |
| Existing baseline of similar size | | | |

Sample sizes and intervals:

Resource measurements, and the hardware they were taken on:

## Estimates that were never measured

-

## Failed experiments

-

## Blockers

State each one precisely: what was needed, what was available, and what would unblock it.

-

## How to launch and resume

Commands that were tested:

```
```
