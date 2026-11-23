# Evaluating the child

Phase 8, plus the data rules that every earlier phase depends on.

## Five partitions

| Partition | Used for |
|---|---|
| Training | Recovery |
| Pruning calibration | Importance scores and expert utility |
| Quantization calibration | Export |
| Development | Iteration, pilot gates, hyperparameters |
| Sealed test | One final measurement of a frozen candidate |

Split by source, problem, or template family first, and generate variations afterward. Splitting after generation puts near-copies on both sides. Deduplicate near matches across partitions.

The sealed test is never used for training, architecture search, routing choices, quantization, or tuning.

Store provenance for everything: source, license, parent revision, generation settings, validators, filtering decisions, and a hash of each dataset.

## Freeze the gates

Fill in `assets/gates.template.yaml` as `gates.yaml` before the pilot. Record its SHA-256 in the run log. A later edit is a new version, declared in the completion manifest along with the reason. Thresholds chosen after seeing results are not gates.

## What to compare

Use the same prompts, generation budgets, and scoring for all five:

1. the original parent
2. the pruned child before recovery
3. the recovered child
4. the quantized, exported child
5. the strongest existing open model near the child's size

The fifth is the one that decides whether the project was worth it. If an existing model of the same size beats the child on the priority capabilities, the child has lineage and no advantage. Report that outcome as it is.

## What to measure

Quality:

- success on the priority tasks
- regression on general capability
- instruction following and tool-call formatting
- appropriate uncertainty
- behavior in the required languages

Resources, on the deployment hardware at the requested context and concurrency:

- startup time, prefill and decode speed, time to first token, throughput
- file size, peak RAM, and peak accelerator memory
- energy per completed task where telemetry exists, with a note on which hardware the reading covers

Mark each figure as measured or estimated.

## Statistics

- Report sample sizes with every score.
- Give an interval, such as a bootstrap confidence interval, and compare systems on the same items.
- A difference smaller than its interval is not a difference.
- Note contamination. The parent may have seen public benchmarks in training, and the child inherits that.

## Judges

Prefer checks a program can make: tests for code, exact or numeric match for mathematics, schema validation for structured output. Where a model has to judge, do not use the parent alone to grade its own child.

## Iteration

Use development failures to decide what to change. Open the sealed test only for a candidate you have frozen, and only once.
