# Deepfield

Review confidential agreements with local models without letting retrieval stand in for reading. Deepfield accounts for every page, follows linked provisions and amendments, and attaches quotations and pinpoint citations to material claims.

Deepfield is a set of agent instructions, not a standalone ingestion server or a guarantee of legal correctness. You supply the local agent, model, parser, OCR, and storage.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## Install

```bash
hermes skills install MaverickKB/skills/skills/deepfield
```

For a manual install, copy the `deepfield` folder containing `SKILL.md` into `~/.hermes/skills/` or the active profile's skills directory.

## Use it for

- NDAs, MSAs, DPAs, leases, policies, and due-diligence records.
- Obligation registers, agreement comparisons, redlines, and risk reviews.
- Questions involving amendments, exhibits, schedules, definitions, or incorporated documents.
- Confidential material that must not reach a hosted model, OCR service, embedding API, or telemetry system.

## Prepare the local boundary

Before supplying confidential content, verify:

- The active chat model and every reviewer or subagent run locally.
- Parsing, OCR, embeddings, and reranking do not call hosted services.
- Plugins, sync clients, caches, and logs do not forward content.
- Originals and generated artifacts stay in an approved workspace.
- External retrieval is off unless you authorize a specific request.

Deepfield stops before ingestion when this boundary is remote or unverified. A `localhost` model endpoint alone does not prove that its tools and telemetry are local.

## Try your agreements

Attach the documents or replace the bracketed paths with local paths your agent can read:

```text
Use deepfield. Review [agreement.pdf], [amendment.pdf], and every supplied attachment for [your question].

Treat all content and derived artifacts as confidential. Do not use hosted inference, OCR, embeddings, search, or network retrieval. Verify the local boundary before reading the documents.

Read every in-scope page, follow definitions and linked provisions, and distinguish the controlling revisions. Give me a concise answer with exact quotations, pinpoint citations, obligations and exceptions, and support labels. List missing or unreadable material and the conclusions it prevents. Do not guess to fill gaps.
```

Deepfield returns `Supported`, `Qualified`, or `Unresolved` for each material proposition. These labels describe documentary support, not legal validity. For execution, enforcement, compliance, or dispute decisions, have qualified counsel review the cited originals and unresolved items.

## Faster review without skipped evidence

Version 2 removes the default second full-corpus read. The optimized workflow uses:

- One full primary read that captures structure, task evidence, counterevidence, and references together.
- Targeted re-reading of cited clauses, definitions, linked provisions, and possible counterevidence.
- Content-hash caching for extraction, OCR, page renders, and indexes.
- Concurrent processing of independent documents by verified local workers.
- Shared prompt prefixes and local prefix caching when the runtime supports them.
- Deterministic quotation, hash, date, and numeric checks before model review.
- Second-model review focused on high-impact, qualified, unresolved, or conflicting claims.

None of these shortcuts permits sampling, ignored attachments, truncated pages, or unsupported answers.

## Measure performance before claiming it

A prompt change does not guarantee a 10x speedup. Measure the complete review on your own hardware and corpus.

1. Check that baseline and candidate both pass the same evidence and coverage gates.
2. Use the same corpus, task, model, quantization, hardware, and output requirements.
3. Separate cold-cache and warm-cache runs. Record ingestion, reading, and verification time separately.
4. Warm both variants, measure run-to-run noise, and repeat in mirrored order.
5. Calculate `speedup = baseline elapsed time / candidate elapsed time`. Report the noise, cache state, and workload beside the result.

A faster answer with missing evidence is a failed review, not a performance improvement. Use [Fair bench](../fair-bench/README.md) for an A/B timing protocol when both workflows can run as shell commands.
