#!/usr/bin/env python3
"""Track a non-linear fault web: fan out, jump to the middle, converge, confirm, resolve."""
import argparse
import json
import os
import sys

LAYERS = [
    "physical", "link", "ip", "dns", "routing", "transport",
    "application", "credentials", "time", "data", "human",
]

DETAIL = [
    "exact string and case", "leading/trailing whitespace", "hidden characters",
    "path and symlink target", "port and protocol", "version and build",
    "permission and owner", "timestamp and timezone", "encoding and line endings",
    "default value", "stale cache or entry", "duplicate or conflicting entry",
    "boundary and off-by-one", "units and scale", "locale and format",
]

DETAIL_BY_LAYER = {
    "physical": ["cable seated", "link light", "power", "NIC enabled"],
    "link": ["port status", "VLAN", "MAC", "duplex", "speed"],
    "ip": ["address", "subnet", "gateway", "duplicate", "DHCP lease"],
    "dns": ["resolv.conf", "search domains", "ndots", "systemd-resolved",
            "/etc/hosts precedence", "per-interface resolver"],
    "routing": ["default route", "route table", "VPN split tunnel", "upstream", "metric"],
    "transport": ["firewall", "MTU", "port", "NAT", "timeout"],
    "application": ["proxy", "captive portal", "browser config", "service config", "cache"],
    "credentials": ["cert expiry", "auth", "token", "permission", "clock"],
    "time": ["clock skew", "lease expiry", "cert validity", "timezone"],
    "data": ["hosts file", "config", "cache", "duplicate entry", "stale entry"],
    "human": ["typo", "wrong interface", "wrong file", "wrong machine", "wrong user"],
}

RESOLUTIONS = ["direct fix", "workaround", "prevention", "monitor only"]
COST = {"low": 0, "med": 1, "high": 2}
REV = {"easy": 0, "med": 1, "hard": 2}


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, web):
    with open(path, "w") as f:
        json.dump(web, f, indent=2)
        f.write("\n")


def find(web, cid):
    for layer, items in web["layers"].items():
        for c in items:
            if c["id"] == cid:
                return layer, c
    return None, None


def surviving(web):
    return [
        c["id"]
        for items in web["layers"].values()
        for c in items
        if c["status"] in ("open", "supported")
    ]


def search_scars(path, query):
    q = query.lower()
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if q in json.dumps(entry).lower():
                out.append(entry)
    return out


def cmd_init(a):
    web = {
        "symptom": a.symptom,
        "middle": [],
        "layers": {layer: [] for layer in LAYERS},
        "plans": [],
        "observations": [],
        "second_pass": {},
        "confirmation": {},
        "resolutions": [],
    }
    save(a.out, web)
    print(f"FAULT WEB: created {a.out} with {len(LAYERS)} layers")
    print("Fill every layer before converging. An empty layer is an unturned stone.")
    if a.scars:
        if not os.path.exists(a.scars):
            print(f"no scar file at {a.scars}")
        else:
            matches = search_scars(a.scars, a.symptom)
            if matches:
                print("scars matching the symptom:")
                for m in matches:
                    print(f"  - {m['symptom']} -> {m['cause']} (fix: {m['fix']})")
            else:
                print("no matching scars")


def cmd_add(a):
    web = load(a.web)
    if a.layer not in web["layers"]:
        print(f"UNKNOWN LAYER: {a.layer}. Use one of: {', '.join(LAYERS)}")
        return 2
    if find(web, a.id)[1] is not None:
        print(f"DUPLICATE ID: {a.id}")
        return 2
    web["layers"][a.layer].append(
        {"id": a.id, "cause": a.cause, "test": a.test, "status": "untested"}
    )
    save(a.web, web)
    print(f"added {a.id} to {a.layer}: {a.cause}")


def cmd_middle(a):
    web = load(a.web)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    for cid in ids:
        if find(web, cid)[1] is None:
            print(f"UNKNOWN: {cid}")
            return 2
    web["middle"] = ids
    save(a.web, web)
    print(f"middle: {', '.join(ids)}")


def cmd_plan(a):
    web = load(a.web)
    prunes = [x.strip() for x in (a.prunes or "").split(",") if x.strip()]
    supports = [x.strip() for x in (a.supports or "").split(",") if x.strip()]
    for cid in prunes + supports:
        if find(web, cid)[1] is None:
            print(f"UNKNOWN: {cid}")
            return 2
    if a.cost not in COST:
        print(f"UNKNOWN COST: {a.cost}. Use one of: {', '.join(COST)}")
        return 2
    web["plans"].append(
        {"text": a.text, "prunes": prunes, "supports": supports, "cost": a.cost, "done": False}
    )
    save(a.web, web)
    print(f"planned: {a.text}")


def cmd_unplan(a):
    web = load(a.web)
    before = len(web["plans"])
    web["plans"] = [p for p in web["plans"] if p["text"] != a.text]
    if len(web["plans"]) == before:
        print(f"UNKNOWN PLAN: {a.text}")
        return 2
    save(a.web, web)
    print(f"removed plan: {a.text}")


def cmd_rank(a):
    web = load(a.web)
    surv = surviving(web)
    plans = [p for p in web["plans"] if not p.get("done")]
    if not plans:
        print("no plans; add one with plan")
        return 0
    rows = []
    for p in plans:
        pruned = len([x for x in p["prunes"] if x in surv])
        supported = len([x for x in p["supports"] if x in surv])
        balance = abs(pruned - len(surv) / 2)
        score = balance + COST.get(p.get("cost", "low"), 0)
        rows.append((score, balance, p))
    rows.sort(key=lambda r: (r[0], r[1]))
    print(f"{'plan':<40}{'prunes':<8}{'supports':<10}{'balance':<8}{'cost':<6}{'score':<6}")
    for score, balance, p in rows:
        print(f"{p['text'][:38]:<40}{len(p['prunes']):<8}{len(p['supports']):<10}{balance:<8}{p.get('cost', 'low'):<6}{score:<6}")
    best = rows[0][2]
    print(f"pick: {best['text']}")
    if len(rows) > 1:
        print(f"what would change it: {rows[1][2]['text']} if it is cheaper or splits closer to half")
    return 0


def cmd_check(a):
    web = load(a.web)
    problems = 0
    seen = set()
    untested = 0
    for layer, items in web["layers"].items():
        if not items:
            print(f"EMPTY LAYER: {layer}")
            problems += 1
        for c in items:
            if c["id"] in seen:
                print(f"DUPLICATE ID: {c['id']}")
                problems += 1
            seen.add(c["id"])
            if not c.get("cause"):
                print(f"NO CAUSE: {c['id']} ({layer})")
                problems += 1
            if not c.get("test"):
                print(f"NO TEST: {c['id']} ({layer}) {c['cause']}")
                problems += 1
            if c.get("status") == "untested":
                print(f"UNTESTED: {c['id']} ({layer}) {c['cause']}")
                problems += 1
                untested += 1
    for o in web["observations"]:
        if not o.get("prunes") and not o.get("supports"):
            print(f"NO EFFECT: observation {o['text']!r} changes nothing")
            problems += 1
    for p in web["plans"]:
        if not p.get("done") and not p.get("prunes") and not p.get("supports"):
            print(f"NO EFFECT PLAN: {p['text']!r} changes nothing")
            problems += 1
    if not web["middle"]:
        print("NO MIDDLE: pick the most likely fault layer before converging")
        problems += 1
    else:
        for cid in web["middle"]:
            layer, c = find(web, cid)
            if c is None:
                print(f"UNKNOWN MIDDLE: {cid}")
                problems += 1
            elif c["status"] == "pruned":
                print(f"PRUNED MIDDLE: {cid} was ruled out; pick a live middle")
                problems += 1
            elif c["status"] == "untested":
                print(f"UNTESTED MIDDLE: {cid} was never run")
                problems += 1
    sp = web.get("second_pass") or {}
    missing = [k for k in ("misleading", "outside", "expert", "changed") if not sp.get(k)]
    if missing:
        print(f"NO SECOND PASS: answer {', '.join(missing)}")
        problems += 1
    conf = web.get("confirmation") or {}
    if not conf:
        print("NO CONFIRMATION: run confirm or record not-possible")
        problems += 1
    elif conf.get("not_possible"):
        print(f"CONFIRMATION NOT POSSIBLE: {conf['not_possible']}")
        problems += 1
    elif not all(conf.get(k) for k in ("remove", "symptom_gone", "restore", "symptom_returns")):
        print("INCOMPLETE CONFIRMATION: answer remove, symptom_gone, restore, symptom_returns")
        problems += 1
    by_cause = {}
    for r in web["resolutions"]:
        by_cause.setdefault(r["cause"], set()).add(r["option"])
    for cause, opts in by_cause.items():
        if len(opts) < 4:
            print(f"INCOMPLETE RESOLUTIONS: {cause} has {len(opts)} of 4")
            problems += 1
    ids = surviving(web)
    print(f"surviving: {', '.join(ids) if ids else 'none'}")
    print(f"untested: {untested}")
    print(f"FAULT WEB: {'CLEAN' if problems == 0 else f'{problems} problems'}")
    return 0 if problems == 0 else 1


def cmd_observe(a):
    web = load(a.web)
    prunes = [x.strip() for x in (a.prunes or "").split(",") if x.strip()]
    supports = [x.strip() for x in (a.supports or "").split(",") if x.strip()]
    for cid in prunes:
        if find(web, cid)[1] is None:
            print(f"UNKNOWN: {cid}")
            return 2
        find(web, cid)[1]["status"] = "pruned"
    for cid in supports:
        if find(web, cid)[1] is None:
            print(f"UNKNOWN: {cid}")
            return 2
        find(web, cid)[1]["status"] = "supported"
    for p in web["plans"]:
        if p["text"] == a.text and not p.get("done"):
            p["done"] = True
    web["observations"].append(
        {"text": a.text, "result": a.result or "", "prunes": prunes, "supports": supports}
    )
    save(a.web, web)
    ids = surviving(web)
    print(f"surviving: {', '.join(ids) if ids else 'none'}")
    if ids:
        print("next observation: rank the plans and run the pick")
    else:
        print("converged: no survivor. Re-fan-out; the cause is outside the web.")
    return 0


def cmd_skip(a):
    web = load(a.web)
    layer, c = find(web, a.candidate)
    if c is None:
        print(f"UNKNOWN: {a.candidate}")
        return 2
    c["status"] = "not-tested"
    c["skip_reason"] = a.reason
    save(a.web, web)
    print(f"skipped {a.candidate}: {a.reason}")


def cmd_detail(a):
    web = load(a.web)
    layer, c = find(web, a.candidate)
    if c is None:
        print(f"UNKNOWN: {a.candidate}")
        return 2
    print(f"detail sweep for {c['id']} ({layer}): {c['cause']}")
    print(f"layer checks ({layer}):")
    for i, item in enumerate(DETAIL_BY_LAYER.get(layer, []), 1):
        print(f"  {i:>2}. {item}")
    print("generic checks:")
    for i, item in enumerate(DETAIL, 1):
        print(f"  {i:>2}. {item}")
    print("The fault is usually the smallest item. Check each one against the real system.")


def cmd_pass(a):
    web = load(a.web)
    fields = {
        "misleading": a.misleading,
        "outside": a.outside,
        "expert": a.expert,
        "changed": a.changed,
    }
    web["second_pass"] = fields
    save(a.web, web)
    print("second pass recorded:")
    for k, v in fields.items():
        print(f"  {k}: {v}")


def cmd_confirm(a):
    web = load(a.web)
    layer, c = find(web, a.cause)
    if c is None:
        print(f"UNKNOWN: {a.cause}")
        return 2
    if a.not_possible:
        web["confirmation"] = {"cause": a.cause, "not_possible": a.not_possible}
    else:
        fields = {
            "remove": a.remove,
            "symptom_gone": a.symptom_gone,
            "restore": a.restore,
            "symptom_returns": a.symptom_returns,
        }
        missing = [k for k, v in fields.items() if not v]
        if missing:
            print(f"MISSING: {', '.join(missing)}")
            return 2
        web["confirmation"] = {"cause": a.cause, **fields}
    save(a.web, web)
    print(f"confirmation recorded for {a.cause}")


def recorded_resolutions(web: dict, cause: str) -> dict[str, dict[str, str]]:
    return {
        entry["option"]: entry
        for entry in web["resolutions"]
        if entry["cause"] == cause and entry["option"] in RESOLUTIONS
    }


def cmd_resolve(a):
    web = load(a.web)
    layer, c = find(web, a.cause)
    if c is None:
        print(f"UNKNOWN: {a.cause}")
        return 2
    if a.option:
        if a.option not in RESOLUTIONS:
            print(f"UNKNOWN OPTION: {a.option}. Use one of: {', '.join(RESOLUTIONS)}")
            return 2
        web["resolutions"] = [
            entry for entry in web["resolutions"]
            if (entry["cause"], entry["option"]) != (a.cause, a.option)
        ]
        web["resolutions"].append(
            {
                "cause": a.cause,
                "option": a.option,
                "cost": a.cost or "",
                "risk": a.risk or "",
                "reversibility": a.reversibility or "",
            }
        )
        save(a.web, web)
        print(f"recorded {a.option} for {a.cause}")
        return 0
    print(f"resolutions for {c['id']} ({layer}): {c['cause']}")
    print(f"{'option':<14}{'cost':<8}{'risk':<8}{'reversibility':<14}")
    recorded = recorded_resolutions(web, a.cause)
    for r in RESOLUTIONS:
        if r in recorded:
            x = recorded[r]
            print(f"{r:<14}{x['cost']:<8}{x['risk']:<8}{x['reversibility']:<14}")
        else:
            print(f"{r:<14}{'':<8}{'':<8}{'':<14}")
    print("Fill cost, risk and reversibility. Pick the cheapest reversible option that fixes the cause.")


def cmd_pick(a):
    web = load(a.web)
    layer, c = find(web, a.cause)
    if c is None:
        print(f"UNKNOWN: {a.cause}")
        return 2
    opts = list(recorded_resolutions(web, a.cause).values())
    if len(opts) < len(RESOLUTIONS):
        print(f"INCOMPLETE: {a.cause} has {len(opts)} of {len(RESOLUTIONS)} distinct resolutions")
        return 1

    def score(r):
        return COST.get(r["cost"], 9) + COST.get(r["risk"], 9) + REV.get(r["reversibility"], 9)

    opts.sort(key=score)
    print(f"{'option':<14}{'cost':<8}{'risk':<8}{'reversibility':<14}{'score':<6}")
    for r in opts:
        print(f"{r['option']:<14}{r['cost']:<8}{r['risk']:<8}{r['reversibility']:<14}{score(r):<6}")
    print(f"pick: {opts[0]['option']}")
    print(f"what would change it: {opts[1]['option']} if its cost, risk or reversibility improves")


def cmd_scar_add(a):
    entry = {"symptom": a.symptom, "cause": a.cause, "fix": a.fix, "trap": a.trap}
    with open(a.file, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"scar added to {a.file}")


def cmd_scar_search(a):
    if not os.path.exists(a.file):
        print(f"no scar file at {a.file}")
        return 2
    matches = search_scars(a.file, a.query)
    if not matches:
        print(f"no scars matching {a.query!r}")
        return 0
    for m in matches:
        print(f"- {m['symptom']} -> {m['cause']} (fix: {m['fix']})")


def cmd_show(a):
    web = load(a.web)
    print(f"symptom: {web['symptom']}")
    print(f"middle: {', '.join(web['middle']) or 'not set'}")
    for layer, items in web["layers"].items():
        print(f"[{layer}]")
        for c in items:
            print(f"  {c['id']} {c['status']:<10} {c['cause']}  test: {c.get('test', '')}")
    for p in web["plans"]:
        print(f"plan: {p['text']} prunes {p['prunes']} supports {p['supports']} cost {p['cost']} done {p['done']}")
    for o in web["observations"]:
        print(f"obs: {o['text']} -> prunes {o['prunes']} supports {o['supports']}")
    for k, v in (web.get("second_pass") or {}).items():
        print(f"pass {k}: {v}")
    for k, v in (web.get("confirmation") or {}).items():
        print(f"confirm {k}: {v}")
    for r in web["resolutions"]:
        print(f"res: {r['cause']} {r['option']} cost={r['cost']} risk={r['risk']} rev={r['reversibility']}")


def cmd_demo(a):
    print("demo: internet down, router answers")
    print("middle: DNS, default route, gateway, upstream")
    print("plan: ping 192.0.2.1 -> prunes R1,R2 supports D1, cost low")
    print("rank: ping 192.0.2.1 splits the surviving set closest to half")
    print("observe: ping works -> IP and routing fine, DNS suspect")
    print("observe: nslookup fails -> DNS confirmed")
    print("detail: resolv.conf has a stale server with a trailing space")
    print("second pass: misleading=captive portal, outside=upstream DNS, expert=network engineer, changed=cert renewed")
    print("confirm: remove stale server -> resolution works; restore -> resolution fails")
    print("resolutions: direct fix, workaround, prevention, monitor only")
    print("pick: direct fix (cost low, risk low, reversibility easy)")


def main():
    p = argparse.ArgumentParser(description="Track a non-linear fault web.")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="create a web skeleton with every layer")
    i.add_argument("--symptom", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--scars")
    i.set_defaults(fn=cmd_init)

    ad = sub.add_parser("add", help="add a candidate to a layer")
    ad.add_argument("--web", required=True)
    ad.add_argument("--layer", required=True)
    ad.add_argument("--id", required=True)
    ad.add_argument("--cause", required=True)
    ad.add_argument("--test", required=True)
    ad.set_defaults(fn=cmd_add)

    m = sub.add_parser("middle", help="set the middle candidates")
    m.add_argument("--web", required=True)
    m.add_argument("--ids", required=True)
    m.set_defaults(fn=cmd_middle)

    pl = sub.add_parser("plan", help="add a planned observation with predicted effects")
    pl.add_argument("--web", required=True)
    pl.add_argument("--text", required=True)
    pl.add_argument("--prunes")
    pl.add_argument("--supports")
    pl.add_argument("--cost", default="low")
    pl.set_defaults(fn=cmd_plan)

    up = sub.add_parser("unplan", help="remove a planned observation by text")
    up.add_argument("--web", required=True)
    up.add_argument("--text", required=True)
    up.set_defaults(fn=cmd_unplan)

    rk = sub.add_parser("rank", help="rank planned observations by split and cost")
    rk.add_argument("--web", required=True)
    rk.set_defaults(fn=cmd_rank)

    c = sub.add_parser("check", help="report every gap in the web")
    c.add_argument("--web", required=True)
    c.set_defaults(fn=cmd_check)

    o = sub.add_parser("observe", help="add an observation and prune or support candidates")
    o.add_argument("--web", required=True)
    o.add_argument("--text", required=True)
    o.add_argument("--prunes")
    o.add_argument("--supports")
    o.add_argument("--result")
    o.set_defaults(fn=cmd_observe)

    sk = sub.add_parser("skip", help="mark a candidate not-tested with a reason")
    sk.add_argument("--web", required=True)
    sk.add_argument("--candidate", required=True)
    sk.add_argument("--reason", required=True)
    sk.set_defaults(fn=cmd_skip)

    d = sub.add_parser("detail", help="print the layer and generic detail checklists")
    d.add_argument("--web", required=True)
    d.add_argument("--candidate", required=True)
    d.set_defaults(fn=cmd_detail)

    sp = sub.add_parser("pass", help="record the second-pass answers")
    sp.add_argument("--web", required=True)
    sp.add_argument("--misleading", required=True)
    sp.add_argument("--outside", required=True)
    sp.add_argument("--expert", required=True)
    sp.add_argument("--changed", required=True)
    sp.set_defaults(fn=cmd_pass)

    cf = sub.add_parser("confirm", help="record the confirmation experiment")
    cf.add_argument("--web", required=True)
    cf.add_argument("--cause", required=True)
    cf.add_argument("--remove")
    cf.add_argument("--symptom-gone")
    cf.add_argument("--restore")
    cf.add_argument("--symptom-returns")
    cf.add_argument("--not-possible")
    cf.set_defaults(fn=cmd_confirm)

    r = sub.add_parser("resolve", help="print or record the creative resolution options")
    r.add_argument("--web", required=True)
    r.add_argument("--cause", required=True)
    r.add_argument("--option")
    r.add_argument("--cost")
    r.add_argument("--risk")
    r.add_argument("--reversibility")
    r.set_defaults(fn=cmd_resolve)

    pk = sub.add_parser("pick", help="rank recorded resolutions and name the pick")
    pk.add_argument("--web", required=True)
    pk.add_argument("--cause", required=True)
    pk.set_defaults(fn=cmd_pick)

    sc = sub.add_parser("scar", help="consult or add to the scar library")
    scs = sc.add_subparsers(dest="scar_cmd", required=True)
    sa = scs.add_parser("add", help="add a fault to the scar library")
    sa.add_argument("--file", required=True)
    sa.add_argument("--symptom", required=True)
    sa.add_argument("--cause", required=True)
    sa.add_argument("--fix", required=True)
    sa.add_argument("--trap", required=True)
    sa.set_defaults(fn=cmd_scar_add)
    ss = scs.add_parser("search", help="search the scar library")
    ss.add_argument("--file", required=True)
    ss.add_argument("--query", required=True)
    ss.set_defaults(fn=cmd_scar_search)

    s = sub.add_parser("show", help="print the current web")
    s.add_argument("--web", required=True)
    s.set_defaults(fn=cmd_show)

    dm = sub.add_parser("demo", help="print a runnable worked example")
    dm.set_defaults(fn=cmd_demo)

    a = p.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
