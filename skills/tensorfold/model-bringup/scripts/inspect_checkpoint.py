#!/usr/bin/env python3
"""Inspect an open-weight model checkpoint from metadata alone for engine bring-up.

Reads `config.json` and safetensors headers (never loading weight payloads) to
produce an engine bring-up contract: architecture dimensions, GQA/MLA/MoE shape,
dtype and byte breakdown, layer-index continuity check, and KV-cache memory fit
across context lengths and concurrent stream counts.

Exit codes: 0 success, 2 missing/invalid checkpoint metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Dict, List, Optional, Sequence, Set, Tuple

DTYPE_BYTES = {
    "F64": 8,
    "I64": 8,
    "U64": 8,
    "F32": 4,
    "I32": 4,
    "U32": 4,
    "F16": 2,
    "BF16": 2,
    "I16": 2,
    "U16": 2,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "I8": 1,
    "U8": 1,
    "BOOL": 1,
}
LAYER_IDX_RE = re.compile(r"(?:^|\.)(?:layers|h|blocks)\.(\d+)\.")
NUMBERED_SHARD_RE = re.compile(r"(.+)-(\d{5})-of-(\d{5})\.safetensors\Z")


def read_safetensors_header(path: str) -> Dict[str, object]:
    with open(path, "rb") as handle:
        raw_len = handle.read(8)
        if len(raw_len) < 8:
            raise ValueError(f"truncated safetensors header in {path}")
        header_len = struct.unpack("<Q", raw_len)[0]
        if header_len > 256 * 1024 * 1024:
            raise ValueError(f"implausible header size {header_len} in {path}")
        raw_json = handle.read(header_len)
        if len(raw_json) < header_len:
            raise ValueError(f"truncated safetensors JSON in {path}")
        payload_size = os.fstat(handle.fileno()).st_size - handle.tell()
    header = json.loads(raw_json.decode("utf-8"))
    if not isinstance(header, dict):
        raise ValueError(f"{path}: expected a safetensors header object")
    for name, tensor in header.items():
        if name == "__metadata__":
            continue
        offsets = tensor.get("data_offsets") if isinstance(tensor, dict) else None
        if (not isinstance(offsets, list) or len(offsets) != 2 or
                not all(type(offset) is int for offset in offsets) or not 0 <= offsets[0] <= offsets[1] <= payload_size):
            raise ValueError(f"{path}: invalid or truncated data range for tensor {name!r}")
    return header


def shard_inventory(root: Path) -> Dict[str, Optional[Set[str]]]:
    index_path = root / "model.safetensors.index.json"
    if not index_path.is_file():
        indices = sorted(set(root.glob("*.safetensors.index.json")) | set(root.glob("*.safetensors.index.*.json")))
        if len(indices) > 1:
            raise ValueError("multiple safetensors indices without a default model.safetensors.index.json")
        if not indices:
            return unindexed_shards(root)
        index_path = indices[0]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    mapping = index.get("weight_map") if isinstance(index, dict) else None
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError(f"{index_path.name}: expected a non-empty weight_map")

    shards: Dict[str, Set[str]] = {}
    for name, shard in mapping.items():
        if (not name or name == "__metadata__" or not isinstance(shard, str) or not shard.endswith(".safetensors")
                or Path(shard).is_absolute() or PureWindowsPath(shard).drive or "\\" in shard or ".." in Path(shard).parts):
            raise ValueError(f"{index_path.name}: invalid tensor or shard entry for {name!r}")
        shards.setdefault(Path(shard).as_posix(), set()).add(name)
    for shard in shards:
        if not (root / shard).is_file():
            raise ValueError(f"{index_path.name}: required shard {shard!r} is missing")
    return {shard: names for shard, names in sorted(shards.items())}


def unindexed_shards(root: Path) -> Dict[str, Optional[Set[str]]]:
    files = sorted(p.name for p in root.glob("*.safetensors") if p.is_file())
    if not files:
        raise ValueError(f"{root}: no safetensors weight files")
    groups: Dict[str, Tuple[int, Set[int]]] = {}
    for name in files:
        match = NUMBERED_SHARD_RE.fullmatch(name)
        if match:
            prefix, part, count = match.groups()
            total, parts = groups.setdefault(prefix, (int(count), set()))
            if total != int(count) or not 1 <= int(part) <= total:
                raise ValueError(f"inconsistent shard number in {name!r}")
            parts.add(int(part))
    for prefix, (total, parts) in groups.items():
        if len(parts) != total:
            raise ValueError(f"incomplete {prefix} shard set: found {len(parts)} of {total}")
    return dict.fromkeys(files)


def scan_safetensors(folder: str) -> Dict[str, object]:
    shards = shard_inventory(Path(folder))
    dtypes: Counter = Counter()
    dtype_bytes: Counter = Counter()
    total_tensors = 0
    total_bytes = 0
    layer_indices: Set[int] = set()
    sample_names: List[str] = []

    for fname, expected in shards.items():
        header = read_safetensors_header(os.path.join(folder, fname))
        if expected is not None:
            present = set(header) - {"__metadata__"}
            if present != expected:
                missing, extra = sorted(expected - present), sorted(present - expected)
                raise ValueError(f"{fname}: tensor inventory differs from index (missing {missing[:5]}, unexpected {extra[:5]})")
        for name, meta in header.items():
            if name == "__metadata__" or not isinstance(meta, dict):
                continue
            total_tensors += 1
            if len(sample_names) < 8:
                sample_names.append(name)
            dtype = str(meta.get("dtype", "UNKNOWN"))
            begin, end = meta["data_offsets"]
            nbytes = end - begin
            dtypes[dtype] += 1
            dtype_bytes[dtype] += nbytes
            total_bytes += nbytes
            m = LAYER_IDX_RE.search(name)
            if m:
                layer_indices.add(int(m.group(1)))

    return {
        "files": len(shards),
        "tensors": total_tensors,
        "total_bytes": total_bytes,
        "total_gib": round(total_bytes / (1024**3), 3),
        "dtypes": {k: {"tensors": dtypes[k], "gib": round(dtype_bytes[k] / (1024**3), 3)} for k in sorted(dtypes)},
        "layer_indices_found": sorted(layer_indices),
        "sample_tensors": sample_names,
    }


def extract_arch(cfg: Dict[str, object]) -> Dict[str, object]:
    sub = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
    layers = int(sub.get("num_hidden_layers") or sub.get("n_layer") or 0)
    hidden = int(sub.get("hidden_size") or sub.get("n_embd") or 0)
    n_heads = int(sub.get("num_attention_heads") or sub.get("n_head") or 0)
    n_kv = int(sub.get("num_key_value_heads") or n_heads)
    head_dim = int(sub.get("head_dim") or (hidden // n_heads if n_heads else 0))
    kv_lora_rank = int(sub.get("kv_lora_rank") or 0)
    qk_rope_dim = int(sub.get("qk_rope_head_dim") or 0)
    experts = int(sub.get("num_local_experts") or sub.get("n_routed_experts") or sub.get("num_experts") or 0)
    active_experts = int(sub.get("num_experts_per_tok") or 0)
    return {
        "architectures": cfg.get("architectures") or sub.get("architectures") or [],
        "model_type": sub.get("model_type") or cfg.get("model_type") or "unknown",
        "layers": layers,
        "hidden_size": hidden,
        "intermediate_size": int(sub.get("intermediate_size") or 0),
        "vocab_size": int(sub.get("vocab_size") or 0),
        "num_attention_heads": n_heads,
        "num_key_value_heads": n_kv,
        "head_dim": head_dim,
        "kv_lora_rank": kv_lora_rank,
        "qk_rope_head_dim": qk_rope_dim,
        "num_experts": experts,
        "num_experts_per_tok": active_experts,
        "tie_word_embeddings": bool(sub.get("tie_word_embeddings", False)),
        "rope_theta": sub.get("rope_theta"),
        "quantization_config": cfg.get("quantization_config") or sub.get("quantization_config"),
    }


def kv_bytes_per_token(arch: Dict[str, object], kv_elem_bytes: int) -> int:
    layers = int(arch["layers"])
    if layers <= 0:
        return 0
    if int(arch["kv_lora_rank"]) > 0:
        return layers * (int(arch["kv_lora_rank"]) + int(arch["qk_rope_head_dim"])) * kv_elem_bytes
    return layers * 2 * int(arch["num_key_value_heads"]) * int(arch["head_dim"]) * kv_elem_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("checkpoint", help="path to checkpoint directory containing config.json")
    parser.add_argument("--contexts", default="4096,32768,131072", help="comma-separated context lengths (default: 4096,32768,131072)")
    parser.add_argument("--streams", default="1,8,32", help="comma-separated stream counts (default: 1,8,32)")
    parser.add_argument("--kv-bytes", type=int, default=2, help="bytes per KV element (default: 2 for FP16/BF16)")
    parser.add_argument("--json", dest="json_out", help="write structured bring-up contract to JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    folder = os.path.abspath(args.checkpoint)
    cfg_path = os.path.join(folder, "config.json")
    if not os.path.isfile(cfg_path):
        print(f"inspect_checkpoint.py: error: config.json not found in {folder}", file=sys.stderr)
        return 2

    try:
        contexts = [int(x.strip()) for x in args.contexts.split(",") if x.strip()]
        streams = [int(x.strip()) for x in args.streams.split(",") if x.strip()]
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        arch = extract_arch(cfg)
        weights = scan_safetensors(folder)
    except (OSError, ValueError, KeyError) as exc:
        print(f"inspect_checkpoint.py: error: {exc}", file=sys.stderr)
        return 2

    kv_bpt = kv_bytes_per_token(arch, args.kv_bytes)
    expected_layers = list(range(int(arch["layers"])))
    found_layers = weights["layer_indices_found"]
    missing_layers = sorted(set(expected_layers) - set(found_layers)) if found_layers else []

    fit_table: List[Dict[str, object]] = []
    weight_gib = float(weights["total_gib"])
    for ctx in contexts:
        for st in streams:
            kv_gib = (kv_bpt * ctx * st) / (1024**3)
            fit_table.append({
                "context": ctx,
                "streams": st,
                "kv_gib": round(kv_gib, 3),
                "weights_plus_kv_gib": round(weight_gib + kv_gib, 3),
            })

    report = {
        "checkpoint": folder,
        "architecture": arch,
        "weights": weights,
        "kv_bytes_per_token": kv_bpt,
        "missing_layer_indices": missing_layers,
        "memory_fit": fit_table,
    }

    print(f"model: {arch['model_type']} {arch['architectures']}")
    print(
        f"shape: layers={arch['layers']} hidden={arch['hidden_size']} heads={arch['num_attention_heads']} "
        f"kv_heads={arch['num_key_value_heads']} head_dim={arch['head_dim']} vocab={arch['vocab_size']}"
    )
    if arch["kv_lora_rank"]:
        print(f"mla: kv_lora_rank={arch['kv_lora_rank']} qk_rope_head_dim={arch['qk_rope_head_dim']}")
    if arch["num_experts"]:
        print(f"moe: experts={arch['num_experts']} active_per_tok={arch['num_experts_per_tok']}")
    print(f"weights: {weights['files']} safetensors file(s), {weights['tensors']} tensors, {weight_gib:.3f} GiB")
    for dt, info in weights["dtypes"].items():
        print(f"  {dt:<10} {info['tensors']:>5} tensors  {info['gib']:.3f} GiB")
    if missing_layers:
        print(f"WARNING: missing layer indices in safetensors: {missing_layers}")
    print(f"kv cache: {kv_bpt:,} bytes/token ({args.kv_bytes}B/elem)")
    for row in fit_table:
        print(f"  ctx={row['context']:>6}  streams={row['streams']:>2}  kv={row['kv_gib']:>6.3f} GiB  total={row['weights_plus_kv_gib']:>7.3f} GiB")

    if args.json_out:
        json.dump(report, open(args.json_out, "w", encoding="utf-8"), indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
