---
name: output-parity
description: Checks whether a change to an LLM inference server changed what the model outputs. Records one build and compares a later one, or compares two running servers, on any OpenAI-compatible chat endpoint. Compares reply text, finish reason, token count and token sequence for every prompt, temperature and seed, and first runs controls that show the comparison could have failed. Use after any change to an inference engine, server, kernel, quantization path, cache or speculative decoding setup that is supposed to keep outputs the same, before claiming a speedup is exact, and when asked whether two builds, two flags or two servers give the same output. Includes a tested script that uses only the Python standard library.
compatibility: The bundled script needs Python 3.9 or later and uses only the standard library. The servers must expose an OpenAI-compatible chat completions endpoint.
---

# Output parity

A change that is meant to keep the model's output the same has to be shown to keep it. This skill answers one question: did the change alter what the model outputs? It does not say whether the change is faster.

The bundled script sends the same chat requests to a base and a candidate and compares the replies. Before it compares, it shows that the comparison could have failed. A comparison that cannot fail proves nothing.

## When to use it

- After a change to an inference engine or server that should not change output: a new kernel, a batching change, a quantization path, a prompt cache, speculative decoding.
- Before you or anyone else calls a speedup exact, lossless or identical.
- When two builds, two flags or two machines are supposed to give the same answer.

Do not use it to judge quality. If the change is allowed to alter output, the project needs a quality gate that was fixed before the run, and this skill is the wrong test.

## Before you run

Fix these first and write them down. Do not change them after you see a result.

1. The prompt set. Start from `assets/prompts.example.jsonl` and add prompts like the real traffic, short and long, prose and code. Each line is one JSON object, `{"id": "...", "prompt": "..."}` or `{"id": "...", "messages": [...]}`.
2. The seeds and temperatures. The defaults are seeds 0 and 1 and temperatures 0 and 0.8, so every prompt is checked greedy and sampled.
3. The token limit. The default is 256. Raise it if the change only acts late in a reply.

## Three ways to run it

The script is `scripts/parity.py`. A URL is the API base, such as `http://localhost:8000/v1`.

**Record, then compare.** Use this when only one build fits on the GPU at a time.

```bash
python3 scripts/parity.py record --url http://localhost:8000/v1 --model my-model \
    --prompts prompts.jsonl --out base.json
# stop the base build and start the candidate build
python3 scripts/parity.py compare --url http://localhost:8000/v1 --model my-model --against base.json
```

`compare` replays the recorded requests. It refuses to run when the model name, the prompt set or a request setting differs from the recording, because comparing different requests is meaningless. `record` does not replace an existing recording unless you pass `--overwrite`.

**Live, two servers.** Use this when both builds are up at once.

```bash
python3 scripts/parity.py live --base-url http://localhost:8000/v1 --candidate-url http://localhost:8001/v1 \
    --model my-model --prompts prompts.jsonl
```

**Live, one server, two settings.** Use this when a request field turns the change on and off. The field below is made up. Use your server's own.

```bash
python3 scripts/parity.py live --base-url http://localhost:8000/v1 --candidate-url http://localhost:8000/v1 \
    --model my-model --prompts prompts.jsonl \
    --base-extra-json '{"speculative": false}' --candidate-extra-json '{"speculative": true}'
```

Other options: `--seeds 0,1`, `--temperatures 0,0.8`, `--max-tokens 256`, `--extra-json` for fields both arms send, `--logprobs` to compare token sequences, `--api-key-env NAME` to read a key from an environment variable, `--timeout`, `--progress`, and `--json OUT` for the full result. Run `python3 scripts/parity.py --help` for the rest.

## What it checks

A cell is one prompt at one temperature with one seed. For every cell the script compares:

- the exact reply text, and the reasoning text when the server returns it
- the finish reason
- the completion token count, when both sides report it
- the token sequence, when `--logprobs` is on and both sides return token lists

When both sides supply raw bytes at a token position, the comparison checks those bytes at that position as well as the displayed token label. The report counts positions with byte coverage. Version 2 recordings retain per-token bytes for replay. Version 1 recordings remain readable and provide label-only token comparisons.

For a mismatch it prints the prompt id, the temperature, the seed, the index of the first differing character or token counted from 0, and a short excerpt of both sides.

## The controls

The script runs three controls on the base before it compares. It sends every base request twice to do so. The results are stored in the recording, so `compare` shows them too.

| Control | Must | Why |
| --- | --- | --- |
| 1. Same seed twice, sampled | match | If the base disagrees with itself, a difference from the candidate means nothing. The script reports the rate of nondeterminism. |
| 2. Different seeds, sampled | differ on at least one prompt | If every reply is the same, the seed or the temperature has no effect, and the sampled comparison proves nothing. |
| 3. Greedy twice | match | The same reason as control 1, at temperature 0. |

The script also checks that the base replies are not all empty. A failed control does not fail the candidate. It means the cells that control covers cannot be judged.

## Reading the result

The last line is the verdict and the exit code matches it.

| Last line | Exit | Meaning |
| --- | --- | --- |
| `PARITY: IDENTICAL (N of N)` | 0 | Every cell matched and every control passed. |
| `PARITY: DIFFERENT (K of N differ)` | 1 | K of the N cells that could be judged differ. The change alters output. |
| `PARITY: INCONCLUSIVE (reason)` | 3 | A control failed. The cells it covers were not judged, and the others matched. |
| one line on standard error | 2 | A usage or endpoint error. Nothing was judged. |

`record` ends with `PARITY: RECORDED` and exit 0, or with `PARITY: INCONCLUSIVE` and exit 3 when a control failed on the base.

When control 1 or 3 fails, find the cause before you move on. The repeat is the second time the server sees each request, so a cache that changes output can show up here as well as a sampler that ignores the seed.

## Which cells to cover

A change can keep output the same in one mode and break it in another. Cover each of these, and say which ones you did not cover.

| Cell | Why | How |
| --- | --- | --- |
| Greedy and seeded sampling | Sampling runs code that greedy decoding skips. | The default temperatures and seeds. |
| Short and long prompts | Long inputs can take other paths and other kernels. | Put both in the prompt file. |
| Speculative against plain decode | Drafted tokens must equal the plain decode. | Per-arm extra fields, or record with it off and compare with it on. |
| A resumed or cached prompt against a fresh one | A cache hit skips work that a fresh prompt does. | Base with the cache off and candidate with it on, by a server flag or a per-arm field. Add a prompt whose messages extend an earlier prompt. |
| A request beside others against alone | Batching changes shapes and can change arithmetic. | Record on an idle server, then run `compare` while another client keeps the server busy with other prompts. |

The script covers the first two rows directly. It sends one request at a time, so the other three depend on how you start the servers, what you put in the extra fields, and what else is running. With a cache on, only the first cell of each prompt is fresh, because each prompt is sent once per temperature and seed.

## Limits

- Text equality is weaker than token equality. Two token sequences can spell the same text. Use `--logprobs` when the server returns token lists.
- A server that does not honour seeds cannot be judged on sampled output. Its greedy cells can still be judged.
- Identical output on these prompts is evidence. It is not proof for all inputs. Report the prompt count and the cells covered beside the verdict.
- Tool calls are not compared.
- A response cache in front of the server replays old replies and hides a change. Bypass it for both arms.
- A small numeric change can flip a near tie between two tokens. That is a real difference in output and the script reports it as one.

## Rules for the agent

1. Fix the prompt set, seeds, temperatures and token limit before the first run. Do not change them to get a better result.
2. Never drop a prompt that mismatches. The mismatch is the finding. Report its id, temperature and seed.
3. Report INCONCLUSIVE as inconclusive. It is not a pass and it is not a fail.
4. Do not rerun in the hope of an identical result. If a rerun gives another answer, report both runs.
5. Quote the last line as printed, with the control results, the prompt count and the cells you covered. Keep the recording and the `--json` result as evidence.
6. Exit 2 means nothing was compared. Fix the request or the endpoint, and do not report a verdict.

## Related skills

When they are available, `fair-bench` answers whether the change is faster and `speedup-referee` referees a speed claim someone else made. Check parity first. A faster build with different output is a different model.
