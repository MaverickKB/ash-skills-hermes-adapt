#!/usr/bin/env python3
"""Solve TensorFold memory fit, quant selection, KV spill and launch flags.

Probes local hardware plus `tensorfold capabilities --json`, inspects remote
Hugging Face checkpoints (`config.json` and shard trees) before running
`tensorfold pull`, and computes exact VRAM / unified-memory fit across every
TensorFold quant format (`bf16`, `modelopt-fp8`, `fp8-block128`, `exlr8`,
`modelopt-nvfp4`, and `mlx-q8` down through `mlx-q2` group quants). Uses only
the Python 3.9+ standard library.

Subcommands:
  probe         Inspect local GPUs, unified memory, host RAM and the local
                `tensorfold` binary (`--version` and `capabilities --json`).
  inspect-repo  Inspect a remote Hugging Face checkpoint's config.json and
                safetensors shard sizes without downloading weight payloads.
  solve         Compute memory fit across TensorFold quant tiers, `--kv-cache`
                (`bf16` vs `fp8`), `--spill-gib`, `--ssd-experts` and
                `--parallel`, emitting ready-to-run `tensorfold serve` and
                `tensorfold benchmark` commands.

Exit codes: 0 fit/success, 1 no quant fits budget, 2 usage or fetch/parse error.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

USER_AGENT = "tensorfold-vram-alchemist/1"
GIB = 1024**3

# TensorFold native quant tiers: effective whole-checkpoint bits per weight
# (including unquantized norms, routers, embeddings and scale metadata).
QUANT_TIERS: List[Dict[str, object]] = [
    {"name": "bf16", "bpw": 16.00, "family": "fp16", "tier": "reference"},
    {"name": "mlx-q8g32", "bpw": 9.00, "family": "mlx", "tier": "lossless-tier"},
    {"name": "mlx-q8g64", "bpw": 8.50, "family": "mlx", "tier": "lossless-tier"},
    {"name": "mlx-q8g128", "bpw": 8.25, "family": "mlx", "tier": "lossless-tier"},
    {"name": "exlr8", "bpw": 8.25, "family": "exlr", "tier": "lossless-tier"},
    {"name": "modelopt-fp8", "bpw": 8.20, "family": "fp8", "tier": "lossless-tier"},
    {"name": "fp8-block128", "bpw": 8.20, "family": "fp8", "tier": "lossless-tier"},
    {"name": "mlx-q6g64", "bpw": 6.50, "family": "mlx", "tier": "near-lossless"},
    {"name": "mlx-q6g128", "bpw": 6.25, "family": "mlx", "tier": "near-lossless"},
    {"name": "mlx-q5g64", "bpw": 5.50, "family": "mlx", "tier": "sweet-spot"},
    {"name": "mlx-q5g128", "bpw": 5.25, "family": "mlx", "tier": "sweet-spot"},
    {"name": "mlx-q4g32", "bpw": 5.00, "family": "mlx", "tier": "sweet-spot"},
    {"name": "modelopt-nvfp4", "bpw": 4.55, "family": "nvfp4", "tier": "sweet-spot"},
    {"name": "mlx-q4g64", "bpw": 4.50, "family": "mlx", "tier": "sweet-spot"},
    {"name": "mlx-q4g128", "bpw": 4.25, "family": "mlx", "tier": "compact"},
    {"name": "mlx-q3g64", "bpw": 3.50, "family": "mlx", "tier": "tight"},
    {"name": "mlx-q3g128", "bpw": 3.25, "family": "mlx", "tier": "tight"},
    {"name": "mlx-q2g64", "bpw": 2.50, "family": "mlx", "tier": "last-resort"},
    {"name": "mlx-q2g128", "bpw": 2.25, "family": "mlx", "tier": "last-resort"},
]

KV_BYTES_PER_ELEM: Dict[str, float] = {
    "bf16": 2.0,
    "fp8": 1.0,
}


class Fail(Exception):
    """Usage, fetch or parse failure -> exit 2."""


def http_get_json(url: str, timeout: float = 30.0) -> object:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token and "huggingface.co" in urllib.parse.urlparse(url).netloc:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Fail(f"HTTP fetch failed for {url}: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Fail(f"invalid JSON from {url}: {exc}") from exc


def normalize_hf_repo(repo_or_url: str) -> str:
    repo = repo_or_url.strip().strip("/")
    if repo.startswith(("http://", "https://")):
        parts = urllib.parse.urlparse(repo).path.strip("/").split("/")
        if len(parts) < 2:
            raise Fail(f"could not extract org/repo from URL {repo_or_url!r}")
        repo = f"{parts[0]}/{parts[1]}"
    if "/" not in repo:
        raise Fail(f"expected Hugging Face repo 'org/name', got {repo_or_url!r}")
    return repo


def detect_quant_from_config(cfg: Dict[str, object]) -> str:
    qcfg = cfg.get("quantization") or cfg.get("quantization_config")
    if isinstance(qcfg, dict):
        qmethod = str(qcfg.get("quant_method") or qcfg.get("quant_algo") or "").lower()
        bits = qcfg.get("bits")
        gsize = qcfg.get("group_size")
        if bits and gsize:
            return f"mlx-q{bits}g{gsize}"
        if "nvfp4" in qmethod:
            return "modelopt-nvfp4"
        if "fp8" in qmethod:
            bsize = qcfg.get("weight_block_size")
            if isinstance(bsize, list) and bsize == [128, 128]:
                return "fp8-block128"
            return "modelopt-fp8"
        if qmethod:
            return qmethod
    dtype = str(cfg.get("torch_dtype") or cfg.get("dtype") or "").lower()
    if "bfloat16" in dtype or dtype == "bf16":
        return "bf16"
    if "float16" in dtype or dtype == "fp16":
        return "bf16"
    return "unknown"


def extract_arch_from_config(cfg: Dict[str, object]) -> Dict[str, object]:
    sub = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
    layers = int(sub.get("num_hidden_layers") or sub.get("n_layer") or 0)
    hidden = int(sub.get("hidden_size") or sub.get("n_embd") or 0)
    heads = int(sub.get("num_attention_heads") or sub.get("n_head") or 0)
    kv_heads = int(sub.get("num_key_value_heads") or heads)
    head_dim = int(sub.get("head_dim") or (hidden // heads if heads else 0))
    inter = int(sub.get("intermediate_size") or 0)
    moe_inter = int(sub.get("moe_intermediate_size") or inter)
    vocab = int(sub.get("vocab_size") or 0)
    experts = int(sub.get("num_local_experts") or sub.get("n_routed_experts") or sub.get("num_experts") or 0)
    active_experts = int(sub.get("num_experts_per_tok") or 0)
    shared_experts = int(sub.get("n_shared_experts") or (1 if sub.get("shared_expert_intermediate_size") else 0))
    shared_inter = int(sub.get("shared_expert_intermediate_size") or (shared_experts * moe_inter))
    kv_lora_rank = int(sub.get("kv_lora_rank") or 0)
    qk_rope_dim = int(sub.get("qk_rope_head_dim") or 0)
    tied = bool(sub.get("tie_word_embeddings", False))
    max_ctx = int(sub.get("max_position_embeddings") or 0)
    sliding_window = int(sub.get("sliding_window") or 0)
    layer_types = sub.get("layer_types") if isinstance(sub.get("layer_types"), list) else None
    full_attn_layers = layers
    swa_layers = 0
    if layer_types:
        full_attn_layers = sum(1 for t in layer_types if "full" in str(t))
        swa_layers = sum(1 for t in layer_types if "sliding" in str(t))
        if full_attn_layers == 0 and swa_layers == 0:
            full_attn_layers = layers

    emb_params = vocab * hidden * (1 if tied else 2)
    q_dim = heads * head_dim
    kv_dim = kv_heads * head_dim
    if kv_lora_rank > 0:
        attn_per_layer = hidden * (kv_lora_rank + qk_rope_dim + q_dim) + q_dim * hidden
    else:
        attn_per_layer = hidden * (q_dim + 2 * kv_dim) + q_dim * hidden
    first_dense = int(sub.get("first_k_dense_replace") or 0)
    moe_layers = max(0, layers - first_dense) if experts > 0 else 0
    dense_layers = layers - moe_layers
    dense_ffn_per_layer = 3 * hidden * inter
    routed_per_layer = (3 * hidden * moe_inter * experts) if experts > 0 else 0
    shared_per_layer = (3 * hidden * shared_inter) if shared_inter > 0 else 0
    router_per_layer = hidden * experts if experts > 0 else 0

    total_params = (
        emb_params
        + layers * (attn_per_layer + 2 * hidden)
        + dense_layers * dense_ffn_per_layer
        + moe_layers * (routed_per_layer + shared_per_layer + router_per_layer)
    )
    active_routed_per_layer = (3 * hidden * moe_inter * active_experts) if experts > 0 else 0
    active_params = (
        emb_params
        + layers * (attn_per_layer + 2 * hidden)
        + dense_layers * dense_ffn_per_layer
        + moe_layers * (active_routed_per_layer + shared_per_layer + router_per_layer)
    )
    routed_fraction = (moe_layers * routed_per_layer / total_params) if total_params > 0 else 0.0

    return {
        "model_type": str(sub.get("model_type") or cfg.get("model_type") or "unknown"),
        "detected_quant": detect_quant_from_config(cfg),
        "layers": layers,
        "full_attn_layers": full_attn_layers,
        "swa_layers": swa_layers,
        "sliding_window": sliding_window,
        "hidden_size": hidden,
        "intermediate_size": inter,
        "moe_intermediate_size": moe_inter,
        "vocab_size": vocab,
        "num_attention_heads": heads,
        "num_key_value_heads": kv_heads,
        "head_dim": head_dim,
        "kv_lora_rank": kv_lora_rank,
        "qk_rope_head_dim": qk_rope_dim,
        "num_experts": experts,
        "num_experts_per_tok": active_experts,
        "moe_layers": moe_layers,
        "max_context": max_ctx,
        "estimated_total_params_b": round(total_params / 1e9, 3),
        "estimated_active_params_b": round(active_params / 1e9, 3),
        "routed_expert_fraction": round(routed_fraction, 4),
    }


def kv_cache_gib(arch: Dict[str, object], context: int, streams: int, kv_cache: str) -> Tuple[float, float]:
    elem_bytes = KV_BYTES_PER_ELEM[kv_cache]
    layers = int(arch.get("layers") or 0)
    full_layers = int(arch.get("full_attn_layers") or layers)
    swa_layers = int(arch.get("swa_layers") or 0)
    swa_win = int(arch.get("sliding_window") or 0)
    mla_rank = int(arch.get("kv_lora_rank") or 0)
    rope_dim = int(arch.get("qk_rope_head_dim") or 0)
    kv_heads = int(arch.get("num_key_value_heads") or 0)
    head_dim = int(arch.get("head_dim") or 0)

    if layers <= 0:
        return 0.0, 0.0
    if mla_rank > 0:
        per_layer_bpt = (mla_rank + rope_dim) * elem_bytes
    else:
        per_layer_bpt = (2 * kv_heads * head_dim) * elem_bytes

    full_bytes = full_layers * per_layer_bpt * context * streams
    swa_tokens = min(context, swa_win) if swa_win > 0 else context
    swa_bytes = swa_layers * per_layer_bpt * swa_tokens * streams
    total_bpt = (full_layers + swa_layers) * per_layer_bpt
    return (full_bytes + swa_bytes) / GIB, total_bpt


def compute_graph_overhead_gib(weight_gib: float, context: int, hidden: int) -> float:
    base = 0.65 + 0.03 * weight_gib
    act = (context * max(hidden, 4096) * 4 * 2) / GIB
    return round(max(0.8, min(base + act, 6.0)), 2)


def solve_quant_entry(
    label: str,
    weight_gib: float,
    bpw: float,
    arch: Dict[str, object],
    usable_vram_gib: float,
    ram_gib: float,
    context: int,
    streams: int,
    kv_cache: str,
    routed_fraction: float,
) -> Dict[str, object]:
    kv_gib, _ = kv_cache_gib(arch, context, streams, kv_cache)
    overhead_gib = compute_graph_overhead_gib(weight_gib, context, int(arch.get("hidden_size") or 4096))
    total_needed = weight_gib + kv_gib + overhead_gib
    headroom = usable_vram_gib - total_needed
    is_moe = int(arch.get("num_experts") or 0) > 0 and routed_fraction > 0.1

    status = "OOM"
    gpu_resident_gib = total_needed
    spill_gib = 0.0
    ssd_experts = False
    prompt_cache_gib = 0.0

    if headroom >= 0:
        status = "FITS_ALL_GPU"
        prompt_cache_gib = round(max(0.0, min(headroom * 0.5, 32.0)), 1)
    elif weight_gib + overhead_gib + (kv_gib / max(1, streams)) <= usable_vram_gib:
        # Weights + at least 1 active stream fit; overflow KV can spill to host RAM.
        kv_room = usable_vram_gib - weight_gib - overhead_gib
        needed_spill = max(0.0, kv_gib - kv_room)
        if needed_spill <= max(0.0, ram_gib - 4.0):
            status = "FITS_WITH_KV_SPILL"
            spill_gib = round(needed_spill + 1.0, 1)
            gpu_resident_gib = usable_vram_gib
    elif is_moe:
        non_routed_gib = weight_gib * (1.0 - routed_fraction)
        base_gpu_need = non_routed_gib + kv_gib + overhead_gib
        if base_gpu_need <= usable_vram_gib:
            status = "FITS_SSD_EXPERTS"
            ssd_experts = True
            gpu_resident_gib = base_gpu_need
            spill_gib = 0.0

    return {
        "quant": label,
        "bpw": round(bpw, 2),
        "weights_gib": round(weight_gib, 2),
        "kv_gib": round(kv_gib, 2),
        "overhead_gib": overhead_gib,
        "total_needed_gib": round(total_needed, 2),
        "gpu_resident_gib": round(gpu_resident_gib, 2),
        "spill_gib": spill_gib,
        "ssd_experts": ssd_experts,
        "prompt_cache_gib": prompt_cache_gib,
        "headroom_gib": round(usable_vram_gib - gpu_resident_gib, 2),
        "status": status,
    }


def build_tensorfold_commands(
    best: Dict[str, object],
    context: int,
    streams: int,
    kv_cache: str,
    gpus: Sequence[float],
    backend: str,
    model_ref: str,
    is_remote_repo: bool,
) -> Dict[str, str]:
    serve_parts = [
        f"tensorfold serve {model_ref}",
        f"--context {context}",
        f"--kv-cache {kv_cache}",
        f"--max-inflight {streams}",
    ]
    if backend == "cuda":
        serve_parts.append("--backend cuda")
    if len(gpus) > 1:
        serve_parts.append(f"--parallel {len(gpus)}")
    if best.get("ssd_experts"):
        serve_parts.append("--ssd-experts")
    if float(best.get("spill_gib") or 0.0) > 0:
        serve_parts.append(f"--spill-gib {best['spill_gib']}")
    if float(best.get("prompt_cache_gib") or 0.0) >= 1.0:
        serve_parts.append(f"--prompt-cache-gib {best['prompt_cache_gib']}")
    serve_parts.append("--host 127.0.0.1 --port 8080")

    bench_parts = [
        f"tensorfold benchmark {model_ref}",
        f"--context {context}",
        f"--kv-cache {kv_cache}",
        "--tokens 256 --reps 5 --temperatures 1.0,0",
    ]
    if backend == "cuda":
        bench_parts.append("--backend cuda")

    cmds = {
        "serve": " ".join(serve_parts),
        "benchmark": " ".join(bench_parts),
    }
    if is_remote_repo:
        cmds["pull"] = f"tensorfold pull {model_ref}"
    return cmds


def _run_cmd(args: Sequence[str]) -> Optional[str]:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            return res.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def probe_tensorfold() -> Dict[str, object]:
    binary = shutil.which("tensorfold")
    if not binary:
        return {"installed": False, "version": None, "backends": [], "families": {}, "quant_formats": []}
    ver = _run_cmd([binary, "--version"])
    cap_raw = _run_cmd([binary, "capabilities", "--json"])
    caps: Dict[str, object] = {}
    if cap_raw:
        idx = cap_raw.find("{")
        if idx >= 0:
            try:
                parsed = json.loads(cap_raw[idx:])
                if isinstance(parsed, dict):
                    caps = parsed
            except json.JSONDecodeError:
                caps = {}
    families = caps.get("families") if isinstance(caps.get("families"), dict) else {}
    all_quants = sorted({str(q) for qs in families.values() if isinstance(qs, list) for q in qs})
    return {
        "installed": True,
        "version": ver or str(caps.get("version") or ""),
        "engine": str(caps.get("engine") or ""),
        "backends": list(caps.get("backends") or []),
        "family_count": len(families),
        "families": families,
        "quant_formats": all_quants,
    }


def probe_hardware() -> Dict[str, object]:
    system = platform.system()
    machine = platform.machine()
    cpu_cores = os.cpu_count() or 0
    ram_gib = 0.0
    gpus: List[Dict[str, object]] = []
    backend = "cpu"
    unified = False

    if system == "Darwin":
        mem_raw = _run_cmd(["sysctl", "-n", "hw.memsize"])
        if mem_raw and mem_raw.isdigit():
            ram_gib = round(int(mem_raw) / GIB, 2)
        if machine == "arm64":
            unified = True
            backend = "metal"
            chip = _run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon"
            gpus.append({
                "name": chip,
                "vram_gib": ram_gib,
                "working_set_gib": round(ram_gib * 0.75, 2),
                "unified_memory": True,
            })
    elif system == "Linux" and os.path.isfile("/proc/meminfo"):
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        ram_gib = round((int(parts[1]) * 1024) / GIB, 2)
                    break

    nv_out = _run_cmd([
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free,compute_cap,pcie.link.gen.current,pcie.link.width.current",
        "--format=csv,noheader,nounits",
    ])
    if nv_out:
        backend = "cuda"
        gpus = []
        for line in nv_out.splitlines():
            cols = [c.strip() for c in line.split(",")]
            if len(cols) >= 3:
                total_mib = float(cols[1])
                free_mib = float(cols[2])
                gpus.append({
                    "name": cols[0],
                    "vram_gib": round(total_mib / 1024.0, 2),
                    "free_gib": round(free_mib / 1024.0, 2),
                    "compute_cap": cols[3] if len(cols) > 3 else "",
                    "pcie": f"Gen{cols[4]} x{cols[5]}" if len(cols) > 5 else "",
                })

    return {
        "os": system,
        "arch": machine,
        "cpu_cores": cpu_cores,
        "ram_gib": ram_gib,
        "backend": backend,
        "unified_memory": unified,
        "gpus": gpus,
        "tensorfold": probe_tensorfold(),
    }


def parse_gpu_budget(gpus_arg: Optional[str], vram_arg: Optional[float], backend: str, carveout: float) -> Tuple[List[float], float]:
    if gpus_arg:
        vals = []
        for part in gpus_arg.split(","):
            part = part.strip()
            if not part:
                continue
            v = float(part)
            if not math.isfinite(v) or v <= 0:
                raise Fail(f"invalid GPU GiB value: {part!r}")
            vals.append(v)
        if not vals:
            raise Fail("--gpus must contain at least one positive GiB value")
    elif vram_arg is not None:
        if not math.isfinite(vram_arg) or vram_arg <= 0:
            raise Fail("--vram-gib must be finite and positive")
        vals = [vram_arg]
    else:
        hw = probe_hardware()
        detected = hw["gpus"]
        if detected:
            if hw["unified_memory"]:
                vals = [float(detected[0]["working_set_gib"])]
                carveout = 0.0
            else:
                vals = [float(g["vram_gib"]) for g in detected]
        else:
            raise Fail("no GPU detected; pass --vram-gib or --gpus explicitly")

    if backend == "metal":
        usable = sum(vals) - carveout
    else:
        usable = sum(vals) - carveout - 0.25 * max(0, len(vals) - 1)
    if usable <= 0:
        raise Fail(f"usable VRAM is non-positive ({usable:.2f} GiB) after carveout")
    return vals, round(usable, 2)


def inspect_local_checkpoint(folder: Path) -> Tuple[Dict[str, object], float]:
    cfg_path = folder / "config.json"
    if not cfg_path.is_file():
        raise Fail(f"config.json not found in {folder}")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise Fail(f"{cfg_path}: expected JSON object")
    arch = extract_arch_from_config(cfg)
    weight_bytes = sum(p.stat().st_size for p in folder.glob("*.safetensors") if p.is_file())
    return arch, round(weight_bytes / GIB, 3)


def inspect_hf_checkpoint(repo_or_url: str) -> Dict[str, object]:
    repo = normalize_hf_repo(repo_or_url)
    cfg = http_get_json(f"https://huggingface.co/{repo}/resolve/main/config.json")
    if not isinstance(cfg, dict):
        raise Fail(f"{repo}: config.json is not a JSON object")
    arch = extract_arch_from_config(cfg)
    entries = http_get_json(f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true")
    if not isinstance(entries, list):
        raise Fail(f"{repo}: unexpected HF file tree response")
    shards = []
    total_bytes = 0
    for e in entries:
        if e.get("type") != "file":
            continue
        p = str(e.get("path") or "")
        if p.endswith(".safetensors"):
            sz = int(e.get("size") or (e.get("lfs") or {}).get("size") or 0)
            shards.append({"path": p, "size_bytes": sz})
            total_bytes += sz
    est_params_b = float(arch.get("estimated_total_params_b") or 0.0)
    bpw = (total_bytes * 8.0 / (est_params_b * 1e9)) if (total_bytes > 0 and est_params_b > 0) else 0.0
    return {
        "repo": repo,
        "architecture": arch,
        "safetensors_shards": len(shards),
        "weight_bytes": total_bytes,
        "weight_gib": round(total_bytes / GIB, 3),
        "estimated_bpw": round(bpw, 2),
    }


def cmd_probe(args: argparse.Namespace) -> int:
    hw = probe_hardware()
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(hw, indent=2) + "\n", encoding="utf-8")
    print(f"host: {hw['os']} {hw['arch']}, {hw['cpu_cores']} CPU threads, {hw['ram_gib']:.2f} GiB RAM, backend={hw['backend']}")
    if hw["gpus"]:
        for idx, g in enumerate(hw["gpus"]):
            extra = f" (working set ~{g['working_set_gib']:.2f} GiB)" if g.get("unified_memory") else ""
            pcie = f" {g['pcie']}" if g.get("pcie") else ""
            print(f"  gpu[{idx}]: {g['name']}  {g['vram_gib']:.2f} GiB{extra}{pcie}")
    else:
        print("  gpus: none detected")
    tf = hw["tensorfold"]
    if tf["installed"]:
        print(
            f"tensorfold: {tf['version']} (engine={tf['engine'] or 'native'}, "
            f"backends={','.join(tf['backends']) or 'unknown'}, "
            f"{tf['family_count']} model families, {len(tf['quant_formats'])} quant formats)"
        )
        if tf["quant_formats"]:
            print(f"quant formats: {', '.join(tf['quant_formats'])}")
    else:
        print("tensorfold: not found in PATH")
    return 0


def cmd_inspect_repo(args: argparse.Namespace) -> int:
    info = inspect_hf_checkpoint(args.repo)
    gpus, usable_vram = parse_gpu_budget(args.gpus, args.vram_gib, args.backend, args.display_carveout_gib)
    arch = info["architecture"]
    row = solve_quant_entry(
        str(arch["detected_quant"]),
        float(info["weight_gib"]),
        float(info["estimated_bpw"]),
        arch,
        usable_vram,
        args.ram_gib,
        args.context,
        args.streams,
        args.kv_cache,
        float(arch.get("routed_expert_fraction") or 0.0),
    )
    cmds = (
        build_tensorfold_commands(row, args.context, args.streams, args.kv_cache, gpus, args.backend, info["repo"], True)
        if row["status"] != "OOM"
        else {}
    )
    out = {"checkpoint": info, "usable_vram_gib": usable_vram, "fit": row, "commands": cmds}
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(
        f"repo: {info['repo']} ({info['safetensors_shards']} safetensors shards, "
        f"{info['weight_gib']:.2f} GiB, quant={arch['detected_quant']}, ~{info['estimated_bpw']:.2f} bpw)"
    )
    print(
        f"shape: {arch['model_type']} layers={arch['layers']} hidden={arch['hidden_size']} "
        f"kv_heads={arch['num_key_value_heads']} head_dim={arch['head_dim']} experts={arch['num_experts']}"
    )
    print(
        f"fit: {row['status']} | weights={row['weights_gib']} GiB kv={row['kv_gib']} GiB "
        f"gpu={row['gpu_resident_gib']} GiB headroom={row['headroom_gib']:+.2f} GiB"
    )
    if cmds:
        print(f"PULL:  {cmds['pull']}")
        print(f"SERVE: {cmds['serve']}")
        print(f"BENCH: {cmds['benchmark']}")
        return 0
    print("FIT: OOM (try --kv-cache fp8, lower --context, or a smaller TensorFold quant)")
    return 1


def cmd_solve(args: argparse.Namespace) -> int:
    if args.context < 1 or args.streams < 1:
        raise Fail("--context and --streams must be positive")
    if args.kv_cache not in KV_BYTES_PER_ELEM:
        raise Fail(f"unsupported --kv-cache {args.kv_cache!r}")
    if not math.isfinite(args.ram_gib) or args.ram_gib < 0:
        raise Fail("--ram-gib must be finite and nonnegative")
    if not math.isfinite(args.display_carveout_gib) or args.display_carveout_gib < 0:
        raise Fail("--display-carveout-gib must be finite and nonnegative")

    gpus, usable_vram = parse_gpu_budget(args.gpus, args.vram_gib, args.backend, args.display_carveout_gib)
    is_remote = False
    measured_weight_gib = 0.0
    if args.checkpoint:
        arch, measured_weight_gib = inspect_local_checkpoint(Path(args.checkpoint))
        model_ref = args.checkpoint
    elif args.config:
        p = Path(args.config)
        if not p.is_file():
            raise Fail(f"config.json not found: {args.config}")
        cfg = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise Fail(f"{args.config}: expected JSON object")
        arch = extract_arch_from_config(cfg)
        model_ref = str(p.parent)
    elif args.hf_repo:
        repo = normalize_hf_repo(args.hf_repo)
        cfg = http_get_json(f"https://huggingface.co/{repo}/resolve/main/config.json")
        if not isinstance(cfg, dict):
            raise Fail(f"{repo}: expected JSON object in config.json")
        arch = extract_arch_from_config(cfg)
        model_ref = repo
        is_remote = True
    elif args.params_b:
        if not math.isfinite(args.params_b) or args.params_b <= 0:
            raise Fail("--params-b must be positive")
        layers = args.layers or 32
        kv_heads = args.kv_heads or 8
        head_dim = args.head_dim or 128
        arch = {
            "model_type": "manual",
            "detected_quant": "manual",
            "layers": layers,
            "full_attn_layers": layers,
            "swa_layers": 0,
            "sliding_window": 0,
            "hidden_size": args.hidden_size or 4096,
            "num_attention_heads": kv_heads * 4,
            "num_key_value_heads": kv_heads,
            "head_dim": head_dim,
            "kv_lora_rank": args.mla_rank or 0,
            "qk_rope_head_dim": args.rope_dim or 0,
            "num_experts": args.experts or 0,
            "num_experts_per_tok": args.active_experts or 0,
            "moe_layers": layers if (args.experts or 0) > 0 else 0,
            "estimated_total_params_b": args.params_b,
            "routed_expert_fraction": args.routed_fraction if (args.experts or 0) > 0 else 0.0,
        }
        model_ref = "MODEL_DIR_OR_REPO"
    else:
        raise Fail("pass one of --checkpoint, --config, --hf-repo, or --params-b")

    params_b = float(args.params_b or arch.get("estimated_total_params_b") or 0.0)
    if params_b <= 0 and measured_weight_gib <= 0:
        raise Fail("could not determine parameter count; pass --params-b")
    routed_frac = float(arch.get("routed_expert_fraction") or 0.0)

    rows: List[Dict[str, object]] = []
    if args.checkpoint and measured_weight_gib > 0:
        bpw = (measured_weight_gib * GIB * 8.0 / (params_b * 1e9)) if params_b > 0 else 0.0
        rows.append(
            solve_quant_entry(
                str(arch.get("detected_quant") or "checkpoint"),
                measured_weight_gib,
                bpw,
                arch,
                usable_vram,
                args.ram_gib,
                args.context,
                args.streams,
                args.kv_cache,
                routed_frac,
            )
        )
    else:
        for q in QUANT_TIERS:
            bpw = float(q["bpw"])
            weight_gib = (params_b * 1e9 * (bpw / 8.0)) / GIB
            entry = solve_quant_entry(
                str(q["name"]),
                weight_gib,
                bpw,
                arch,
                usable_vram,
                args.ram_gib,
                args.context,
                args.streams,
                args.kv_cache,
                routed_frac,
            )
            entry["tier"] = q["tier"]
            rows.append(entry)

    fit_order = ("FITS_ALL_GPU", "FITS_WITH_KV_SPILL", "FITS_SSD_EXPERTS")
    best = None
    for target_status in fit_order:
        best = next((r for r in rows if r["status"] == target_status), None)
        if best is not None:
            break

    commands = (
        build_tensorfold_commands(best, args.context, args.streams, args.kv_cache, gpus, args.backend, model_ref, is_remote)
        if best
        else {}
    )
    report = {
        "hardware": {
            "gpus_gib": gpus,
            "usable_vram_gib": usable_vram,
            "ram_gib": args.ram_gib,
            "backend": args.backend,
            "display_carveout_gib": args.display_carveout_gib,
        },
        "workload": {"context": args.context, "streams": args.streams, "kv_cache": args.kv_cache},
        "architecture": arch,
        "candidates": rows,
        "recommended": best,
        "commands": commands,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(
        f"budget: usable_vram={usable_vram:.2f} GiB (gpus={gpus}, carveout={args.display_carveout_gib} GiB) "
        f"ram={args.ram_gib:.1f} GiB | ctx={args.context} streams={args.streams} kv={args.kv_cache}"
    )
    print(f"model: {arch['model_type']} ~{params_b:.2f}B params, layers={arch['layers']}, experts={arch.get('num_experts', 0)}")
    print(f"{'QUANT':<16} {'BPW':>5} {'WGTS':>7} {'KV':>6} {'GPU':>7} {'SPILL':>7} {'HEADRM':>7}  STATUS")
    for r in rows:
        print(
            f"{r['quant']:<16} {r['bpw']:>5.2f} {r['weights_gib']:>6.2f}G {r['kv_gib']:>5.2f}G "
            f"{r['gpu_resident_gib']:>6.2f}G {r['spill_gib']:>6.1f}G {r['headroom_gib']:>+6.2f}G  {r['status']}"
        )
    if best:
        print(f"\nRECOMMENDED: {best['quant']} ({best['status']}, headroom {best['headroom_gib']:+.2f} GiB)")
        if commands.get("pull"):
            print(f"PULL:  {commands['pull']}")
        print(f"SERVE: {commands['serve']}")
        print(f"BENCH: {commands['benchmark']}")
        return 0
    print("\nSOLVER: NO_FIT (increase --vram-gib / --ram-gib, lower --context, or switch to --kv-cache fp8)")
    return 1


def add_hw_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--vram-gib", type=float, help="total GPU VRAM (or Apple unified working set) in GiB")
    p.add_argument("--gpus", help="comma-separated per-GPU VRAM in GiB for multi-GPU --parallel runs, e.g. 32,32")
    p.add_argument("--ram-gib", type=float, default=64.0, help="host RAM in GiB for KV spill budgets (default: 64)")
    p.add_argument("--backend", choices=("metal", "cuda", "rocm"), default="metal", help="accelerator backend (default: metal)")
    p.add_argument("--display-carveout-gib", type=float, default=1.0, help="desktop display VRAM reserve on GPU 0 (default: 1.0)")
    p.add_argument("--context", type=int, default=32768, help="target context per stream (default: 32768)")
    p.add_argument("--streams", type=int, default=1, help="concurrent requests / --max-inflight (default: 1)")
    p.add_argument("--kv-cache", choices=tuple(KV_BYTES_PER_ELEM), default="bf16", help="TensorFold --kv-cache format (default: bf16)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="probe local GPU, unified memory, host RAM and local tensorfold capabilities")
    p_probe.add_argument("--json", dest="json_out", help="write probe result to JSON file")

    p_repo = sub.add_parser("inspect-repo", help="inspect a remote Hugging Face checkpoint's size and fit before tensorfold pull")
    p_repo.add_argument("repo", help="Hugging Face repo ID or URL")
    add_hw_args(p_repo)
    p_repo.add_argument("--json", dest="json_out", help="write inspection result to JSON file")

    p_solve = sub.add_parser("solve", help="solve memory fit across all TensorFold quant tiers and emit serve/benchmark commands")
    p_solve.add_argument("--checkpoint", help="local checkpoint directory with config.json and safetensors")
    p_solve.add_argument("--config", help="local config.json path")
    p_solve.add_argument("--hf-repo", help="Hugging Face repo ID or URL (fetches config.json)")
    p_solve.add_argument("--params-b", type=float, help="manual total parameter count in billions")
    p_solve.add_argument("--layers", type=int, help="manual layer count (with --params-b)")
    p_solve.add_argument("--hidden-size", type=int, help="manual hidden size (with --params-b)")
    p_solve.add_argument("--kv-heads", type=int, help="manual KV head count (with --params-b)")
    p_solve.add_argument("--head-dim", type=int, help="manual head dim (with --params-b)")
    p_solve.add_argument("--mla-rank", type=int, help="manual MLA kv_lora_rank (with --params-b)")
    p_solve.add_argument("--rope-dim", type=int, help="manual MLA qk_rope_head_dim (with --params-b)")
    p_solve.add_argument("--experts", type=int, help="manual routed expert count (with --params-b)")
    p_solve.add_argument("--active-experts", type=int, help="manual active experts per token (with --params-b)")
    p_solve.add_argument("--routed-fraction", type=float, default=0.82, help="manual MoE routed weight fraction (default: 0.82)")
    add_hw_args(p_solve)
    p_solve.add_argument("--json", dest="json_out", help="write full solver output to JSON file")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "probe":
            return cmd_probe(args)
        if args.cmd == "inspect-repo":
            return cmd_inspect_repo(args)
        if args.cmd == "solve":
            return cmd_solve(args)
        raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        print(f"vram_solve.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
