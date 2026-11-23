#!/usr/bin/env python3
"""Inventory a safetensors checkpoint from its headers alone.

Reads only the JSON header of each .safetensors file, so it works on
checkpoints far larger than memory and never loads a tensor.

Counts are tensor elements as stored. In a packed or block-quantized
checkpoint the stored element count can differ from the architectural
parameter count, so the report flags the tensors where that may be the case.
Component names come from regex rules on tensor names. They are heuristics:
check them against the model's own code and override with --rules when wrong.

Layers are grouped by stack. A stack is every tensor that shares the name
text before its "layers.N." part, so "model.layers.0" and
"vision_tower.layers.0" are layer 0 of two different stacks. The report lists
each stack. The inventory treats the stack with the most parameters as the
trunk, or the one named with --trunk, and puts every other stack into the
always-active bucket as "other_stacks".

Tensors that look like quantization scales are left out of the parameter
estimate and listed by name, so a wrong match is easy to see.

--emit-inventory refuses to write an inventory whose total differs from the
parameter estimate, because budget.py would then count the wrong model.

Exit status: 0 on success, 2 when an input cannot be read or the inventory
cannot be written.

Examples:
  count_params.py /models/parent
  count_params.py /models/parent --json inventory_report.json
  count_params.py /models/parent --emit-inventory inventory.json
  count_params.py /models/parent --trunk model --emit-inventory inventory.json
"""
import argparse
import json
import os
import re
import struct
import sys
from collections import defaultdict

DTYPE_BITS = {
    "BOOL": 8, "U8": 8, "I8": 8, "F8_E4M3": 8, "F8_E5M2": 8,
    "I16": 16, "U16": 16, "F16": 16, "BF16": 16,
    "I32": 32, "U32": 32, "F32": 32,
    "I64": 64, "U64": 64, "F64": 64,
}
INTEGER_DTYPES = {"BOOL", "U8", "I8", "I16", "U16", "I32", "U32", "I64", "U64"}

# Ordered. The first rule whose pattern is found in the lowercased tensor name wins.
# quantization_aux names only what quantization tools write. A bare "scale" is not
# enough: norm weights and logit scales carry that name and are real parameters.
# "mtp" must stand as its own word part, so mtp_head and mtp.0 match and smtp does not.
DEFAULT_RULES = [
    ["quantization_aux", r"(weight_scale|(?:input|output)_scale|scale_inv|zero_point|absmax|quant_state|\.scales$)"],
    ["aux_modules", r"((?<![a-z0-9])mtp(?![a-z])|multi_token)"],
    ["shared_experts", r"shared_experts?"],
    ["routed_experts", r"(experts?\.\d+\.|\.experts\.)"],
    ["norms", r"(norm|(?<![a-z])rms(?![a-z])|\.ln_?\d*\.|\.ln_f\.)"],
    ["embeddings", r"(embed_tokens|tok_embeddings|\bwte\b|\bwpe\b|embeddings?\.)"],
    ["output_head", r"(lm_head|unembed|output_projection)"],
    ["routers", r"(router|\.gate\.(weight|bias)$|e_score_correction_bias)"],
    ["indexers", r"indexer"],
    ["attention", r"(attn|attention|[qkvo]_proj|kv_[ab]_proj|q_[ab]_proj)"],
    ["mlp", r"(mlp|ffn|feed_forward|fc[12]|gate_proj|up_proj|down_proj|intermediate|\.c_fc|\.c_proj)"],
]

LAYER_RE = re.compile(r"(?:^|\.)(?:layers|layer|h|blocks|block)\.(\d+)\.")
EXPERT_RE = re.compile(r"(?<!shared_)experts?\.(\d+)\.")
SHARED_EXPERT_RE = re.compile(r"shared_experts?\.(\d+)\.")


def read_header(path):
    """Return (tensors, data_start) for one safetensors file."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        raw = f.read(8)
        if len(raw) != 8:
            raise ValueError(f"{path}: too short to be a safetensors file")
        (n,) = struct.unpack("<Q", raw)
        if n <= 0 or n > size - 8:
            raise ValueError(f"{path}: header length {n} is not valid for a {size}-byte file")
        blob = f.read(n)
    try:
        header = json.loads(blob.decode("utf-8"))
    except ValueError:
        raise ValueError(f"{path}: the header is not valid JSON") from None
    if not isinstance(header, dict):
        raise ValueError(f"{path}: the header is not a JSON object")
    header.pop("__metadata__", None)
    whole = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0
    for name, info in header.items():
        try:
            shape, (begin, end) = info["shape"], info["data_offsets"]
            ok = isinstance(info["dtype"], str) and isinstance(shape, list) and all(whole(d) for d in shape) \
                and whole(begin) and whole(end) and begin <= end
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            raise ValueError(f"{path}: tensor {name} lacks a usable dtype, shape, or data_offsets")
    return header, 8 + n


def find_files(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in names if n.endswith(".safetensors")]
        elif os.path.isfile(p):
            files.append(p)
        else:
            raise ValueError(f"not found: {p}")
    if not files:
        raise ValueError("no .safetensors files found")
    return sorted(files)


def classify(name, rules):
    lowered = name.lower()
    for component, pattern in rules:
        if pattern.search(lowered):
            return component
    return "unclassified"


def elements(shape):
    n = 1
    for d in shape:
        n *= d
    return n


def new_layer():
    return {
        "components": defaultdict(int),
        "expert_params": defaultdict(int),          # numbered experts, by expert id
        "fused_expert_params": 0,                   # expert tensors with no expert id in the name
        "fused_expert_dims": set(),
        "shared_expert_params": defaultdict(int),
    }


def expert_row(index, layer, warnings):
    """Summarise the routed experts of one layer, counting numbered and fused tensors together."""
    numbered, fused = layer["expert_params"], layer["fused_expert_params"]
    dims = sorted(layer["fused_expert_dims"], key=str)
    count, sizes = None, None
    if numbered and not fused:
        count, sizes = len(numbered), list(numbered.values())
    elif fused and not numbered:
        if len(dims) == 1 and dims[0]:
            count = dims[0]
            sizes = [fused // count]
        else:
            warnings.append(f"layer {index}: fused expert tensors disagree on the leading dimension {dims}, "
                            "so the expert count is unknown")
    else:
        count = len(numbered)
        if dims == [count]:
            sizes = [n + fused // count for n in numbered.values()]
            warnings.append(f"layer {index}: experts are stored as numbered tensors plus fused tensors with "
                            f"leading dimension {count}. Both are counted, with the fused {fused:,} elements "
                            "split evenly across the experts. Check that against the model code.")
        else:
            warnings.append(f"layer {index}: {count} numbered experts sit beside fused expert tensors with "
                            f"leading dimension {dims}. The fused {fused:,} elements cannot be split per "
                            "expert, so no inventory can be written. Reclassify them with --rules.")
    if sizes and min(sizes) != max(sizes):
        warnings.append(f"layer {index}: routed experts differ in size")
    return {
        "fused": bool(fused), "numbered": bool(numbered), "count": count,
        "total": sum(numbered.values()) + fused,
        "params_each_min": min(sizes) if sizes else None,
        "params_each_max": max(sizes) if sizes else None,
    }


def build_report(files, rules, top, trunk=None):
    tensors = {}
    warnings = []
    for path in files:
        header, data_start = read_header(path)
        need = data_start + max((info["data_offsets"][1] for info in header.values()), default=0)
        if need > os.path.getsize(path):
            warnings.append(
                f"{path}: the header describes {need:,} bytes but the file holds {os.path.getsize(path):,}. "
                "The file is cut short, or it is a header-only stub. Counts come from the header alone.")
        for name, info in header.items():
            if name in tensors:
                warnings.append(f"duplicate tensor name across files: {name}")
                continue
            tensors[name] = info

    components = defaultdict(int)
    dtypes = defaultdict(lambda: {"elements": 0, "bytes": 0})
    non_layer = defaultdict(int)
    stacks = defaultdict(lambda: {"layers": defaultdict(new_layer), "parameters": 0})
    unclassified, excluded, matrices = [], [], defaultdict(list)
    integer_elements = 0

    for name, info in tensors.items():
        dtype, shape = info["dtype"], info["shape"]
        n = elements(shape)
        begin, end = info["data_offsets"]
        nbytes = end - begin
        bits = DTYPE_BITS.get(dtype)
        if bits is None:
            warnings.append(f"unknown dtype {dtype} on {name}")
        elif nbytes * 8 != n * bits:
            warnings.append(f"{name}: {nbytes} bytes does not match {n} elements of {dtype}")
        dtypes[dtype]["elements"] += n
        dtypes[dtype]["bytes"] += nbytes

        component = classify(name, rules)
        components[component] += n
        if component == "unclassified":
            unclassified.append((n, name))
        if component == "quantization_aux":
            excluded.append((n, name, dtype))
        elif dtype in INTEGER_DTYPES:
            integer_elements += n

        # Auxiliary modules can hold their own numbered layers. Keep them out of
        # every stack so they are never merged into a trunk layer.
        m = None if component == "aux_modules" else LAYER_RE.search(name)
        if not m:
            non_layer[component] += n
            if component in ("embeddings", "output_head") and len(shape) == 2:
                matrices[component].append((name, shape))
            continue
        stack = stacks[name[:m.start()]]        # the stack is named by the text before "layers.N."
        if component != "quantization_aux":
            stack["parameters"] += n
        layer = stack["layers"][int(m.group(1))]
        layer["components"][component] += n
        if component == "routed_experts":
            e = EXPERT_RE.search(name)
            if e:
                layer["expert_params"][int(e.group(1))] += n
            else:
                layer["fused_expert_dims"].add(shape[0] if shape else None)
                layer["fused_expert_params"] += n
        elif component == "shared_experts":
            s = SHARED_EXPERT_RE.search(name)
            layer["shared_expert_params"][int(s.group(1)) if s else 0] += n

    total = sum(components.values())
    aux = components.get("quantization_aux", 0)
    if aux:
        warnings.append(
            f"{aux:,} elements in {len(excluded)} tensors look like quantization data. They are excluded "
            "from the parameter estimate. Read the list of excluded tensors and confirm each one against "
            "the checkpoint's quantization scheme. Override with --rules if a real parameter is listed."
        )
    if integer_elements:
        warnings.append(
            f"{integer_elements:,} elements are stored as integer dtypes. They may be packed or "
            "quantized codes, in which case the element count is not the parameter count. "
            "Take architectural counts from the config for those tensors."
        )
    if components.get("unclassified"):
        warnings.append("some tensors matched no rule. Review the unclassified list and extend --rules.")
    for emb_name, emb_shape in matrices["embeddings"]:
        twin = next((name for name, shape in matrices["output_head"] if shape == emb_shape), None)
        if twin:
            warnings.append(
                f"{emb_name} and {twin} are both stored with shape {emb_shape}. They may be tied. If the "
                f"model ties them, the total counts {elements(emb_shape):,} parameters twice. Check the "
                "config, and if they are tied subtract that number from always.output_head in the inventory."
            )
            break

    if trunk is not None and trunk not in stacks:
        found = ", ".join(json.dumps(p) for p in sorted(stacks)) or "none"
        raise ValueError(f"--trunk {json.dumps(trunk)} names no layer stack. Stacks found: {found}")
    if trunk is None and stacks:
        trunk = max(sorted(stacks), key=lambda prefix: stacks[prefix]["parameters"])
    others = sorted(p for p in stacks if p != trunk)
    other_parameters = sum(stacks[p]["parameters"] for p in others)
    if others:
        warnings.append(
            f"found {len(stacks)} layer stacks. {json.dumps(trunk)} is treated as the trunk. The other "
            f"stacks ({', '.join(json.dumps(p) for p in others)}) hold {other_parameters:,} parameters, "
            "which the inventory puts in always.other_stacks and counts as always active. "
            "Pass --trunk PREFIX if the trunk is a different stack."
        )

    layer_rows = []
    trunk_layers = stacks[trunk]["layers"] if stacks else {}
    for index in sorted(trunk_layers):
        layer = trunk_layers[index]
        row = {"index": index, "components": dict(layer["components"])}
        if layer["expert_params"] or layer["fused_expert_params"]:
            row["experts"] = expert_row(index, layer, warnings)
        sp = layer["shared_expert_params"]
        if sp:
            sizes = list(sp.values())
            row["shared_experts"] = {"count": len(sizes), "total": sum(sizes),
                                     "params_each": max(sizes), "params_each_min": min(sizes)}
            if min(sizes) != max(sizes):
                warnings.append(f"layer {index}: shared experts differ in size")
        layer_rows.append(row)

    unclassified.sort(reverse=True)
    excluded.sort(reverse=True)
    return {
        "files": len(files),
        "tensors": len(tensors),
        "total_elements": total,
        "quantization_aux_elements": aux,
        "parameter_estimate": total - aux,
        "components": dict(sorted(components.items(), key=lambda kv: -kv[1])),
        "dtypes": dict(dtypes),
        "non_layer": dict(non_layer),
        "trunk": trunk,
        "stacks": [{"prefix": p, "trunk": p == trunk, "layers": len(stacks[p]["layers"]),
                    "parameters": stacks[p]["parameters"]} for p in sorted(stacks)],
        "other_stacks_parameters": other_parameters,
        "layers": layer_rows,
        "unclassified": [{"name": n, "elements": c} for c, n in unclassified[:top]],
        "quantization_aux_tensors": {
            "count": len(excluded),
            "listed": [{"name": n, "elements": c, "dtype": d} for c, n, d in excluded[:top]],
        },
        "warnings": warnings,
    }


def to_inventory(report):
    """Shape the report into the inventory that budget.py reads.

    Raises ValueError when the inventory would not add up to the parameter estimate.
    """
    non_layer = dict(report["non_layer"])
    non_layer.pop("quantization_aux", None)
    always = {
        "embeddings": non_layer.pop("embeddings", 0),
        "output_head": non_layer.pop("output_head", 0),
        "aux_modules": non_layer.pop("aux_modules", 0),
    }
    if report["other_stacks_parameters"]:
        always["other_stacks"] = report["other_stacks_parameters"]
    always["other"] = sum(non_layer.values())

    layers = []
    for row in report["layers"]:
        comps = dict(row["components"])
        comps.pop("quantization_aux", None)
        comps.pop("routed_experts", None)
        comps.pop("shared_experts", None)
        entry = {"index": row["index"], "shared": sum(comps.values())}
        if "shared_experts" in row:
            se = row["shared_experts"]
            if se["params_each"] != se["params_each_min"]:
                raise ValueError(f"layer {row['index']}: shared experts differ in size, which the inventory "
                                 "cannot express. Write that layer's entry by hand.")
            entry["shared_experts"] = {"count": se["count"], "params_each": se["params_each"]}
        if "experts" in row:
            ex = row["experts"]
            if ex["count"] is None or ex["params_each_max"] is None:
                raise ValueError(f"layer {row['index']}: the routed experts could not be counted per expert. "
                                 "See the warnings above, then fix the grouping with --rules.")
            if ex["params_each_min"] != ex["params_each_max"]:
                raise ValueError(f"layer {row['index']}: routed experts differ in size, which the inventory "
                                 "cannot express. Write that layer's entry by hand.")
            entry["experts"] = {"count": ex["count"], "params_each": ex["params_each_max"], "top_k": None}
            routers = comps.get("routers", 0)
            if ex["count"] and routers and routers % ex["count"] == 0:
                entry["experts"]["router_per_expert"] = routers // ex["count"]
                entry["shared"] -= routers
        layers.append(entry)

    counted = sum(always.values())
    for entry in layers:
        se, ex = entry.get("shared_experts", {}), entry.get("experts", {})
        counted += entry["shared"] + se.get("count", 0) * se.get("params_each", 0)
        counted += ex.get("count", 0) * (ex.get("params_each", 0) + ex.get("router_per_expert", 0))
    if counted != report["parameter_estimate"]:
        raise ValueError(
            f"the inventory would total {counted:,} but the checkpoint's parameter estimate is "
            f"{report['parameter_estimate']:,}. The grouping lost or doubled "
            f"{abs(counted - report['parameter_estimate']):,} elements. Fix it with --rules.")

    note = (
        "Fill in top_k for each MoE layer from the model config. Check the component split against the "
        "model code. router_per_expert assumes a linear router with one row per expert. If the router "
        "is built differently, delete that key and add its parameters back into 'shared'."
    )
    if always["aux_modules"]:
        note += (
            f" This checkpoint holds {always['aux_modules']:,} parameters in auxiliary modules. Decide from "
            "the model code whether inference uses them. If it does not, add \"aux_modules\" to "
            "convention.inactive so they stop counting as active. They stay in the total for as long as "
            "they are in the package."
        )
    if "other_stacks" in always:
        note += (
            f" Layers come from the stack {json.dumps(report['trunk'])}. Other layer stacks hold "
            f"{always['other_stacks']:,} parameters, counted in always.other_stacks as always active. "
            "Rerun count_params.py with --trunk if the trunk is a different stack."
        )
    return {
        "note": note,
        "warnings": report["warnings"],
        "convention": {"inactive": []},
        "always": always,
        "layers": layers,
    }


def print_report(report):
    print(f"files: {report['files']}   tensors: {report['tensors']}")
    print(f"stored elements:     {report['total_elements']:>20,}")
    print(f"quantization aux:    {report['quantization_aux_elements']:>20,}")
    print(f"parameter estimate:  {report['parameter_estimate']:>20,}")
    print("\nby component")
    for name, n in report["components"].items():
        print(f"  {name:<18}{n:>20,}")
    print("\nby dtype")
    for name, d in report["dtypes"].items():
        print(f"  {name:<18}{d['elements']:>20,} elements {d['bytes']:>20,} bytes")
    if report["stacks"]:
        print("\nlayer stacks, named by the text before \"layers.N.\"")
        for s in report["stacks"]:
            print(f"  {json.dumps(s['prefix']):<32} layers: {s['layers']:<5} parameters: {s['parameters']:>18,}"
                  f"{'   trunk' if s['trunk'] else ''}")
    rows = report["layers"]
    if rows:
        moe = [r for r in rows if "experts" in r]
        print(f"\ntrunk layers: {len(rows)}   with routed experts: {len(moe)}")
        if moe:
            counts = sorted({r["experts"]["count"] for r in moe if r["experts"]["count"]})
            sizes = sorted({r["experts"]["params_each_max"] for r in moe if r["experts"]["params_each_max"]})
            print(f"  routed experts per MoE layer: {counts}")
            print(f"  parameters per routed expert: {[f'{s:,}' for s in sizes]}")
    if report["unclassified"]:
        print("\nlargest unclassified tensors")
        for item in report["unclassified"]:
            print(f"  {item['elements']:>16,}  {item['name']}")
    quant = report["quantization_aux_tensors"]
    if quant["count"]:
        print(f"\ntensors excluded as quantization data: {quant['count']}, largest first")
        for item in quant["listed"]:
            print(f"  {item['elements']:>16,}  {item['dtype']:<8}{item['name']}")
        if quant["count"] > len(quant["listed"]):
            print(f"  ... and {quant['count'] - len(quant['listed'])} more. Raise --top to see them.")
    if report["warnings"]:
        print("\nwarnings")
        for w in report["warnings"]:
            print(f"  - {w}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="safetensors files, or directories searched recursively")
    ap.add_argument("--rules", help='JSON file holding an ordered list of ["component", "regex"] pairs')
    ap.add_argument("--trunk", metavar="PREFIX",
                    help='the layer stack to treat as the trunk, named by the text before "layers.N." '
                         "as the report lists it. Default: the stack with the most parameters")
    ap.add_argument("--json", help="write the full report to this file")
    ap.add_argument("--emit-inventory", help="write an inventory for budget.py to this file")
    ap.add_argument("--top", type=int, default=15,
                    help="how many unclassified tensors, and how many excluded quantization tensors, to list")
    args = ap.parse_args()

    raw_rules = DEFAULT_RULES
    if args.rules:
        try:
            with open(args.rules) as f:
                raw_rules = json.load(f)
        except ValueError:
            raise ValueError(f"{args.rules}: the rules file is not valid JSON") from None
    try:
        rules = [(name, re.compile(pattern)) for name, pattern in raw_rules]
    except (TypeError, ValueError, re.error) as exc:
        raise ValueError(f"the rules must be a list of [\"component\", \"regex\"] pairs: {exc}") from None

    report = build_report(find_files(args.paths), rules, args.top, args.trunk)
    print_report(report)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
    if args.emit_inventory:
        inventory = to_inventory(report)
        with open(args.emit_inventory, "w") as f:
            json.dump(inventory, f, indent=2)
        print(f"\ninventory written to {args.emit_inventory}")


if __name__ == "__main__":
    try:
        main()
    except OSError as exc:
        print(f"error: {exc.filename or ''}: {exc.strerror or exc}", file=sys.stderr)
        sys.exit(2)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
