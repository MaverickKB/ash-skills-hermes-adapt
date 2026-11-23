#!/usr/bin/env python3
"""Check a child checkpoint against its inheritance manifest and its parent.

The manifest maps every child tensor to where it came from. This script
confirms that the map is complete, that the shapes are what the stated
operation implies, and that inherited values match the parent byte for byte.
Run it right after pruning, before recovery training changes the weights.

Coverage. By default every element of every copy and index_select tensor is
compared with its parent source. The last line of output states how many
inherited values were compared and how many were not. An inherited value that
was not compared fails the check, unless one of these flags was given:

  --allow-dtype-change   A tensor whose dtype differs from its parent cannot
                         be compared byte for byte. With this flag such a
                         tensor is counted as not compared and no longer
                         fails the check.
  --sample-per-index N   Compare N elements for each selected index of each
                         selected dim, and not every element. Every selected
                         row or column is touched at least once. A wrong row
                         or column is caught only if it differs at a sampled
                         position, so this is a quick check and not proof.
  --no-bytes             Check names and shapes only. No value is compared.

What a pass proves. The child matches its manifest. A pass does not prove
that the manifest is consistent across tensors, for example that the rows
kept in an up projection are the same set as the columns kept in the matching
down projection. Fixture tests and the identity conversion cover that.

Cost. The default reads every inherited byte of the child once, plus the
parent bytes it came from, so allow about the time it takes to read the child
from disk twice. A selection of whole rows adds almost nothing to that. A
selection along an inner dim adds Python work: about 5 to 15 seconds per
billion selected values for a matrix, and up to about 100 seconds per billion
for a long vector with scattered indices, as measured on one laptop. Memory
stays near three times --block-bytes however large the checkpoints are,
because tensors are streamed and never loaded whole. Only the Python standard
library is used.

Manifest shape (see assets/manifest.schema.json):

  {"tensors": [
    {"child": "layers.0.attn.q.weight", "op": "copy",
     "parent": "layers.0.attn.q.weight"},
    {"child": "layers.1.mlp.experts.0.up.weight", "op": "index_select",
     "parent": "layers.2.mlp.experts.5.up.weight",
     "select": [{"dim": 0, "indices": [0, 3, 7]}, {"dim": 1, "range": [0, 16]}]},
    {"child": "layers.1.mlp.experts.1.up.weight", "op": "transform",
     "parent": ["layers.2.mlp.experts.6.up.weight"], "transform": "functional merge, see notes"},
    {"child": "adapter.weight", "op": "new_init", "init": "normal(0, 0.02)"}
  ]}

Exit status: 0 pass, 1 the check failed, 2 an input could not be read.

Example:
  check_manifest.py --manifest manifest.json --parent /models/parent --child runs/x/child
"""
import argparse
import itertools
import json
import os
import random
import struct
import sys
from collections import defaultdict

DTYPE_BYTES = {
    "BOOL": 1, "U8": 1, "I8": 1, "F8_E4M3": 1, "F8_E5M2": 1,
    "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
    "I32": 4, "U32": 4, "F32": 4,
    "I64": 8, "U64": 8, "F64": 8,
}
OPS = {"copy", "index_select", "transform", "new_init"}
CHUNK = 16 * 1024 * 1024          # bytes per read when two contiguous spans are compared
BLOCK_BYTES = 64 * 1024 * 1024    # default for --block-bytes
MAX_RUNS = 65536                  # runs gathered in memory before a compare
MAX_OPEN = 64                     # file handles kept open per checkpoint


class InputError(Exception):
    """An input that cannot be read. Reported on one line with exit status 2."""


def elements(shape):
    n = 1
    for d in shape:
        n *= d
    return n


def unravel(flat, shape):
    out = []
    for d in reversed(shape):
        out.append(flat % d)
        flat //= d
    return out[::-1]


# ---------------------------------------------------------------- reading

def read_header(path):
    """Return (header, data_start, file_size) for one safetensors file."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(8)
            if len(raw) != 8:
                raise InputError(f"{path}: too short to be a safetensors file")
            (n,) = struct.unpack("<Q", raw)
            if n <= 0 or n > size - 8:
                raise InputError(f"{path}: header length {n} is not valid for a {size}-byte file")
            blob = f.read(n)
    except OSError as exc:
        raise InputError(f"{path}: {exc.strerror or exc}") from None
    try:
        header = json.loads(blob.decode("utf-8"))
    except ValueError:
        raise InputError(f"{path}: the header is not valid JSON") from None
    if not isinstance(header, dict):
        raise InputError(f"{path}: the header is not a JSON object")
    header.pop("__metadata__", None)
    return header, 8 + n, size


def tensor_entry(file, name, info, data_start, size):
    try:
        dtype, shape = info["dtype"], info["shape"]
        begin, end = info["data_offsets"]
    except (KeyError, TypeError, ValueError):
        raise InputError(f"{file}: tensor {name} lacks dtype, shape, or data_offsets") from None
    whole = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0
    if not (isinstance(dtype, str) and isinstance(shape, list) and all(whole(d) for d in shape)
            and whole(begin) and whole(end) and begin <= end <= size - data_start):
        raise InputError(f"{file}: tensor {name} has a shape or byte range that does not fit the file")
    n, nbytes = elements(shape), end - begin
    itemsize = DTYPE_BYTES.get(dtype)
    if itemsize is None:
        # A dtype this script does not know. Whole-byte elements can still be compared.
        itemsize = nbytes // n if n and nbytes % n == 0 else None
    elif nbytes != n * itemsize:
        raise InputError(f"{file}: tensor {name} holds {nbytes} bytes, which is not {n} elements of {dtype}")
    return {"file": file, "dtype": dtype, "shape": shape, "itemsize": itemsize,
            "start": data_start + begin, "end": data_start + end}


def index_checkpoint(path, label):
    """Map tensor name to file, dtype, shape, element size, and absolute byte range.

    Returns the index and a list of problems, such as one name stored in two shard files.
    """
    if os.path.isdir(path):
        files = sorted(os.path.join(root, n) for root, _, names in os.walk(path)
                       for n in names if n.endswith(".safetensors"))
        if not files:
            raise InputError(f"no .safetensors files under {path}")
    elif os.path.isfile(path):
        files = [path]
    else:
        raise InputError(f"{label} checkpoint not found: {path}")
    index, problems = {}, []
    for file in files:
        header, data_start, size = read_header(file)
        for name, info in header.items():
            entry = tensor_entry(file, name, info, data_start, size)
            if name in index:
                problems.append(f"{label}: tensor {name} is stored in both {index[name]['file']} and {file}")
                continue
            index[name] = entry
    return index, problems


class Handles:
    """Open each file once and reuse the handle. Past the cap, the least recently used is closed."""

    def __init__(self, limit=MAX_OPEN):
        self.limit = limit
        self.files = {}

    def get(self, path):
        f = self.files.pop(path, None)
        if f is None:
            if len(self.files) >= self.limit:
                self.files.pop(next(iter(self.files))).close()
            try:
                f = open(path, "rb")
            except OSError as exc:
                raise InputError(f"{path}: {exc.strerror or exc}") from None
        self.files[path] = f
        return f

    def close(self):
        for f in self.files.values():
            f.close()
        self.files.clear()


def read_at(f, offset, nbytes):
    f.seek(offset)
    data = f.read(nbytes)
    if len(data) != nbytes:
        raise InputError(f"{f.name}: the file ended inside a tensor. It may have changed during the check")
    return data


# ---------------------------------------------------------------- comparing

def first_difference(a, b):
    """Index of the first byte where two equal-length byte strings differ, or -1."""
    if a == b:
        return -1
    lo, hi = 0, len(a)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if a[lo:mid] != b[lo:mid]:
            hi = mid
        else:
            lo = mid
    return lo


def compare_span(pf, poff, cf, coff, nbytes, block_bytes):
    """Compare two contiguous spans chunk by chunk. Returns the offset of a difference, or -1."""
    done = 0
    while done < nbytes:
        step = min(CHUNK, block_bytes, nbytes - done)
        d = first_difference(read_at(pf, poff + done, step), read_at(cf, coff + done, step))
        if d >= 0:
            return done + d
        done += step
    return -1


def runs_of(indices):
    """Collapse indices into (start, length) runs of consecutive values, in order."""
    if isinstance(indices, range):
        return [(indices.start, len(indices))] if len(indices) else []
    runs, start, prev = [], None, None
    for i in indices:
        if start is not None and i == prev + 1:
            prev = i
            continue
        if start is not None:
            runs.append((start, prev - start + 1))
        start = prev = i
    if start is not None:
        runs.append((start, prev - start + 1))
    return runs


def line_offsets(start, terms):
    """Yield start plus one term from each list, in row-major order, without building the product."""
    if not terms:
        yield start
        return
    head, rest = terms[0], terms[1:]
    for term in head:
        if rest:
            yield from line_offsets(start + term, rest)
        else:
            yield start + term


def windows(stream, block_bytes):
    """Group (offset, length) runs so that each group spans at most block_bytes of the parent.

    A single run longer than block_bytes comes out alone.
    """
    batch, lo, hi, total = [], 0, 0, 0
    for off, n in stream:
        if batch and (max(hi, off + n) - min(lo, off) > block_bytes
                      or total + n > block_bytes or len(batch) >= MAX_RUNS):
            yield lo, hi, batch
            batch = []
        if batch:
            lo, hi, total = min(lo, off), max(hi, off + n), total + n
        else:
            lo, hi, total = off, off + n, n
        batch.append((off, n))
    if batch:
        yield lo, hi, batch


def compare_runs(pf, cf, coff, stream, block_bytes):
    """Compare the child, read in order from coff, with parent runs. Returns a child byte offset or -1."""
    pos = 0
    for lo, hi, batch in windows(stream, block_bytes):
        if len(batch) == 1:
            d = compare_span(pf, lo, cf, coff + pos, hi - lo, block_bytes)
            if d >= 0:
                return pos + d
            pos += hi - lo
            continue
        buf = read_at(pf, lo, hi - lo)
        expected = b"".join([buf[off - lo:off - lo + n] for off, n in batch])
        d = first_difference(expected, read_at(cf, coff + pos, len(expected)))
        if d >= 0:
            return pos + d
        pos += len(expected)
    return -1


def read_lines(f, bases, row):
    """Read whole lines of `row` bytes at the given offsets. Neighbouring lines share one read."""
    parts, start, count = [], bases[0], 1
    for base in bases[1:]:
        if base == start + count * row:
            count += 1
            continue
        parts.append(read_at(f, start, count * row))
        start, count = base, 1
    parts.append(read_at(f, start, count * row))
    return parts[0] if len(parts) == 1 else b"".join(parts)


def compare_strided(pf, cf, coff, lines, group, row, unit, cols):
    """Compare a selection along an inner dim, one byte column of a group of lines at a time.

    A strided slice picks the same byte of the same column out of every line in
    the group, so the Python work per group does not grow with the number of lines.
    Returns a child byte offset or -1.
    """
    crow = len(cols) * unit
    pos = 0
    lines = iter(lines)
    while True:
        bases = list(itertools.islice(lines, group))
        if not bases:
            return -1
        pbuf = read_lines(pf, bases, row)
        cbuf = read_at(cf, coff + pos, len(bases) * crow)
        for j, col in enumerate(cols):
            for b in range(unit):
                mine, theirs = cbuf[j * unit + b::crow], pbuf[col * unit + b::row]
                if mine != theirs:
                    return pos + first_difference(mine, theirs) * crow + j * unit + b
        pos += len(bases) * crow


def strided_is_cheaper(kept, unit, group, runs):
    """Rough cost model in nanoseconds for the two ways to compare a group of lines."""
    return kept * unit * (300 + 2 * group) < group * runs * 400


def compare_tensor(pf, cf, p, c, maps, block_bytes):
    """Compare every element of a child tensor with its parent source.

    maps holds, for each parent dim, the parent index of each child index, or
    None where the dim is kept whole. Returns None when every byte matches, or
    the child byte offset of a difference.
    """
    shape, size = p["shape"], p["itemsize"]
    selected = [d for d, m in enumerate(maps) if m is not None]
    if not selected:
        d = compare_span(pf, p["start"], cf, c["start"], c["end"] - c["start"], block_bytes)
        return None if d < 0 else d
    t = selected[-1]                           # the last dim with a selection
    unit = size * elements(shape[t + 1:])      # bytes in one step along dim t
    row = unit * shape[t]                      # bytes in one parent line along dim t
    cols = maps[t]
    terms = []
    for d in range(t):
        stride = size * elements(shape[d + 1:])
        terms.append(range(0, shape[d] * stride, stride) if maps[d] is None
                     else [i * stride for i in maps[d]])
    lines = line_offsets(p["start"], terms)    # parent offset of each line, in child order
    runs = [(a * unit, n * unit) for a, n in runs_of(cols)]
    if row <= block_bytes:
        group = max(1, min(elements(c["shape"][:t]), block_bytes // row))
        if strided_is_cheaper(len(cols), unit, group, len(runs)):
            d = compare_strided(pf, cf, c["start"], lines, group, row, unit, cols)
            return None if d < 0 else d
    stream = ((base + off, n) for base in lines for off, n in runs)
    d = compare_runs(pf, cf, c["start"], stream, block_bytes)
    return None if d < 0 else d


def sample_picks(child_shape, selected, per_index, rng):
    """Child positions for a sampled check.

    Every index of every selected dim appears in at least one position, so
    each kept row or column is touched however small per_index is. The other
    coordinates of each position are random.
    """
    picks = set()
    for d in selected:
        for j in range(child_shape[d]):
            for _ in range(per_index):
                pick = [rng.randrange(n) for n in child_shape]
                pick[d] = j
                picks.add(tuple(pick))
    return sorted(picks)


def flat_index(multi, shape):
    flat = 0
    for i, d in zip(multi, shape):
        flat = flat * d + i
    return flat


# ---------------------------------------------------------------- the check

def dim_maps(select, parent_shape):
    """One entry per parent dim: the parent index for each child index, or None for a dim kept whole."""
    maps = [None] * len(parent_shape)
    for rule in select or []:
        d = rule["dim"]
        if not isinstance(d, int) or isinstance(d, bool) or not 0 <= d < len(parent_shape):
            raise ValueError(f"dim {d} is out of range for shape {parent_shape}")
        if maps[d] is not None:
            raise ValueError(f"dim {d} is selected twice")
        ranged = "indices" not in rule
        idx = list(rule["range"] if ranged else rule["indices"])
        if any(not isinstance(i, int) or isinstance(i, bool) for i in idx):
            raise ValueError(f"an index on dim {d} is not an integer")
        if ranged:
            start, stop = idx
            idx = range(start, max(start, stop))    # half-open, kept lazy so a long range costs nothing
            ends = (idx[0], idx[-1]) if idx else ()
        else:
            ends = (min(idx), max(idx)) if idx else ()
        if ends and (ends[0] < 0 or ends[1] >= parent_shape[d]):
            raise ValueError(f"an index on dim {d} is outside the parent size {parent_shape[d]}")
        maps[d] = idx
    return maps


def whole_dim(idx, size):
    """True when a selection keeps every index of the dim in order, which makes it no selection."""
    if isinstance(idx, range):
        return idx == range(size)
    return len(idx) == size and all(i == j for j, i in enumerate(idx))


def check(manifest, parent, child, compare_bytes=True, allow_dtype_change=False,
          sample_per_index=0, seed=0, block_bytes=BLOCK_BYTES, problems=()):
    errors, warnings = list(problems), []
    by_op = defaultdict(int)
    skipped = defaultdict(int)
    compared = 0
    seen = set()
    rng = random.Random(seed)
    parent_files, child_files = Handles(), Handles()

    tensors = manifest.get("tensors") if isinstance(manifest, dict) else None
    if not isinstance(tensors, list):
        raise InputError("the manifest needs a 'tensors' list")
    try:
        for entry in tensors:
            if not isinstance(entry, dict) or not isinstance(entry.get("child"), str):
                errors.append(f"manifest entry without a child tensor name: {json.dumps(entry)[:80]}")
                continue
            name, op = entry["child"], entry.get("op")
            if name in seen:
                errors.append(f"{name}: listed twice")
                continue
            seen.add(name)
            if not isinstance(op, str) or op not in OPS:
                errors.append(f"{name}: unknown op {op!r}")
                continue
            c = child.get(name)
            if c is None:
                errors.append(f"{name}: in the manifest but not in the child checkpoint")
                continue
            n = elements(c["shape"])
            by_op[op] += n
            if "child_shape" in entry and entry["child_shape"] != c["shape"]:
                errors.append(f"{name}: manifest says shape {entry['child_shape']}, checkpoint has {c['shape']}")

            if op == "new_init":
                if not entry.get("init"):
                    errors.append(f"{name}: new_init needs an 'init' description")
                continue
            if op == "transform":
                sources = entry.get("parent")
                sources = [sources] if isinstance(sources, str) else (sources or [])
                if not isinstance(sources, list) or not sources or not entry.get("transform"):
                    errors.append(f"{name}: transform needs 'parent' and a 'transform' description")
                    continue
                for s in sources:
                    if not isinstance(s, str) or s not in parent:
                        errors.append(f"{name}: parent tensor {s!r} not found")
                continue

            source = entry.get("parent")
            p = parent.get(source) if isinstance(source, str) else None
            if p is None:
                errors.append(f"{name}: parent tensor {source!r} not found")
                continue
            if "parent_shape" in entry and entry["parent_shape"] != p["shape"]:
                errors.append(f"{name}: manifest says parent shape {entry['parent_shape']}, parent has {p['shape']}")

            try:
                maps = dim_maps(entry.get("select") if op == "index_select" else None, p["shape"])
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(f"{name}: bad select: {exc}")
                continue
            expected = [len(m) if m is not None else d for m, d in zip(maps, p["shape"])]
            if expected != c["shape"]:
                errors.append(f"{name}: {op} from {p['shape']} gives {expected}, checkpoint has {c['shape']}")
                continue
            if not compare_bytes:
                skipped["no_bytes"] += n
                continue
            if p["dtype"] != c["dtype"]:
                skipped["dtype_change"] += n
                message = (f"{name}: dtype changed from {p['dtype']} to {c['dtype']}, "
                           f"so {n:,} inherited values were not compared")
                if allow_dtype_change:
                    warnings.append(message)
                else:
                    errors.append(message + ". Check against a parent copy in the child's dtype, "
                                  "or pass --allow-dtype-change and verify these values another way")
                continue
            if n == 0:
                continue
            maps = [None if m is None or whole_dim(m, d) else m for m, d in zip(maps, p["shape"])]
            selected = [d for d, m in enumerate(maps) if m is not None]
            if selected and p["itemsize"] is None:
                errors.append(f"{name}: cannot compare a selection of dtype {p['dtype']}, its element size is unknown")
                continue
            pf, cf = parent_files.get(p["file"]), child_files.get(c["file"])
            size = p["itemsize"]

            if sample_per_index and selected:
                picks = sample_picks(c["shape"], selected, sample_per_index, rng)
                bad = None
                for pick in picks:
                    src = [m[i] if m is not None else i for m, i in zip(maps, pick)]
                    mine = read_at(cf, c["start"] + flat_index(pick, c["shape"]) * size, size)
                    if mine != read_at(pf, p["start"] + flat_index(src, p["shape"]) * size, size):
                        bad = (list(pick), src)
                        break
                if bad:
                    errors.append(f"{name}: child{bad[0]} differs from parent{bad[1]}")
                else:
                    compared += len(picks)
                    skipped["sampling"] += n - len(picks)
                continue

            offset = compare_tensor(pf, cf, p, c, maps, block_bytes)
            if offset is None:
                compared += n
            elif size is None:
                errors.append(f"{name}: marked {op} but byte {offset} differs from the parent")
            else:
                pick = unravel(offset // size, c["shape"])
                src = [m[i] if m is not None else i for m, i in zip(maps, pick)]
                errors.append(f"{name}: marked {op} but child{pick} differs from parent{src}")
    finally:
        parent_files.close()
        child_files.close()

    for name in child:
        if name not in seen:
            errors.append(f"{name}: in the child checkpoint but missing from the manifest")

    total = sum(elements(c["shape"]) for c in child.values())
    inherited = by_op["copy"] + by_op["index_select"]
    not_compared = inherited - compared
    reasons = {key: skipped[key] for key in ("dtype_change", "sampling", "no_bytes") if skipped[key]}
    failed = not_compared - sum(reasons.values())
    if failed:
        reasons["failed_checks"] = failed
    share = lambda x: round(100 * x / total, 4) if total else 0.0
    return {
        "ok": not errors,
        "comparison": "none" if not compare_bytes else "sampled" if sample_per_index else "full",
        "child_tensors": len(child),
        "child_parameters": total,
        "inherited": {"parameters": inherited, "percent": share(inherited)},
        "transformed": {"parameters": by_op["transform"], "percent": share(by_op["transform"])},
        "new_init": {"parameters": by_op["new_init"], "percent": share(by_op["new_init"])},
        "inherited_values_compared": compared,
        "inherited_values_not_compared": not_compared,
        "not_compared_by_reason": reasons,
        "errors": errors,
        "warnings": warnings,
    }


def verdict(report, flags):
    """The last line of output. It carries the result and its own coverage."""
    coverage = (f"compared {report['inherited_values_compared']:,} of {report['inherited']['parameters']:,} "
                f"inherited values, {report['inherited_values_not_compared']:,} not compared")
    if not report["ok"]:
        count = len(report["errors"])
        return f"FAIL with {count} error{'' if count == 1 else 's'}: {coverage}"
    return f"PASS{' with ' + ', '.join(flags) if flags else ''}: {coverage}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--parent", required=True, help="parent safetensors file or directory")
    ap.add_argument("--child", required=True, help="child safetensors file or directory")
    ap.add_argument("--allow-dtype-change", action="store_true",
                    help="do not fail on tensors whose dtype differs from the parent. Their values are "
                         "counted as not compared")
    ap.add_argument("--sample-per-index", type=int, default=0, metavar="N",
                    help="partial check: compare N elements per selected index of each selected dim in "
                         "index_select tensors, and not every element. Copies are still compared in full")
    ap.add_argument("--seed", type=int, default=0, help="seed for --sample-per-index")
    ap.add_argument("--no-bytes", action="store_true", help="check names and shapes only. No value is compared")
    ap.add_argument("--block-bytes", type=int, default=BLOCK_BYTES, metavar="BYTES",
                    help="largest span of one tensor held in memory at a time (default 64 MiB)")
    ap.add_argument("--json", help="write the report to this file")
    ap.add_argument("--max-errors", type=int, default=30)
    args = ap.parse_args()
    if args.sample_per_index < 0 or args.block_bytes < 1:
        ap.error("--sample-per-index cannot be negative and --block-bytes must be at least 1")

    try:
        with open(args.manifest) as f:
            manifest = json.load(f)
    except OSError as exc:
        raise InputError(f"{args.manifest}: {exc.strerror or exc}") from None
    except ValueError:
        raise InputError(f"{args.manifest}: the manifest is not valid JSON") from None
    parent, parent_problems = index_checkpoint(args.parent, "parent")
    child, child_problems = index_checkpoint(args.child, "child")
    report = check(manifest, parent, child, compare_bytes=not args.no_bytes,
                   allow_dtype_change=args.allow_dtype_change, sample_per_index=args.sample_per_index,
                   seed=args.seed, block_bytes=args.block_bytes, problems=parent_problems + child_problems)

    flags = []
    if args.no_bytes:
        flags.append("--no-bytes")
    elif args.sample_per_index:
        flags.append(f"--sample-per-index {args.sample_per_index}")
    if args.allow_dtype_change:
        flags.append("--allow-dtype-change")
    report["flags"] = flags
    report["verdict"] = verdict(report, flags)

    print(f"child tensors: {report['child_tensors']}   parameters: {report['child_parameters']:,}")
    for key in ("inherited", "transformed", "new_init"):
        print(f"  {key:<12}{report[key]['parameters']:>18,}  {report[key]['percent']:>8.2f}%")
    for reason, count in report["not_compared_by_reason"].items():
        print(f"  inherited values not compared, {reason.replace('_', ' ')}: {count:,}")
    for w in report["warnings"][: args.max_errors]:
        print(f"warning: {w}")
    if len(report["warnings"]) > args.max_errors:
        print(f"... and {len(report['warnings']) - args.max_errors} more warnings")
    for e in report["errors"][: args.max_errors]:
        print(f"ERROR: {e}")
    if len(report["errors"]) > args.max_errors:
        print(f"... and {len(report['errors']) - args.max_errors} more errors")
    if args.json:
        try:
            with open(args.json, "w") as f:
                json.dump(report, f, indent=2)
        except OSError as exc:
            raise InputError(f"{args.json}: {exc.strerror or exc}") from None
    print(report["verdict"])
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    try:
        main()
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
