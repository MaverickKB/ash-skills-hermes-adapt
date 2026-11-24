---
name: think-like-ash
description: Diagnoses a fault the way a fast human does, by fanning out a web of candidate causes across every layer, jumping to the middle of the stack, converging by pruning, sweeping the tiny details, taking a second pass, then generating creative resolutions. Ships a stdlib fault-web tracker (`think_like_ash.py`) that enforces layer coverage, tracks convergence, and prints the detail and resolution checklists. Use when a fault has no obvious cause, when a linear checklist keeps missing it, when the symptom is misleading, or when you want a non-linear, detail-obsessed diagnosis.
metadata:
  version: "1.0.0"
---

# Think like Ash

A linear checklist starts at the bottom of the stack and walks up. A fast human does not. They jump from the symptom to a web of causes, land in the middle where the fault usually lives, prune by observation, and find the fault in a tiny detail. This skill makes that style explicit and keeps it honest.

## When to use

- A fault has no obvious cause, or a linear checklist keeps missing it.
- The symptom is misleading, such as "internet is down" when the router answers.
- The fault is intermittent, or only one machine sees it.
- You want a second pass and more than one resolution.

For a bug with a clean reproducer, `diagnosing-bugs` is faster. This skill is for the messy, non-linear case.

## The shape

Six phases. Do not skip the fan-out or the second pass; those are where the creative work happens.

1. **Fan out.** From the symptom, write candidate causes across every layer. Do not order them. Include the tiny-detail candidates.
2. **Jump to the middle.** Pick the layer where the fault most likely lives, not the bottom or the top. Start there.
3. **Converge.** Each observation prunes or supports candidates. Converge on the smallest set consistent with all evidence.
4. **Sweep details.** For each survivor, check the tiny details. The fault is usually the smallest item.
5. **Second pass.** Re-frame the problem. What if the symptom is misleading? What is outside the web? What would a different expert check first?
6. **Resolve creatively.** Generate a direct fix, a workaround, a prevention, and a monitor-only option. Score cost, risk, and reversibility. Pick one.

## The fault web

Every layer gets at least one candidate. An empty layer is an unturned stone.

| Layer | Example candidates |
|---|---|
| physical | cable, Wi-Fi signal, NIC, power |
| link | switch port, VLAN, MAC, duplex |
| ip | no address, wrong subnet, wrong gateway, duplicate address, DHCP |
| dns | wrong server, stale cache, resolution failure, hijack |
| routing | no default route, wrong route, VPN split tunnel, upstream |
| transport | firewall, MTU, port block, NAT |
| application | proxy, captive portal, browser config, service config |
| credentials | expired cert, auth, token, permission |
| time | clock skew, expired lease, certificate validity |
| data | stale hosts file, wrong config, corrupt cache |
| human | typo, wrong interface, wrong file, wrong machine |

## Jump to the middle

The middle is the boundary where the symptom changes state. For "internet is down but the router answers", the link and IP-to-router are fine, so the middle is the boundary between the local network and the internet: DNS, default route, gateway, upstream. Start there, not at the cable.

## Converge

- One observation at a time. Name what it prunes and what it supports.
- Prefer the observation that splits the surviving set in half. A check that cannot change the answer is not worth running.
- Add planned observations with `plan` and rank them with `rank`; the pick splits the surviving set closest to half at the lowest cost.
- When no candidate survives, the cause is outside the web. Re-fan-out; do not force a survivor.
- Keep the web. The record of what was ruled out is the diagnosis.

## Confirm the cause

Converging is not confirming. Remove the cause and watch the symptom disappear; restore it and watch the symptom return. Record both halves with `confirm`. If you cannot run the experiment, record why with `confirm --not-possible`; `check` still reports the gap, because an unconfirmed cause is not a diagnosis.

## Sweep details

For each survivor, check the smallest things first:

exact string and case, leading and trailing whitespace, hidden characters, path and symlink target, port and protocol, version and build, permission and owner, timestamp and timezone, encoding and line endings, default value, stale cache or entry, duplicate or conflicting entry, boundary and off-by-one, units and scale, locale and format.

The fault is usually one of these. A correct-looking config with a trailing space is still wrong.

## Untested accounting

A candidate is untested until an observation prunes or supports it. `check` counts untested candidates and refuses `CLEAN`. Skip one only with a reason, using `skip`. A web that is complete but untested is a ritual, not a diagnosis.

## Second pass

Before you fix, think twice:

- What if the symptom is misleading? Ping works but the router is a captive portal. DNS is fine but the clock is wrong.
- What is outside the web? A dependency, an upstream service, a physical cause, a human step.
- What would a different expert check first? Ask what a network engineer, a database engineer, or the person who built it would look at.
- What changed recently? A deploy, a certificate, a dependency, a config, a clock.

Add the missing candidates to the web before you converge again. Record the four answers with `pass`; `check` fails until they are there.

## Resolve creatively

For the confirmed cause, write four options:

| Option | Question |
|---|---|
| direct fix | What removes the cause? |
| workaround | What restores service without the fix? |
| prevention | What stops the next occurrence? |
| monitor only | Is it transient, and what would tell you it returned? |

Score each by cost, risk, and reversibility. Pick the cheapest reversible option that fixes the cause. A workaround that hides the cause is not a fix.

## Pick the resolution

Record all four options with `resolve`. Recording the same option again updates its scores. `pick` requires four distinct options, ranks them by cost, risk, and reversibility, and names what would change the choice. A pick with no runner-up is a guess.

## Scar library

Consult before starting: `init --scars scars.jsonl` prints past faults that match the symptom. Add after: `scar add` stores the symptom, cause, fix, and trap case. The library compounds; a fault you hit twice is a fault you did not learn.

## Traps

| Trap | Tell |
|---|---|
| Jumping without a web | You have one theory and no ruled-out list |
| Converging on the first survivor | You stopped when one candidate fit |
| Stopping at the fix | You never swept details or took the second pass |
| Skipping the second pass | You never asked what is outside the web |
| Complete but untested | Every layer has a candidate and none was run |
| Converged but unconfirmed | The cause fits and the symptom was never removed |
| Hiding with a workaround | Service is back but the cause is still there |
| Forcing a survivor | The web is empty and you picked the least bad |
| Walking the stack | You started at the cable, not the middle |

## Script

`scripts/think_like_ash.py` needs Python 3.9+ and only the standard library. It builds the web, enforces coverage, tracks convergence, and records resolutions. Run it with `--help` for all commands.

```bash
python3 scripts/think_like_ash.py init --symptom "internet down, router answers" --out web.json --scars scars.jsonl
python3 scripts/think_like_ash.py add --web web.json --layer dns --id D1 --cause "stale resolver" --test "nslookup"
python3 scripts/think_like_ash.py middle --web web.json --ids D1,R1
python3 scripts/think_like_ash.py plan --web web.json --text "ping 192.0.2.1" --prunes R1,R2 --supports D1 --cost low
python3 scripts/think_like_ash.py unplan --web web.json --text "curl upstream"
python3 scripts/think_like_ash.py rank --web web.json
python3 scripts/think_like_ash.py observe --web web.json --text "ping 192.0.2.1" --prunes R1,R2 --supports D1
python3 scripts/think_like_ash.py skip --web web.json --candidate P1 --reason "link light is on"
python3 scripts/think_like_ash.py detail --web web.json --candidate D1
python3 scripts/think_like_ash.py pass --web web.json --misleading "captive portal" --outside "upstream DNS" --expert "network engineer: resolver" --changed "cert renewed yesterday"
python3 scripts/think_like_ash.py confirm --web web.json --cause D1 --remove "stale server" --symptom-gone "resolution works" --restore "stale server" --symptom-returns "resolution fails"
python3 scripts/think_like_ash.py resolve --web web.json --cause D1 --option "direct fix" --cost low --risk low --reversibility easy
python3 scripts/think_like_ash.py pick --web web.json --cause D1
python3 scripts/think_like_ash.py scar add --file scars.jsonl --symptom "internet down" --cause "stale resolver" --fix "correct resolv.conf" --trap "trailing space"
```

`check` exits 0 when every layer has a candidate, every candidate has a cause and a test, every id is unique, no candidate is untested, every observation changes the web, a live middle is set, the second pass answers all four questions, the confirmation is recorded, and every cause with resolutions has all four. It exits 1 on any gap. `add`, `middle`, `plan`, `unplan`, `observe`, `skip`, `confirm`, `resolve`, and `pick` exit 2 on an unknown layer, id, or option.

## Worked example

Symptom: internet is down, but the router answers.

1. Fan out: physical, link, ip, dns, routing, transport, application, credentials, time, data, human.
2. Jump to the middle: DNS, default route, gateway, upstream.
3. Converge: `ping 192.0.2.1` works, so IP and routing are fine and DNS is suspect. `nslookup` fails, so DNS is confirmed. `cat /etc/resolv.conf` shows a stale server.
4. Sweep details: the stale server has a trailing space, or the file is a symlink to the wrong place, or the address is a typo.
5. Second pass: what if the DNS server is reachable but not responding? What if the clock is wrong and the resolver is fine? What if IPv6 is the real path?
6. Resolve: direct fix the resolver config, workaround with a public resolver, prevent with a pinned config and a check, monitor if it is transient.

## Rules

- Do not walk the stack in order. Jump to the middle.
- Do not stop at the first survivor. Sweep the details and take the second pass.
- Do not force a survivor when the web is empty. Re-fan-out.
- Do not hide the cause with a workaround. Name it and fix it, or say why you did not.
- Keep the web and the observations. The ruled-out list is the evidence.
