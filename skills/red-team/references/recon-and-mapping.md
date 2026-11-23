# Recon and mapping

This guide covers steps 3 and 4 of the method in `SKILL.md`: passive reconnaissance, then a map of the attack surface. It stays at the level of standard, published testing methodology.

The scope rule applies to every step here. Check before each action and log after it.

## Contents

- Before you start
- Passive reconnaissance
- Resolve names and check the addresses
- Active enumeration
- Map the attack surface
- Log each step
- When to stop

## Before you start

- The scope file validates, and the testing window is open.
- You know the data handling rules and the stop conditions.
- Each tool you plan to use exists. Check, and read its help before you rely on a flag:

```sh
for tool in dig whois curl openssl nmap; do command -v "$tool" >/dev/null || echo "missing: $tool"; done
```

If a tool is missing, tell the operator. Do not install software without asking. Flags differ between versions, so trust the help text on the machine over this guide.

## Passive reconnaissance

Technique class: `passive-recon`. Nothing is sent to the target. Look up only names and addresses that are in scope.

Sources, in a sensible order:

1. Documents from the operator: asset lists, network diagrams, earlier reports, and the published rules of a vendor program.
2. Registration records for a domain or an address block:

```sh
whois example.com
```

3. Public DNS records, through the resolver the operator named:

```sh
dig +short example.com A
dig +short example.com AAAA
dig +short example.com NS
dig +short example.com MX
dig +short example.com TXT
```

4. Certificate transparency logs. Search a public log search service for certificates issued for the in-scope domain. They often list more host names.
5. Public code and documentation: public repositories, package registries, published API documents and job posts of the organization.
6. Public indexes of internet-facing services. Read what they already hold about an in-scope address. Do not ask them to scan anything.

Notes on what counts as passive:

- A normal DNS lookup goes to your resolver. That is ordinary traffic, and most engagements treat it as passive. If the scope file says otherwise, follow the scope file.
- A query sent straight to the target's own name server is active. So is a zone transfer attempt, and so is guessing names from a word list. Use `active-scan` for these, and check the name server as a target first.
- If you find a password, key or token in a public source, do not use it. Record where it was found, redact it, and tell the operator. Using it needs the operator's yes.

Every name or address you find is a candidate, not a target. A name is not in scope because it looks related. Run the scope check on it. If it is refused, write it under open questions in the state file and leave it alone.

## Resolve names and check the addresses

The scope checker never resolves a name. Resolving is the operator's job, or yours with the resolver the operator named.

```sh
dig +short app.example.com A
dig +short app.example.com AAAA
```

Then check every address that comes back:

```sh
python3 "$SKILL_DIR/scripts/scope_check.py" --scope scope.json \
  --target 192.0.2.10 --technique active-scan
```

- If an address is refused, do not touch it. It may be a shared host or a delivery network that belongs to someone else. Ask the operator.
- Record the resolved addresses and the time in the state file. DNS answers change. Resolve again at the start of each session.
- Where a tool allows it, point the tool at the address you checked, not at the name.

## Active enumeration

Technique class: `active-scan`. This is non-intrusive enumeration. It goes ahead inside scope without a separate approval, at a low rate.

Find which hosts answer in an in-scope range:

```sh
nmap -n -sn 192.0.2.0/28
```

List open ports and service versions on one checked address:

```sh
nmap -n -Pn -sT -sV -p 22,80,443 --max-rate 50 -oN evidence/ports-192.0.2.10.txt 192.0.2.10
```

- `-n` stops the scanner from resolving names, so it touches only the address you checked.
- `--max-rate` keeps the load low. Start with a few ports. Widen the list only when you need to.
- `-sT` makes ordinary connections and needs no special privileges.
- Check the range itself with the scope checker before a range scan. A range that overlaps an out-of-scope entry is refused.

Read the response headers of a web server:

```sh
curl -sS -I --max-time 10 https://app.example.com/
```

Do not add the flag that follows redirects. If the answer points to another host, check that host first. To make the request go to the address you checked, add `--resolve app.example.com:443:192.0.2.10`.

Read the certificate that a server presents:

```sh
openssl s_client -connect app.example.com:443 -servername app.example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

Names inside a certificate are candidates. Check each one before you use it.

For a web application, browse it as a normal user would and note what you see. Reading the public pages, the robots file and the site map is enumeration. Guessing paths from a word list sends many requests. Keep the rate low and stop at the first sign of trouble.

Stay away from out-of-scope paths. When the scope check prints a note about out-of-scope paths on a host, tell your tools to exclude them.

## Map the attack surface

Write the map into the state file as you go. For each target, record:

| Item | What to record |
|---|---|
| Target | The name or address, and the scope rule it matched |
| Addresses | Resolved addresses, with the time they were resolved |
| Services | Port, protocol, product and version as observed, and how it was observed |
| Entry points | Sign-in pages, forms, upload points, API endpoints, admin interfaces |
| Authentication | What needs an account, what does not, and which test accounts you were given |
| Technology | Server software, frameworks, and third-party components with versions |
| Trust boundaries | Where data crosses from one system or privilege level to another |
| Data | What kind of data the service handles, as far as you can tell without reading it |

For other kinds of target:

- Cloud account (`cloud-review`): read the configuration through the provider's own interface, with the read-only credentials the operator supplied. List public endpoints, storage, identities and their permissions.
- Repository (`source-review`): list languages, dependency manifests, build and deploy configuration, and places where secrets appear. Report the location of a secret, not its value.
- Binary, firmware or device: see `binary-and-firmware.md`.

## Log each step

```sh
python3 "$SKILL_DIR/scripts/engagement_log.py" log --log engagement_log.jsonl \
  --scope scope.json --target 192.0.2.10 --technique active-scan \
  --command "nmap -n -Pn -sT -sV -p 22,80,443 --max-rate 50 -oN evidence/ports-192.0.2.10.txt 192.0.2.10" \
  --output evidence/ports-192.0.2.10.txt
```

## When to stop

Stop, log a `note` with the label `stop`, and tell the operator when:

- an address or name turns out to belong to someone else
- a service slows down, returns errors, or locks an account
- a response contains personal data
- you are blocked or rate limited. Do not try to get around a block.
