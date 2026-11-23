---
name: inference-speed-hunt
description: Finds and proves real speedups in an LLM inference engine by accounting for where a whole served request spends its time and removing the waste, across prompt processing, time to first token, decode, and concurrent throughput. Keeps the model's output identical unless the project owner has said otherwise. Use for open-ended performance work on an inference server or runtime, such as profiling a request, asking where the time goes, kernel, dispatch, batching or memory-layout work, a regression hunt, or a full performance review. To check a win someone already claims, use speedup-referee instead.
metadata:
  version: "1.0.0"
---

# Inference speed hunt

Find the biggest speedup the engine can actually deliver. Start with a real served request, account for where its time goes, and keep hunting after the first win.

Every implementation choice is open, including replacing an execution path. Question kernel boundaries, memory layouts, dispatch, batching, cache ownership, and whether the CPU or the GPU does the work. Three things are fixed unless the project owner says otherwise: the model's arithmetic, its precision and quantization, and its output, token for token.

This skill is a method. It does not know your engine. Read the project's own contributor guide, performance notes and benchmark tools first, and where they are stricter than this file, they win.

## Ground rules

1. **Same output.** A speed change that changes tokens is a different model. Greedy output and seeded sampled output equal the base build. Speculative decode equals plain decode, a resumed prompt equals a fresh one, and a request beside others equals the same request alone. If your best idea needs different arithmetic, stop and bring the owner the decision with the evidence.
2. **Read the record, then predict.** Before you build, search the project's performance notes, changelog and issues for the idea. Write the gain you expect and the range you would believe. A result outside the range means debug the measurement first. If the record already answers the question, do not rerun it.
3. **Bound the change, never the goal.** Work out the most a change can win before you build it, from the time it could remove on the request's critical path, and skip it if that is under the bar. A bound is a working number for choosing what to build. Do not conclude that a target is out of reach. A loss tells you about one mechanism on one cell.
4. **Served or it did not happen.** A kernel timed alone shows what is possible. A change is measured when a real request on the default path runs it and gets faster.
5. **Your own code.** Do not copy or translate another engine's source. Ideas are fine. What lands is written from this project's code and the model's own definition, under a licence the project accepts.
6. **Inside your authority.** Work on a branch. Get the owner's word before you push, open pull requests, merge, publish, change a shared machine or stop someone else's workload. Find out which machine you may use and how it is locked before the first GPU run, and run one model process per machine.

## 1. Account for the whole request

Baseline real serving workloads with the project's own benchmark clients. Record the commit and a hash of the binary, the hardware and memory, OS and toolchain, driver and runtime versions, model revision, quantization, precision, prompt and context lengths, concurrency, and every speculative-decode and cache setting. Use short and long prompts, plain and speculative decode, and solo and concurrent requests.

Include the path real requests take. Servers often sample by default, and a request that leaves its token limit at the default can take a different code path from a short benchmark. Measure a default-settings cell beside the pinned ones.

Trace one request from the API to GPU completion. Account for CPU work, GPU execution, copies, waits, allocations and idle gaps along its critical path. Take the kernel that ran from the trace or the log, never from reading the dispatch code.

Look for the engine's own timing logs before you write a timing tool. Check how your profiler behaves before you attach it to a process that holds a full model. Some profilers replay work or hold large buffers, and on a machine where the GPU shares system memory that can exhaust it.

Do not size a kernel by skipping it. A knockout changes what the next kernel finds in the cache, so the number is wrong in both directions. Read its time from a trace of the real request, or run it twice on equally cold data.

For every significant kernel, log the shapes it was called with, launches per step, GPU time and share, and bytes moved. Keep measured values apart from estimates.

For speculative decode, split every rate into two parts: tokens kept per pass, and passes per second. A request gets faster through either part, and they have different fixes.

Investigate the gaps large enough to change where you would spend engineering time.

## 2. Find the waste and design ways to remove it

Walk the request stage by stage: tokenization and templating, prompt processing, attention and its cached state, the feed-forward layers, normalization, sampling, speculative proposal and verification, and the scheduling between requests. At each boundary ask what can be removed, reused, overlapped or reorganized. Look for bytes and launches that repeat, hardware left idle, round trips to the host between steps, work repeated across tokens or requests, and shape or layout choices that rule out a better path.

Record what you checked and what the evidence ruled out. Check each idea against the record before you rank it. Retry a recorded loss only if you can name what has changed since.

Design a clean-sheet fast path for the worst bottleneck. Develop at least two distinct combinations that cross layers, each with a mechanism that explains why its pieces work together.

Rank the strongest candidates, up to ten. A short list of strong ones beats a padded ten. Give each one its exact code locations, the evidence, a hypothesis for the root cause, the mechanism, the hardware and models it affects, its risks, and the smallest experiment that could prove it wrong. Bound the end-to-end gain by the critical-path time the change could remove, and count overlapping savings once. Use about 3% of a served cell as the first screen for a standalone win. Keep a smaller change in play when it unlocks a larger combined gain.

## 3. Prove the wins and reprofile

Set pass and fail criteria for the top three experiments before you run them. Prototype the strongest candidate and the strongest combination. Test each change alone where you can, then measure the combination against the same baseline and against the strongest single change. For dependent changes, remove one piece at a time and measure what it contributes.

Check the output first. Compare greedy token hashes and seeded sampling, speculative against plain, resumed against fresh, and solo against concurrent, at every batch width and draft depth you can force. Check edge shapes, tails and non-finite values in any kernel you touched. Run the project's kernel tests and confirm each negative control fails.

Then measure so a skeptic would accept it.

- Run base against base first. That spread is your noise.
- Run one unscored warm-up, then base, candidate, candidate, base, on a fresh server for each arm. The first run of a job is often the fastest, and it flatters whichever arm gets it.
- Use cache-off settings and fresh state for cold prompt processing.
- Give every stream its own prompt in concurrent cells. Identical streams can run in step and swing widely between repeats.
- Compare tokens per second only between builds with identical tokens.
- Track clock speed, temperature, power state and other load. Drop rounds where the machine throttled.
- Report cold prompt processing, time to first token, plain and speculative decode, acceptance, single-request latency, concurrent throughput, and memory where it matters.
- Save commands, raw logs, traces, binary hashes and token hashes somewhere that survives a reboot.
- Limit every claim to the hardware and model revisions you tested.

Keep the changes that pass. No served cell may get slower, even from a change whose output is identical. Log failed experiments in the project's own record and revert them cleanly. Reprofile after every verified win, find the bottleneck it exposes, and test the next idea. Note every other model and backend the win fits.

Continue until the session's budget ends, or a concrete resource or authorization limit stops you. When your candidate list runs out, go back to the accounting and write a new one.

Before you call a win proven, hand it to `speedup-referee` in a fresh session.

## 4. Leave reproducible results

Write the report in a notes folder beside the checkout, or wherever your brief says, never inside the repository tree. Include the critical-path breakdown, coverage and gaps, the ranked candidates, the top three experiment plans, standalone and combined measurements, output checks, changes kept or reverted, and the next most valuable experiment. If hardware was unavailable, finish the source-backed work and give the exact commands that would collect the missing evidence.

Keep the closing message short. For each cell give the rate before and after, with its two parts for speculative decode, then what was kept, what lost and what comes next.

## Sibling inference-engine skills

- Bringing up a new model family or verifying offline token-hash receipts: `model-bringup`
- Writing or gating a native Metal, CUDA or CPU kernel against FP64/ULP bounds and tail shapes: `kernel-contract`
- Closing the gap between one serial stream and N concurrent streams in lanes: `one-stream-lanes`
- Pushing cold prefill, warm prefix caching, paged KV pools, spill and direct-IO loading: `prefill-and-kv`
- Designing and auditing continuous batching, lane caps, TTFT knees and multi-device scaling: `serving-concurrency`
- Independently auditing a claimed speedup in a fresh session: `speedup-referee`
