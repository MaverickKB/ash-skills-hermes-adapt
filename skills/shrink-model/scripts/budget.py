#!/usr/bin/env python3
"""Count total and active parameters, search child shapes, and size the job.

Works from an inventory JSON, which count_params.py --emit-inventory produces:

  {
    "convention": {"inactive": ["aux_modules"]},
    "always": {"embeddings": N, "output_head": N, "aux_modules": N, "other": N},
    "layers": [
      {"index": 0, "shared": N},
      {"index": 1, "shared": N,
       "shared_experts": {"count": 1, "params_each": N},
       "experts": {"count": 256, "params_each": N, "top_k": 8, "router_per_expert": N}}
    ]
  }

Accounting, per layer:
  floor  = shared + all shared experts + router rows for every stored expert
  total  = floor + every stored routed expert
  active = floor + top_k routed experts
router_per_expert is optional. Without it the router is counted inside "shared".
"always" entries are added to the total, and to active unless they are named in
convention.inactive. A dense model is the same inventory with no "experts".

What the search counts exactly. Layer count, experts kept, and top-k are exact:
the search adds up the inventory's own numbers. Expert width is a linear model.
An expert at width w is counted as round(params_each x w). That is exact only
when every tensor in an expert has the intermediate width as exactly one of its
dimensions, and w times the intermediate width is a whole number. A bias on the
down projection, a per-expert norm, or any other term that does not scale with
the intermediate width makes the count an estimate. For such a model, and for
any change to residual width or attention dimensions, write the analytic
estimator described in references/moe.md and use its count.

Drop order. --drop-order lists layer positions, counted from 0 in inventory
order, in the order to drop them. It must hold at least as many positions as
the smallest --layers value drops. A shorter list is an error, never a silent
change to the request. Without --drop-order the kept layers are spaced evenly,
which is good for counting only.

Numbers accept suffixes: 35B, 6e9, 500M, 1.5T.

Exit status: 0 on success, 2 when an input or a request cannot be used.

Commands:
  budget.py count inventory.json
  budget.py search inventory.json --max-total 35B --max-active 6B \\
      --layers 78,60,48 --experts 256,128,64 --top-k 8,4 --expert-width 1,0.5
  budget.py search inventory.json --max-total 35B --max-active 6B \\
      --layers 76 --drop-order 40,41
  budget.py feasibility --parent-total 753B --parent-bits 8 \\
      --child-total 35B --child-active 6B --disk .
"""
import argparse
import itertools
import json
import math
import re
import shutil
import sys

SUFFIX = {"": 1, "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}
MAX_COUNT = 10 ** 18              # no inventory entry is larger than this
WIDTH_NOTE = (
    "Expert width is a linear model: an expert at width w is counted as round(params_each x w).",
    "It is exact only when every tensor in an expert has the intermediate width as exactly one of its",
    "dimensions, and w times the intermediate width is a whole number. A bias on the down projection,",
    "a per-expert norm, or any other term that does not scale with the intermediate width makes the",
    "count an estimate. In that case write the analytic estimator described in references/moe.md,",
    "check that it reproduces the parent's measured count, and use its count for Gate B.",
)


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def num(text):
    m = re.fullmatch(r"([0-9]*\.?[0-9]+(?:e[0-9]+)?)([kmbt]?)", text.strip().replace(",", "").replace("_", "").lower())
    if not m:
        raise argparse.ArgumentTypeError(f"not a number: {text}")
    return int(round(float(m.group(1)) * SUFFIX[m.group(2)]))


def num_list(text):
    return [num(t) for t in text.split(",") if t.strip()]


def float_list(text):
    try:
        values = [float(t) for t in text.split(",") if t.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a list of numbers: {text}") from None
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise argparse.ArgumentTypeError(f"a width scale must be a finite number above 0: {text}")
    return values


def human(n):
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= size:
            return f"{n / size:.3f}{unit}"
    return str(n)


def gb(nbytes):
    return f"{nbytes / 1e9:,.1f} GB"


def whole_numbers(holder, keys, where):
    """Check that holder[key] is a whole, non-negative number for each key, and store it as an int."""
    for key in keys:
        value = holder.get(key)
        if not isinstance(value, (int, float)):
            value = None
        ok = (value is not None and not isinstance(value, bool) and 0 <= value <= MAX_COUNT
              and (isinstance(value, int) or value.is_integer()))
        if not ok:
            fail(f"{where}: '{key}' must be a whole number, found {json.dumps(holder.get(key))}")
        holder[key] = int(value)


def load(path):
    try:
        with open(path) as f:
            inv = json.load(f)
    except OSError as exc:
        fail(f"{path}: {exc.strerror or exc}")
    except ValueError:
        fail(f"{path}: the inventory is not valid JSON")
    if not isinstance(inv, dict) or not isinstance(inv.get("always"), dict) or not isinstance(inv.get("layers"), list):
        fail("inventory needs an 'always' object and a 'layers' list")
    whole_numbers(inv["always"], list(inv["always"]), "always")
    convention = inv.setdefault("convention", {})
    if convention is None:
        convention = inv["convention"] = {}
    if not isinstance(convention, dict):
        fail("'convention' must be an object, such as {\"inactive\": []}")
    inactive = convention.get("inactive", [])
    if not isinstance(inactive, list) or any(not isinstance(name, str) or name not in inv["always"] for name in inactive):
        # A misspelt name would leave that entry counted as active without a word.
        fail(f"convention.inactive must be a list of 'always' keys ({', '.join(inv['always'])}), found {json.dumps(inactive)}")
    for position, layer in enumerate(inv["layers"]):
        where = f"layer at position {position}"
        if not isinstance(layer, dict):
            fail(f"{where}: must be an object")
        layer.setdefault("shared", 0)
        whole_numbers(layer, ["shared"], where)
        for key in ("shared_experts", "experts"):
            group = layer.get(key)
            if group is None:
                continue
            if not isinstance(group, dict):
                fail(f"{where}: '{key}' must be an object")
            # A missing count would drop the whole group from every total without a word.
            whole_numbers(group, ["count", "params_each"], f"{where}, {key}")
            whole_numbers(group, [k for k in ("router_per_expert", "top_k") if group.get(k) is not None], f"{where}, {key}")
    return inv


def layer_counts(layer, experts_keep=None, top_k=None, expert_width=1.0, shared_expert_width=1.0):
    shared = layer.get("shared", 0)
    se = layer.get("shared_experts") or {}
    floor = shared + round(se.get("count", 0) * se.get("params_each", 0) * shared_expert_width)
    ex = layer.get("experts") or {}
    stored = ex.get("count") or 0
    if not stored:
        return floor, floor, floor
    keep = min(stored, experts_keep) if experts_keep else stored
    k = top_k if top_k is not None else ex.get("top_k")
    if k is None:
        fail(f"layer {layer.get('index')}: top_k is missing. Fill it in from the model config.")
    k = min(k, keep)
    each = round(ex["params_each"] * expert_width)
    floor += keep * ex.get("router_per_expert", 0)   # the router scores every kept expert
    return floor + keep * each, floor + k * each, floor


def model_counts(inv, kept=None, **edits):
    inactive = set((inv.get("convention") or {}).get("inactive", []))
    always = inv["always"]
    total = sum(always.values())
    active = sum(v for k, v in always.items() if k not in inactive)
    floor = active
    for i, layer in enumerate(inv["layers"]):
        if kept is not None and i not in kept:
            continue
        t, a, f = layer_counts(layer, **edits)
        total, active, floor = total + t, active + a, floor + f
    return total, active, floor


def width_rounds(inv, kept, expert_width, shared_expert_width):
    """True when a width scale does not land on a whole parameter count in some kept layer."""
    whole = lambda x: abs(x - round(x)) < 1e-6
    for i, layer in enumerate(inv["layers"]):
        if i not in kept:
            continue
        se, ex = layer.get("shared_experts") or {}, layer.get("experts") or {}
        if not whole(se.get("count", 0) * se.get("params_each", 0) * shared_expert_width):
            return True
        if ex.get("count") and not whole(ex["params_each"] * expert_width):
            return True
    return False


def evenly_spaced(n, keep):
    if keep >= n:
        return set(range(n))
    if keep == 1:
        return {0}
    return {round(i * (n - 1) / (keep - 1)) for i in range(keep)}


def kept_layers(n, keep, drop_order):
    if drop_order:
        return set(range(n)) - set(drop_order[: n - keep])
    return evenly_spaced(n, keep)


def check_request(inv, args):
    """Refuse a grid the search could only honour by quietly changing it."""
    n = len(inv["layers"])
    for keep in args.layers or []:
        if keep > n:
            fail(f"--layers {keep} asks for more layers than the inventory's {n}")
    order = args.drop_order or []
    if any(p >= n for p in order):
        fail(f"--drop-order holds a position outside 0 to {n - 1}, the inventory's layer positions")
    if len(set(order)) != len(order):
        fail("--drop-order lists a position twice")
    if order:
        for keep in args.layers or []:
            need = n - keep
            if need > len(order):
                fail(f"--layers {keep} drops {need} of {n} layers but --drop-order lists only {len(order)}. "
                     f"Add {need - len(order)} more.")
    stored = [layer["experts"]["count"] for layer in inv["layers"] if (layer.get("experts") or {}).get("count")]
    most = max(stored, default=0)
    for flag, values in (("--experts", args.experts), ("--top-k", args.top_k)):
        for value in values or []:
            if value < 1:
                fail(f"{flag} {value} must be at least 1")
            if stored and value > most:
                fail(f"{flag} {value} is more than the {most} routed experts any layer stores")


def cmd_count(args):
    inv = load(args.inventory)
    total, active, floor = model_counts(inv)
    always = inv["always"]
    lean = active - always.get("embeddings", 0) - always.get("output_head", 0)
    moe = sum(1 for layer in inv["layers"] if (layer.get("experts") or {}).get("count"))
    print(f"layers: {len(inv['layers'])}   with routed experts: {moe}")
    print(f"total parameters:                {total:>20,}  ({human(total)})")
    print(f"active per token:                {active:>20,}  ({human(active)})")
    print(f"  without embeddings and head:   {lean:>20,}  ({human(lean)})")
    print(f"always-active floor:             {floor:>20,}  ({human(floor)})")
    print("  The floor is what stays active with zero routed experts selected.")
    inactive = (inv.get("convention") or {}).get("inactive", [])
    print(f"convention: 'always' entries counted as inactive: {inactive or 'none'}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"total": total, "active": active, "active_without_embeddings_and_head": lean, "floor": floor}, f, indent=2)


def cmd_search(args):
    inv = load(args.inventory)
    check_request(inv, args)
    n = len(inv["layers"])
    layer_grid = args.layers or [n]
    expert_grid = args.experts or [None]
    topk_grid = args.top_k or [None]
    width_grid = args.expert_width or [1.0]
    shared_grid = args.shared_expert_width or [1.0]

    combos = list(itertools.product(layer_grid, expert_grid, topk_grid, width_grid, shared_grid))
    if len(combos) > args.max_candidates:
        fail(f"{len(combos)} candidates exceeds --max-candidates {args.max_candidates}. Narrow the grids.")

    rows, lowest_active, lowest_floor = [], None, None
    for keep, experts, top_k, width, shared_width in combos:
        if experts is not None and top_k is not None and top_k > experts:
            continue
        kept = kept_layers(n, keep, args.drop_order)
        total, active, floor = model_counts(
            inv, kept, experts_keep=experts, top_k=top_k, expert_width=width, shared_expert_width=shared_width)
        row = {"layers": len(kept), "experts": experts, "top_k": top_k, "expert_width": width,
               "shared_expert_width": shared_width, "total": total, "active": active, "floor": floor,
               "width_rounded": width_rounds(inv, kept, width, shared_width)}
        if lowest_active is None or active < lowest_active["active"]:
            lowest_active = row
        if lowest_floor is None or floor < lowest_floor["floor"]:
            lowest_floor = row
        if total <= args.max_total and active <= args.max_active:
            row["fill"] = total / args.max_total + active / args.max_active
            rows.append(row)

    print(f"evaluated {len(combos)} candidates, {len(rows)} within total <= {human(args.max_total)} and active <= {human(args.max_active)}")
    if args.drop_order:
        print(f"Layers are dropped in the order given: {args.drop_order}.")
    else:
        print("Layer choice here is for counting only. Pick which layers to keep from measured sensitivity.")
    scaled = any(w != 1.0 for w in width_grid + shared_grid)
    if scaled:
        for line in WIDTH_NOTE:
            print(line)
    if lowest_active is None or lowest_floor is None:
        fail("every candidate was invalid: top_k was larger than the experts kept in each one")
    if not rows:
        la, lf = lowest_active, lowest_floor
        print(f"\nno candidate fits. Lowest active found: {human(la['active'])} with {la['layers']} layers, "
              f"experts {la['experts']}, top_k {la['top_k']}, expert width {la['expert_width']}.")
        print(f"Lowest always-active floor in the grid: {human(lf['floor'])} at {lf['layers']} layers.")
        if lf["floor"] > args.max_active:
            print("The floor alone exceeds the active budget, so changes to routed experts cannot reach it. "
                  "Reduce depth, residual width, or shared components, then rebuild the inventory.")
    else:
        rows.sort(key=lambda r: -r["fill"])
        print(f"\n{'layers':>6} {'experts':>8} {'top_k':>6} {'e_width':>8} {'s_width':>8} {'total':>10} {'active':>10} {'floor':>10}")
        for r in rows[: args.show]:
            print(f"{r['layers']:>6} {str(r['experts']):>8} {str(r['top_k']):>6} {r['expert_width']:>8} "
                  f"{r['shared_expert_width']:>8} {human(r['total']):>10} {human(r['active']):>10} {human(r['floor']):>10}"
                  f"{'  rounded' if r['width_rounded'] else ''}")
        if any(r["width_rounded"] for r in rows[: args.show]):
            print("rounded: the width scale did not land on a whole number of parameters, so that row is an estimate.")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"expert_width_model": "linear, see --help for when it is exact",
                       "feasible": rows, "lowest_active": lowest_active, "lowest_floor": lowest_floor}, f, indent=2)


def cmd_feasibility(args):
    p, c = args.parent_total, args.child_total
    if c < 1:
        fail("the child target must be at least 1 parameter")
    if c >= p:
        fail(f"the child target ({human(c)}) is not smaller than the parent ({human(p)})")
    print(f"total compression:   {p / c:,.1f}x   ({human(p)} -> {human(c)})")
    if args.parent_active and args.child_active:
        print(f"active compression:  {args.parent_active / args.child_active:,.1f}x   "
              f"({human(args.parent_active)} -> {human(args.child_active)})")
    stored = p * args.parent_bits / 8
    trainable = p * args.train_bits / 8
    print(f"\nparent as stored ({args.parent_bits}-bit):        {gb(stored)}")
    print(f"parent at {args.train_bits}-bit for surgery:      {gb(trainable)}")
    print("child weights:")
    for bits in (16, 8, 4):
        print(f"  {bits:>2}-bit: {gb(c * bits / 8)}")
    per_param = 2 * args.train_bits / 8 + args.optimizer_bytes
    print(f"child training state, weights + gradients + optimizer at {per_param:g} bytes per parameter: {gb(c * per_param)}")
    print("  This excludes activations and assumes every parameter is trainable. Replace it with a measured probe.")
    if args.disk:
        try:
            free = shutil.disk_usage(args.disk).free
        except OSError as exc:
            fail(f"--disk {args.disk}: {exc.strerror or exc}")
        need = stored + trainable + c * args.train_bits / 8
        print(f"\nfree disk at {args.disk}: {gb(free)}")
        print(f"rough disk for parent, a trainable copy, and one child checkpoint: {gb(need)}")
        print("  fits" if free > need else "  does not fit")
    print("\nEvery figure above is arithmetic from the inputs. None of it is a measurement.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("count", help="total, active, and floor for an inventory")
    c.add_argument("inventory")
    c.add_argument("--json")
    c.set_defaults(func=cmd_count)

    s = sub.add_parser("search", help="bounded grid search for child shapes within both budgets")
    s.add_argument("inventory")
    s.add_argument("--max-total", type=num, required=True)
    s.add_argument("--max-active", type=num, required=True)
    s.add_argument("--layers", type=num_list, help="layer counts to try, such as 78,60,48")
    s.add_argument("--experts", type=num_list,
                   help="routed experts kept per MoE layer. A layer that stores fewer keeps all it has")
    s.add_argument("--top-k", type=num_list,
                   help="experts selected per token. A layer that keeps fewer selects all it keeps")
    s.add_argument("--expert-width", type=float_list,
                   help="scale on routed-expert size, such as 1,0.75,0.5. A linear model: "
                        "see the top-level --help for when it is exact")
    s.add_argument("--shared-expert-width", type=float_list, help="scale on shared-expert size, the same linear model")
    s.add_argument("--drop-order", type=num_list,
                   help="layer positions to drop first, counted from 0, from sensitivity analysis. "
                        "Must cover every layer the smallest --layers value drops")
    s.add_argument("--max-candidates", type=int, default=100000)
    s.add_argument("--show", type=int, default=10)
    s.add_argument("--json")
    s.set_defaults(func=cmd_search)

    f = sub.add_parser("feasibility", help="compression ratio and storage arithmetic")
    f.add_argument("--parent-total", type=num, required=True)
    f.add_argument("--parent-active", type=num)
    f.add_argument("--parent-bits", type=int, default=16, help="bits per weight as stored")
    f.add_argument("--child-total", type=num, required=True)
    f.add_argument("--child-active", type=num)
    f.add_argument("--train-bits", type=int, default=16, help="bits per weight in the trainable copy")
    f.add_argument("--optimizer-bytes", type=float, default=8.0, help="optimizer state per parameter, 8 for two 32-bit moments")
    f.add_argument("--disk", help="path whose free space to check")
    f.set_defaults(func=cmd_feasibility)

    args = ap.parse_args()
    try:
        args.func(args)
    except OSError as exc:
        fail(f"{exc.filename or 'output'}: {exc.strerror or exc}")


if __name__ == "__main__":
    main()
