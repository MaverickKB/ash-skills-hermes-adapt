---
name: model-bringup
description: Brings up a new open-weight LLM architecture or checkpoint from scratch inside a native inference engine (Metal, CUDA, Zig, C++, Rust) with layer-by-layer FP64 parity, KV memory fit, and offline SHA-256 token receipt verification. Ships metadata-only checkpoint inspector (`inspect_checkpoint.py`) and offline receipt verifier (`verify_receipts.py`). Use when adding a new model family or checkpoint to an inference runtime, mapping safetensors weight layouts and KV cache budgets, or gating bring-up exactness across cold, cached, drafted and concurrent serving.
metadata:
  version: "1.0.0"
  hermes:
    tags: [tensorfold, checkpoints, kv-cache, model-loading]
    related_skills: [kernel-contract, prefill-and-kv]
---

# Model bring-up

Bringing up a new model family in a native engine fails when you wire the whole forward pass first and stare at garbled text at the end. Build from the checkpoint's metadata and mathematical definition upward, locking exact numerical parity at every layer boundary before tuning a single kernel.

## Ground rules

1. **Clean room.** Build from `config.json`, the safetensors index, the model's official definition and your own engine code. Never copy or translate another inference engine's source.
2. **Exactness before speed.** Get a clear, verifiable scalar or reference path matching token-for-token before writing fused or wide kernels. Keep the scalar/reference path intact as an ongoing oracle.
3. **Separate outcomes.** An isolated bring-up pass, a serving cutover and a public benchmark release each need their own receipts and maintainer approval.
4. **Full hashes, never eyeballs.** Compare full 64-hex `token_sha256` digests (and logit/state bytes when changing cache or scheduling), never approximate text similarity.

## Step 1: Audit the checkpoint contract (metadata only)

Run `scripts/inspect_checkpoint.py` before writing any loader or allocator code. It reads `config.json`, the shard index when present, and safetensors headers. For indexed checkpoints it checks every required shard and its assigned tensor names before reporting memory use. It counts only shards named by the index and checks byte ranges against file sizes without loading weight data. For an unindexed numbered set it checks that all numbered parts are present.

```bash
python3 scripts/inspect_checkpoint.py /path/to/checkpoint --contexts 4096,32768,131072 --streams 1,8,32
```

Confirm from the output:

- exact layer count, hidden/intermediate dims, Q/KV head counts (GQA ratio or MLA `kv_lora_rank` + `qk_rope_head_dim`), MoE expert counts (`num_experts`, `num_experts_per_tok`), RoPE theta/scaling and tied-embedding flag
- dtype histogram, quantization packing metadata and continuous layer indices (zero missing layers)
- total weights + KV cache GiB against your target device's physical memory and working-set allowance before allocating buffers

## Step 2: Climb the parity ladder

Never debug the full stack until the layer below it passes:

1. **Tokenizer and template.** Confirm exact token IDs on special tokens, BOS/EOS, whitespace, Unicode, tool-call tags and thinking tags.
2. **Single primitives against FP64 reference.** Check embedding lookup, norm (RMSNorm/LayerNorm epsilon and cast order), RoPE frequencies and pairing layout (interleaved vs split-half), QKV projection, attention/MLA/recurrence, MLP activation/gating and MoE router top-k tie breaking.
3. **Layer 0 to full logits.** Compare layer-0 hidden states and final outputs on a 1-token and multi-token prompt before running generation loops.
4. **Serving invariance matrix.** Verify identical `token_sha256` across:
   - greedy and fixed-seed sampling
   - cold prefill vs chunked prefill vs prefix-cached/resumed continuation (using an empty cache store, since a global cache-off flag can take a different prefill grid)
   - drafted/speculative vs plain serial (`"draft": false`)
   - concurrent burst streams (distinct prompt per stream) vs solo reference

## Step 3: Verify receipts offline (`scripts/verify_receipts.py`)

```bash
python3 scripts/verify_receipts.py paired control.json candidate.json
python3 scripts/verify_receipts.py bursts decode_bursts.json
```

Every reply must carry a full 64-hex `token_sha256` field (`text_sha256` and prompt/completion token counts are checked when present on both sides). Exit codes: `0` every reply matched, `1` mismatch, `2` invalid receipt (missing/truncated hash, mismatched fixture set, or incomplete burst).

## Closing the bring-up

Hand off kernel optimization to `kernel-contract`, single-stream gap work to `one-stream-lanes`, cold/warm prefill and KV pools to `prefill-and-kv`, concurrent scheduling to `serving-concurrency`, and full request accounting to the TensorFold performance playbook in `mythos-mode`. Keep raw logs, binary hashes and token hashes in a notes folder beside the checkout, never inside the repository tree or `/tmp`.
