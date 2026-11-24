#!/usr/bin/env python3
"""Check whether a change to an LLM inference server changed what the model outputs.

The tool sends the same chat requests to a base and a candidate, one request at
a time, and compares the replies. It works with any OpenAI-compatible chat
endpoint and needs only the Python standard library.

  parity.py record  --url URL --model M --prompts FILE --out base.json
  parity.py compare --url URL --model M --against base.json
  parity.py live    --base-url A --candidate-url B --model M --prompts FILE

Before it compares anything it runs controls on the base, because a comparison
that cannot fail proves nothing. The last line of output is the verdict.

Exit status: 0 identical, 1 different, 2 usage or endpoint error, 3 inconclusive.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import http.client
import json
import math
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

PROG = "parity.py"
RECORDING_FORMAT = "output-parity-recording"
RECORDING_VERSION = 2
# Request fields the tool sets itself. Extra JSON may not override them.
RESERVED_KEYS = ("model", "messages", "temperature", "seed", "max_tokens", "logprobs", "stream")
DEFAULT_SEEDS = [0, 1]
DEFAULT_TEMPERATURES = [0, 0.8]
DEFAULT_MAX_TOKENS = 256
DEFAULT_TIMEOUT = 120.0
EXCERPT_CHARS = 24
EXCERPT_TOKENS = 3
MAX_DIFFS_SHOWN = 10
EXIT_IDENTICAL, EXIT_DIFFERENT, EXIT_ERROR, EXIT_INCONCLUSIVE = 0, 1, 2, 3
# Some gateways refuse the default urllib client name, so send a neutral one.
USER_AGENT = "output-parity/1"
# Before Python 3.10 a socket timeout is not a TimeoutError.
TIMEOUTS = (TimeoutError, socket.timeout)

EPILOG = (
    "Run 'parity.py SUBCOMMAND --help' for the options of one subcommand.\n\n"
    "controls, run on the base before the comparison:\n"
    "  1 the same seed twice at a temperature above 0 must match\n"
    "  2 different seeds at a temperature above 0 must differ on at least one prompt\n"
    "  3 temperature 0 twice must match\n"
    "a failed control leaves the cells it covers unjudged, and the result is INCONCLUSIVE\n\n"
    "last line of output:\n"
    "  PARITY: IDENTICAL (N of N)           exit 0\n"
    "  PARITY: DIFFERENT (K of N differ)    exit 1\n"
    "  PARITY: INCONCLUSIVE (reason)        exit 3\n"
    "  PARITY: RECORDED (N cells, ...)      exit 0, from 'record' when its controls pass\n"
    "usage and endpoint errors print one line on standard error and exit 2"
)

_secrets = []


class Fail(Exception):
    """A usage or endpoint error. It is printed as one line and the tool exits 2."""


class OneLineParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(EXIT_ERROR, f"{PROG}: error: {message}\n")


# ---------------------------------------------------------------- small helpers

def say(line=""):
    print(line, flush=True)


def one_line(text, limit=200):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "..."


def redact(text):
    for secret in _secrets:
        text = text.replace(secret, "[key hidden]")
    return text


def number(value):
    """Show 0.0 as 0 and 0.8 as 0.8."""
    return f"{value:g}"


def listing(values):
    return ",".join(number(value) for value in values)


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ------------------------------------------------------------- option parsing

def seed_list(text):
    try:
        seeds = [int(part) for part in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected whole numbers separated by commas, got '{text}'")
    if any(seed < 0 for seed in seeds):
        raise argparse.ArgumentTypeError("seeds must be 0 or more")
    if len(set(seeds)) != len(seeds):
        raise argparse.ArgumentTypeError("a seed is listed twice")
    return seeds


def temperature_list(text):
    try:
        values = [float(part) for part in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected numbers separated by commas, got '{text}'")
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise argparse.ArgumentTypeError("temperatures must be 0 or more")
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("a temperature is listed twice")
    return [int(value) if value == int(value) else value for value in values]


def positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got '{text}'")
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def positive_seconds(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of seconds, got '{text}'")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return value


def json_object(text):
    try:
        value = json.loads(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not valid JSON")
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("expected a JSON object such as '{\"top_p\": 0.9}'")
    reserved = sorted(set(value) & set(RESERVED_KEYS))
    if reserved:
        raise argparse.ArgumentTypeError(f"may not set {', '.join(reserved)}; the tool sets those fields itself")
    return value


def add_common(parser, replay=False):
    """Options shared by the three subcommands. In replay mode they default to the recording."""
    fallback = "the recording's value" if replay else None
    group = parser.add_argument_group(
        "request settings",
        "Checked against the recording; leave them out to replay it as recorded." if replay else None)
    group.add_argument("--seeds", type=seed_list, metavar="LIST",
                       help=f"comma-separated seeds (default: {fallback or listing(DEFAULT_SEEDS)})")
    group.add_argument("--temperatures", type=temperature_list, metavar="LIST",
                       help="comma-separated temperatures; 0 is greedy "
                            f"(default: {fallback or listing(DEFAULT_TEMPERATURES)})")
    group.add_argument("--max-tokens", type=positive_int, metavar="N",
                       help=f"max_tokens for every request (default: {fallback or DEFAULT_MAX_TOKENS})")
    group.add_argument("--logprobs", action="store_true",
                       help="send logprobs: true and compare the token sequence when the server returns token lists")
    group.add_argument("--extra-json", type=json_object, metavar="JSON",
                       help="JSON object merged into every request body of both arms")
    other = parser.add_argument_group("connection and output")
    other.add_argument("--api-key-env", metavar="NAME",
                       help="name of the environment variable that holds the API key; "
                            "sent as a bearer header and never printed")
    other.add_argument("--timeout", type=positive_seconds, default=DEFAULT_TIMEOUT, metavar="S",
                       help=f"seconds to wait for one reply (default: {number(DEFAULT_TIMEOUT)})")
    other.add_argument("--json", metavar="OUT", help="write the full result to this file as JSON")
    other.add_argument("--progress", action="store_true", help="print one line per request on standard error")


def build_parser():
    parser = OneLineParser(prog=PROG, description=__doc__, epilog=EPILOG,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", metavar="{record,compare,live}", parser_class=OneLineParser)
    commands.required = True
    raw = argparse.RawDescriptionHelpFormatter

    record = commands.add_parser(
        "record", formatter_class=raw,
        help="record one build's replies, with the controls, for a later compare",
        description="Send every prompt at every temperature and seed to URL, run the controls there, and save the\n"
                    "replies and the control results to a recording.",
        epilog="example:\n  parity.py record --url http://localhost:8000/v1 --model my-model \\\n"
               "      --prompts prompts.jsonl --out base.json\n\n"
               "exit status: 0 recorded and controls passed, 3 recorded but a control failed, 2 error")
    record.add_argument("--url", required=True,
                        help="API base such as http://localhost:8000/v1, or a full .../chat/completions URL")
    record.add_argument("--model", required=True, help="model name sent in every request")
    record.add_argument("--prompts", required=True, metavar="FILE",
                        help="JSONL file, one {\"id\", \"messages\"} or {\"id\", \"prompt\"} object per line")
    record.add_argument("--out", required=True, metavar="FILE", help="where to write the recording")
    record.add_argument("--overwrite", action="store_true", help="replace --out if it already exists")
    record.add_argument("--base-extra-json", type=json_object, metavar="JSON",
                        help="JSON object merged into the recorded arm only, on top of --extra-json")
    add_common(record)
    record.set_defaults(run=run_record)

    compare = commands.add_parser(
        "compare", formatter_class=raw,
        help="replay a recording's requests against a server and compare",
        description="Replay the recorded requests against URL and compare each reply with the recording.\n"
                    "The model, prompt set and request settings must equal the recording's, or the run is refused.\n"
                    "The controls stored in the recording are shown first and decide which cells can be judged.",
        epilog="example:\n  parity.py compare --url http://localhost:8000/v1 --model my-model --against base.json\n\n"
               "exit status: 0 identical, 1 different, 3 inconclusive, 2 error")
    compare.add_argument("--url", required=True, help="the candidate: an API base or a full .../chat/completions URL")
    compare.add_argument("--model", required=True, help="model name; must equal the recording's")
    compare.add_argument("--against", required=True, metavar="FILE", help="a recording made by 'record'")
    compare.add_argument("--prompts", metavar="FILE",
                         help="optional: check that this prompt file is the recorded prompt set")
    compare.add_argument("--base-extra-json", type=json_object, metavar="JSON",
                         help="optional: check that the recording was made with this base-only JSON")
    compare.add_argument("--candidate-extra-json", type=json_object, metavar="JSON",
                         help="JSON object merged into the candidate's requests only, on top of --extra-json")
    add_common(compare, replay=True)
    compare.set_defaults(run=run_compare)

    live = commands.add_parser(
        "live", formatter_class=raw,
        help="compare two servers that are up at the same time",
        description="Send the same requests to a base and a candidate and compare the replies. The two URLs may be\n"
                    "the same server when the arms differ by a request field, set with the per-arm JSON options.\n"
                    "The controls run on the base first, so every base request is sent twice.",
        epilog="examples:\n"
               "  parity.py live --base-url http://localhost:8000/v1 --candidate-url http://localhost:8001/v1 \\\n"
               "      --model my-model --prompts prompts.jsonl\n"
               "  parity.py live --base-url http://localhost:8000/v1 --candidate-url http://localhost:8000/v1 \\\n"
               "      --model my-model --prompts prompts.jsonl \\\n"
               "      --base-extra-json '{\"speculative\": false}' --candidate-extra-json '{\"speculative\": true}'\n\n"
               "exit status: 0 identical, 1 different, 3 inconclusive, 2 error")
    live.add_argument("--base-url", required=True, help="the base: an API base or a full .../chat/completions URL")
    live.add_argument("--candidate-url", required=True, help="the candidate, in the same form")
    live.add_argument("--model", required=True, help="model name sent in every request")
    live.add_argument("--prompts", required=True, metavar="FILE",
                      help="JSONL file, one {\"id\", \"messages\"} or {\"id\", \"prompt\"} object per line")
    live.add_argument("--base-extra-json", type=json_object, metavar="JSON",
                      help="JSON object merged into the base's requests only, on top of --extra-json")
    live.add_argument("--candidate-extra-json", type=json_object, metavar="JSON",
                      help="JSON object merged into the candidate's requests only, on top of --extra-json")
    add_common(live)
    live.set_defaults(run=run_live)
    return parser


# -------------------------------------------------------------------- prompts

def load_prompts(path):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as error:
        raise Fail(f"cannot read prompts file {path}: {error.strerror or error}")
    except UnicodeDecodeError:
        raise Fail(f"prompts file {path} is not UTF-8 text")
    prompts, seen = [], set()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        where = f"{path} line {line_number}"
        try:
            item = json.loads(line)
        except ValueError:
            raise Fail(f"{where}: not valid JSON")
        prompts.append(normal_prompt(item, where, seen))
    if not prompts:
        raise Fail(f"{path} holds no prompts")
    return prompts


def normal_prompt(item, where, seen):
    """Check one prompt object and return it as {"id", "messages"}."""
    if not isinstance(item, dict):
        raise Fail(f"{where}: expected a JSON object")
    ident = item.get("id")
    if not isinstance(ident, str) or not ident.strip():
        raise Fail(f"{where}: needs a non-empty string \"id\"")
    if ident in seen:
        raise Fail(f"{where}: id '{ident}' is used twice")
    seen.add(ident)
    if ("messages" in item) == ("prompt" in item):
        raise Fail(f"{where}: needs exactly one of \"messages\" or \"prompt\"")
    if "prompt" in item:
        if not isinstance(item["prompt"], str) or not item["prompt"]:
            raise Fail(f"{where}: \"prompt\" must be a non-empty string")
        return {"id": ident, "messages": [{"role": "user", "content": item["prompt"]}]}
    messages = item["messages"]
    if not isinstance(messages, list) or not messages or not all(
            isinstance(message, dict) and isinstance(message.get("role"), str) and "content" in message
            for message in messages):
        raise Fail(f"{where}: \"messages\" must be a non-empty list of objects with \"role\" and \"content\"")
    return {"id": ident, "messages": messages}


def prompts_hash(prompts):
    """Hash of the prompt set as it is sent: ids, messages and their order."""
    canonical = json.dumps(prompts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- endpoint

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect, so the API key cannot reach a host the user did not name."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_api_key(name):
    if not name:
        return None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise Fail("--api-key-env takes the name of an environment variable, not the key itself")
    key = os.environ.get(name, "").strip()
    if not key:
        raise Fail("the environment variable named by --api-key-env is not set or is empty")
    if not (key.isascii() and key.isprintable()):
        raise Fail("the API key holds a character that cannot go in an HTTP header")
    _secrets.append(key)
    return key


def endpoint_url(text, option):
    """Turn an API base or a full URL into the chat completions URL."""
    try:
        parts = urllib.parse.urlsplit(text)
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise Fail(f"{option} is not a valid URL")
    if not text.isascii() or not text.isprintable() or " " in text:
        raise Fail(f"{option} is not a valid URL; it holds a space or a character outside ASCII")
    if parts.scheme not in ("http", "https") or not host:
        raise Fail(f"{option} must be an http or https URL such as http://localhost:8000/v1")
    if parts.username is not None or parts.password is not None:
        raise Fail(f"{option} must not carry a user name or password; use --api-key-env")
    path = parts.path.rstrip("/")
    if not path:
        path = "/v1/chat/completions"
    elif not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


class Endpoint:
    def __init__(self, url, option, api_key, timeout):
        self.url = endpoint_url(url, option)
        parts = urllib.parse.urlsplit(self.url)
        # The query string can hold a key, so it is sent but never shown or stored.
        self.shown = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        self.api_key = api_key
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect)

    def ask(self, body):
        """POST one request and return the parts of the reply that are compared."""
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            try:
                detail = one_line(error.read(600).decode("utf-8", "replace"))
            except (OSError, http.client.HTTPException):
                detail = ""
            finally:
                error.close()
            if 300 <= error.code < 400:
                detail = "redirects are not followed; give the final URL"
            raise Fail(f"{self.shown} returned HTTP {error.code}" + (f": {detail}" if detail else ""))
        except TIMEOUTS:
            raise Fail(f"{self.shown} did not answer within {number(self.timeout)} seconds")
        except urllib.error.URLError as error:
            if isinstance(error.reason, TIMEOUTS):
                raise Fail(f"{self.shown} did not answer within {number(self.timeout)} seconds")
            reason = getattr(error.reason, "strerror", None) or error.reason
            raise Fail(f"cannot reach {self.shown}: {one_line(reason)}")
        except (OSError, http.client.HTTPException) as error:
            raise Fail(f"the request to {self.shown} failed: {one_line(error) or type(error).__name__}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise Fail(f"{self.shown} returned a response that is not JSON")
        return parse_reply(payload, self.shown)


def valid_token_bytes(tokens, vectors):
    if vectors is None:
        return True
    return (isinstance(tokens, list) and isinstance(vectors, list) and len(vectors) == len(tokens)
            and all(v is None or (isinstance(v, list) and all(is_int(b) and 0 <= b <= 255 for b in v))
                    for v in vectors))


def parse_reply(payload, shown):
    """Pick the compared parts out of a chat completion response."""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        detail = ""
        if isinstance(payload, dict) and payload.get("error"):
            error = payload["error"]
            detail = ": " + one_line(error.get("message", error) if isinstance(error, dict) else error)
        raise Fail(f"{shown} returned JSON without choices[0].message{detail}")
    text = message.get("content")
    if text is None:
        text = ""
    elif not isinstance(text, str):
        text = json.dumps(text, sort_keys=True)
    reasoning = message.get("reasoning_content")
    if reasoning is None:
        reasoning = message.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, str):
        reasoning = json.dumps(reasoning, sort_keys=True)
    tokens = None
    token_bytes = None
    logprobs = choice.get("logprobs")
    items = logprobs.get("content") if isinstance(logprobs, dict) else None
    if isinstance(items, list) and items and all(
            isinstance(item, dict) and isinstance(item.get("token"), str) for item in items):
        tokens = [item["token"] for item in items]
        vectors = [item.get("bytes") for item in items]
        if any(v is not None for v in vectors):
            if not valid_token_bytes(tokens, vectors):
                raise Fail(f"{shown} returned invalid bytes in a logprobs token")
            token_bytes = vectors
    usage = payload.get("usage")
    count = usage.get("completion_tokens") if isinstance(usage, dict) else None
    finish = choice.get("finish_reason")
    return {
        "text": text,
        "reasoning": reasoning,
        "finish_reason": finish if isinstance(finish, str) else None,
        "completion_tokens": count if is_int(count) else None,
        "tokens": tokens,
        "token_bytes": token_bytes,
    }


# ------------------------------------------------------------------ comparing

def first_difference(left, right):
    """Index of the first position where two sequences differ, or None when they are equal."""
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return None if len(left) == len(right) else min(len(left), len(right))


def text_excerpt(text, index):
    start, end = max(0, index - EXCERPT_CHARS), min(len(text), index + EXCERPT_CHARS)
    return ("..." if start else "") + repr(text[start:end]) + ("..." if end < len(text) else "")


def token_excerpt(tokens, index):
    start, end = max(0, index - EXCERPT_TOKENS), min(len(tokens), index + EXCERPT_TOKENS + 1)
    return ("... " if start else "") + json.dumps(tokens[start:end]) + (" ..." if end < len(tokens) else "")


def first_token_byte_difference(replies):
    sequences = [reply["token_bytes"] for reply in replies if reply.get("token_bytes") is not None]
    if len(sequences) >= 2:
        for index, column in enumerate(zip(*sequences)):
            known = [value for value in column if value is not None]
            if any(value != known[0] for value in known[1:]):
                return index
    return None


def differences(base, candidate):
    """Every compared field that differs between two replies. An empty list means they match."""
    found = []
    for field in ("text", "reasoning"):
        left, right = base[field] or "", candidate[field] or ""
        index = first_difference(left, right)
        if index is not None:
            found.append({"field": field, "index": index,
                          "base": text_excerpt(left, index), "candidate": text_excerpt(right, index)})
    if base["finish_reason"] != candidate["finish_reason"]:
        found.append({"field": "finish_reason", "base": base["finish_reason"],
                      "candidate": candidate["finish_reason"]})
    if base["completion_tokens"] is not None and candidate["completion_tokens"] is not None \
            and base["completion_tokens"] != candidate["completion_tokens"]:
        found.append({"field": "completion_tokens", "base": base["completion_tokens"],
                      "candidate": candidate["completion_tokens"]})
    if base["tokens"] is not None and candidate["tokens"] is not None:
        index = first_difference(base["tokens"], candidate["tokens"])
        if index is not None:
            found.append({"field": "tokens", "index": index, "base": token_excerpt(base["tokens"], index),
                          "candidate": token_excerpt(candidate["tokens"], index)})
    index = first_token_byte_difference((base, candidate))
    if index is not None:
        found.append({"field": "token_bytes", "index": index, "base": base["token_bytes"][index],
                      "candidate": candidate["token_bytes"][index]})
    return found


def difference_lines(found):
    lines = []
    for item in found:
        field = item["field"]
        if field in ("text", "reasoning", "tokens", "token_bytes"):
            unit = "token" if field in ("tokens", "token_bytes") else "character"
            lines.append(f"    {field}: first difference at {unit} {item['index']}")
            lines.append(f"      base       {item['base']}")
            lines.append(f"      candidate  {item['candidate']}")
        else:
            lines.append(f"    {field.replace('_', ' ')}: base {item['base']!r}, candidate {item['candidate']!r}")
    return lines


# ------------------------------------------------------------ running requests

def all_cells(prompts, settings):
    return [(prompt["id"], temperature, seed) for prompt in prompts
            for temperature in settings["temperatures"] for seed in settings["seeds"]]


def request_body(model, messages, temperature, seed, settings, arm_extra):
    body = {"model": model, "messages": messages, "temperature": temperature, "seed": seed,
            "max_tokens": settings["max_tokens"]}
    if settings["logprobs"]:
        body["logprobs"] = True
    body.update(settings["extra"])
    body.update(arm_extra)
    return body


def run_cells(endpoint, label, model, prompts, settings, arm_extra, cells, progress):
    """Send one request per cell, in order, and return {cell: reply}."""
    messages = {prompt["id"]: prompt["messages"] for prompt in prompts}
    replies = {}
    for count, (ident, temperature, seed) in enumerate(cells, 1):
        where = (f"{label} request {count} of {len(cells)}: id={one_line(ident)} "
                 f"temperature={number(temperature)} seed={seed}")
        if progress:
            print(where, file=sys.stderr, flush=True)
        try:
            replies[(ident, temperature, seed)] = endpoint.ask(
                request_body(model, messages[ident], temperature, seed, settings, arm_extra))
        except Fail as error:
            raise Fail(f"{error} [{where}]")
    return replies


def run_controls(endpoint, model, prompts, settings, arm_extra, cells, first, progress):
    """Repeat every base cell once and judge the three controls."""
    second = run_cells(endpoint, "base repeat", model, prompts, settings, arm_extra, cells, progress)

    def repeats(sampled):
        keys = [cell for cell in cells if (cell[1] > 0) == sampled]
        bad = [cell for cell in keys if differences(first[cell], second[cell])]
        status = "not_applicable" if not keys else ("fail" if bad else "pass")
        return {"status": status, "repeats": len(keys), "mismatched": len(bad),
                "cells": [{"id": cell[0], "temperature": cell[1], "seed": cell[2]} for cell in bad]}

    sampled_temperatures = [value for value in settings["temperatures"] if value > 0]
    seeds = list(settings["seeds"])
    seen = dict(first)
    if sampled_temperatures and len(seeds) == 1:
        # One seed cannot show that seeds matter, so ask once more with another seed.
        seeds.append(seeds[0] + 1)
        extra = [(prompt["id"], value, seeds[1]) for prompt in prompts for value in sampled_temperatures]
        seen.update(run_cells(endpoint, "base seed control", model, prompts, settings, arm_extra, extra, progress))
    rows = []
    for value in sampled_temperatures:
        changed = 0
        for prompt in prompts:
            replies = [seen[(prompt["id"], value, seed)] for seed in seeds]
            texts = {(reply["text"], reply["reasoning"] or "") for reply in replies}
            labels = {tuple(reply["tokens"]) for reply in replies if reply["tokens"] is not None}
            changed += len(texts) > 1 or len(labels) > 1 or first_token_byte_difference(replies) is not None
        rows.append({"temperature": value, "prompts": len(prompts), "changed": changed})
    if not rows:
        seed_status = "not_applicable"
    else:
        seed_status = "fail" if any(row["changed"] == 0 for row in rows) else "pass"
    empty = sum(1 for cell in cells if not first[cell]["text"] and not first[cell]["reasoning"])
    return {
        "same_seed_twice": repeats(True),
        "different_seeds": {"status": seed_status, "seeds": seeds if rows else [], "by_temperature": rows},
        "greedy_twice": repeats(False),
        "replies": {"status": "fail" if empty == len(cells) else "pass", "cells": len(cells), "empty": empty},
    }


def rate(part, whole):
    return f"{100 * part / whole:.0f}%"


def print_controls(controls, title):
    say(title)
    same, seeds, greedy, replies = (controls[name] for name in
                                    ("same_seed_twice", "different_seeds", "greedy_twice", "replies"))

    def line(label, status, detail):
        word = {"pass": "PASS", "fail": "FAIL", "not_applicable": "SKIP"}[status]
        say(f"  {label:<20}{word}  {detail}")

    def repeat_detail(control, kind, cause):
        if control["status"] == "not_applicable":
            return f"no {kind} cells in this run"
        if control["status"] == "pass":
            return f"{control['repeats']} of {control['repeats']} {kind} repeats matched"
        return (f"{control['mismatched']} of {control['repeats']} {kind} repeats differed "
                f"({rate(control['mismatched'], control['repeats'])} nondeterministic); {cause}")

    line("1 same seed twice", same["status"],
         repeat_detail(same, "sampled", "the server is not deterministic for a fixed seed"))
    if seeds["status"] == "not_applicable":
        line("2 seeds differ", "not_applicable", "no sampled cells in this run")
    else:
        parts = [f"temperature {number(row['temperature'])}: {row['changed']} of {row['prompts']} prompts changed"
                 for row in seeds["by_temperature"]]
        detail = "; ".join(parts) + f" across seeds {listing(seeds['seeds'])}"
        if seeds["status"] == "fail":
            detail += "; the seed or temperature has no effect, so the sampled comparison proves nothing"
        line("2 seeds differ", seeds["status"], detail)
    line("3 greedy twice", greedy["status"],
         repeat_detail(greedy, "greedy", "the server is not deterministic at temperature 0"))
    kept = replies["cells"] - replies["empty"]
    line("- replies not empty", replies["status"], f"{kept} of {replies['cells']} base replies have content")


def not_judged_reason(controls, temperature):
    """Why a cell at this temperature cannot be judged, or None when it can."""
    if controls["replies"]["status"] == "fail":
        return "every base reply is empty, so there is nothing to compare"
    if temperature == 0:
        control = controls["greedy_twice"]
        if control["status"] == "fail":
            return (f"greedy cells not judged: the base gave different replies on "
                    f"{control['mismatched']} of {control['repeats']} greedy repeats")
        return None
    control = controls["same_seed_twice"]
    if control["status"] == "fail":
        return (f"sampled cells not judged: the base gave different replies for the same seed on "
                f"{control['mismatched']} of {control['repeats']} repeats")
    for row in controls["different_seeds"]["by_temperature"]:
        if row["temperature"] == temperature and row["changed"] == 0:
            return (f"sampled cells at temperature {number(temperature)} not judged: different seeds gave "
                    "identical replies, so the seed or temperature has no effect")
    return None


def unique(values):
    return list(dict.fromkeys(values))


def control_failures(controls, settings):
    return unique(reason for reason in
                  (not_judged_reason(controls, value) for value in settings["temperatures"]) if reason)


# ------------------------------------------------------------------ reporting

def print_header(mode, arms, model, prompts, digest, settings):
    say(f"output-parity {mode}")
    for label, shown, extra in arms:
        say(f"{label:<10} {shown}" + (f"  extra {json.dumps(extra, sort_keys=True)}" if extra else ""))
    temperatures = settings["temperatures"]
    cells = len(prompts) * len(temperatures) * len(settings["seeds"])
    greedy = len(prompts) * len(settings["seeds"]) * sum(1 for value in temperatures if value == 0)
    say(f"model {model}, {len(prompts)} prompts (sha256 {digest[:12]}), temperatures {listing(temperatures)}, "
        f"seeds {listing(settings['seeds'])}, max_tokens {settings['max_tokens']}"
        + (", logprobs on" if settings["logprobs"] else "")
        + (f", extra {json.dumps(settings['extra'], sort_keys=True)}" if settings["extra"] else ""))
    say(f"{cells} cells: {greedy} greedy, {cells - greedy} sampled")
    say()


def compare_and_report(cells, base, candidate, controls, settings):
    """Print the comparison and return (rows, summary, verdict, reason, exit code)."""
    rows = []
    for cell in cells:
        found = differences(base[cell], candidate[cell])
        reason = not_judged_reason(controls, cell[1])
        rows.append({"id": cell[0], "temperature": cell[1], "seed": cell[2],
                     "group": "greedy" if cell[1] == 0 else "sampled",
                     "judged": reason is None, "not_judged_reason": reason,
                     "identical": not found, "differences": found,
                     "base": base[cell], "candidate": candidate[cell]})
    say("Comparison of base and candidate")
    differing = [row for row in rows if row["judged"] and not row["identical"]]
    for row in differing[:MAX_DIFFS_SHOWN]:
        say(f"  DIFF id={one_line(row['id'])} temperature={number(row['temperature'])} seed={row['seed']}")
        for line in difference_lines(row["differences"]):
            say(line)
    if len(differing) > MAX_DIFFS_SHOWN:
        say(f"  and {len(differing) - MAX_DIFFS_SHOWN} more differing cells; --json lists them all")
    for group in ("greedy", "sampled"):
        members = [row for row in rows if row["group"] == group]
        judged = [row for row in members if row["judged"]]
        same = sum(1 for row in judged if row["identical"])
        if judged:
            odd = len(judged) - same
            tail = "" if not odd else f", {odd} differ" + ("s" if odd == 1 else "")
            say(f"  {group:<8} {same} of {len(judged)} cells identical{tail}")
        skipped = [row for row in members if not row["judged"]]
        if skipped:
            matched = sum(1 for row in skipped if row["identical"])
            say(f"  {group:<8} {len(skipped)} cells not judged; {matched} matched and "
                f"{len(skipped) - matched} differed, which proves nothing after a failed control")
    with_count = sum(1 for cell in cells if base[cell]["completion_tokens"] is not None
                     and candidate[cell]["completion_tokens"] is not None)
    with_tokens = sum(1 for cell in cells if base[cell]["tokens"] is not None and candidate[cell]["tokens"] is not None)
    say(f"  compared: text and finish reason on {len(cells)} cells, completion token count on {with_count}, "
        + (f"token sequence on {with_tokens}" if settings["logprobs"] else "token sequence not requested"))
    if settings["logprobs"]:
        positions = sum(1 for cell in cells for left, right in
                        zip(base[cell].get("token_bytes") or (), candidate[cell].get("token_bytes") or ())
                        if left is not None and right is not None)
        say(f"  compared raw token bytes on {positions} token positions")
    if settings["logprobs"] and with_tokens < len(cells):
        say(f"  NOTE: --logprobs was set but only {with_tokens} of {len(cells)} cells came back with token lists "
            "on both sides; the others were compared as text")

    judged_count = sum(1 for row in rows if row["judged"])
    reasons = unique(row["not_judged_reason"] for row in rows if not row["judged"])
    summary = {"cells": len(rows), "judged": judged_count, "not_judged": len(rows) - judged_count,
               "different": len(differing), "identical": judged_count - len(differing)}
    if differing:
        if reasons:
            say(f"  NOTE: {len(rows) - judged_count} cells were not judged: {'; '.join(reasons)}")
        return rows, summary, "DIFFERENT", f"{len(differing)} of {judged_count} differ", EXIT_DIFFERENT
    if reasons:
        return rows, summary, "INCONCLUSIVE", "; ".join(reasons), EXIT_INCONCLUSIVE
    return rows, summary, "IDENTICAL", f"{judged_count} of {judged_count}", EXIT_IDENTICAL


def write_json(path, document, what):
    temporary = f"{path}.tmp-{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2) + "\n")
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise Fail(f"cannot write {what} {path}: {error.strerror or error}")


def check_writable(path, option, keep=()):
    """Fail before any request is sent when an output file cannot be written or would replace an input."""
    for other_option, other in keep:
        if other and os.path.realpath(path) == os.path.realpath(other):
            raise Fail(f"{option} and {other_option} name the same file, {path}")
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        raise Fail(f"{option}: the folder {folder} does not exist")
    if os.path.isdir(path):
        raise Fail(f"{option}: {path} is a folder")
    if not os.access(folder, os.W_OK):
        raise Fail(f"{option}: the folder {folder} is not writable")


def finish(args, result, verdict, reason, code):
    result.update({"verdict": verdict, "reason": reason, "exit_code": code})
    if args.json:
        write_json(args.json, result, "result")
    say()
    say(f"PARITY: {verdict} ({reason})")
    return code


def settings_from(args):
    return {
        "seeds": args.seeds if args.seeds is not None else list(DEFAULT_SEEDS),
        "temperatures": args.temperatures if args.temperatures is not None else list(DEFAULT_TEMPERATURES),
        "max_tokens": args.max_tokens if args.max_tokens is not None else DEFAULT_MAX_TOKENS,
        "logprobs": bool(args.logprobs),
        "extra": args.extra_json or {},
    }


def result_base(mode, model, digest, prompts, settings):
    return {"tool": "output-parity", "mode": mode, "finished_at": now_utc(), "model": model,
            "prompts_sha256": digest, "prompt_ids": [prompt["id"] for prompt in prompts], "settings": settings}


# ---------------------------------------------------------------- subcommands

def run_record(args):
    prompts = load_prompts(args.prompts)
    digest = prompts_hash(prompts)
    settings = settings_from(args)
    arm_extra = args.base_extra_json or {}
    check_writable(args.out, "--out", keep=[("--prompts", args.prompts)])
    if os.path.exists(args.out) and not args.overwrite:
        raise Fail(f"{args.out} already exists; pass --overwrite to replace it")
    if args.json:
        check_writable(args.json, "--json", keep=[("--prompts", args.prompts), ("--out", args.out)])
    endpoint = Endpoint(args.url, "--url", read_api_key(args.api_key_env), args.timeout)
    print_header("record", [("base", endpoint.shown, arm_extra)], args.model, prompts, digest, settings)
    cells = all_cells(prompts, settings)
    replies = run_cells(endpoint, "base", args.model, prompts, settings, arm_extra, cells, args.progress)
    controls = run_controls(endpoint, args.model, prompts, settings, arm_extra, cells, replies, args.progress)
    print_controls(controls, "Controls on the base")
    recording = {
        "format": RECORDING_FORMAT, "version": RECORDING_VERSION, "recorded_at": now_utc(),
        "url": endpoint.shown, "model": args.model, "settings": settings, "base_extra": arm_extra,
        "prompts_sha256": digest, "prompts": prompts, "controls": controls,
        "cells": [dict({"id": cell[0], "temperature": cell[1], "seed": cell[2]}, **replies[cell]) for cell in cells],
    }
    write_json(args.out, recording, "recording")
    say()
    say(f"Recording written to {args.out}")
    result = result_base("record", args.model, digest, prompts, settings)
    result.update({"base": {"url": endpoint.shown, "extra": arm_extra}, "recording": args.out, "controls": controls})
    failures = control_failures(controls, settings)
    if failures:
        return finish(args, result, "INCONCLUSIVE", "; ".join(failures), EXIT_INCONCLUSIVE)
    return finish(args, result, "RECORDED", f"{len(cells)} cells, controls passed", EXIT_IDENTICAL)


def load_recording(path):
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except OSError as error:
        raise Fail(f"cannot read recording {path}: {error.strerror or error}")
    except ValueError:
        raise Fail(f"{path} is not a recording: it is not valid JSON")
    if not isinstance(document, dict) or document.get("format") != RECORDING_FORMAT:
        raise Fail(f"{path} is not a recording made by '{PROG} record'")
    if not is_int(document.get("version")) or document["version"] not in (1, RECORDING_VERSION):
        raise Fail(f"{path} is recording version {one_line(document.get('version'))}; "
                   f"this tool reads versions 1 and {RECORDING_VERSION}")
    damaged = Fail(f"{path} is damaged or incomplete; record it again")
    try:
        settings, controls, prompts = document["settings"], document["controls"], document["prompts"]
        well_formed = (
            isinstance(document["model"], str) and isinstance(document["base_extra"], dict)
            and isinstance(settings["extra"], dict) and isinstance(settings["logprobs"], bool)
            and is_int(settings["max_tokens"]) and settings["seeds"] and settings["temperatures"]
            and all(is_int(seed) for seed in settings["seeds"])
            and all(is_number(value) and value >= 0 for value in settings["temperatures"])
            and isinstance(prompts, list) and bool(prompts)
            and all(controls[name]["status"] in ("pass", "fail", "not_applicable")
                    for name in ("same_seed_twice", "different_seeds", "greedy_twice"))
            and controls["replies"]["status"] in ("pass", "fail")
            and all(is_int(controls[name][key]) for name in ("same_seed_twice", "greedy_twice")
                    for key in ("repeats", "mismatched"))
            and all(is_number(row["temperature"]) and is_int(row["changed"]) and is_int(row["prompts"])
                    for row in controls["different_seeds"]["by_temperature"])
            and all(is_int(seed) for seed in controls["different_seeds"]["seeds"])
            and is_int(controls["replies"]["cells"]) and is_int(controls["replies"]["empty"]))
        if not well_formed:
            raise damaged
        seen = set()
        prompts = [normal_prompt(item, f"{path} prompt {count}", seen) for count, item in enumerate(prompts, 1)]
        replies = {}
        for cell in document["cells"]:
            reply = {
                "text": cell["text"], "reasoning": cell["reasoning"], "finish_reason": cell["finish_reason"],
                "completion_tokens": cell["completion_tokens"], "tokens": cell["tokens"],
                "token_bytes": cell["token_bytes"] if document["version"] >= 2 else None}
            if not isinstance(reply["text"], str) or not isinstance(reply["reasoning"], (str, type(None))) \
                    or not isinstance(reply["tokens"], (list, type(None))):
                raise damaged
            if reply["tokens"] is not None and not all(isinstance(label, str) for label in reply["tokens"]):
                raise damaged
            if not valid_token_bytes(reply["tokens"], reply["token_bytes"]):
                raise damaged
            replies[(cell["id"], cell["temperature"], cell["seed"])] = reply
    except (KeyError, TypeError, IndexError):
        raise damaged
    if prompts_hash(prompts) != document["prompts_sha256"]:
        raise Fail(f"{path}: the stored prompts do not match the stored prompt hash; the file was edited")
    if set(all_cells(prompts, settings)) != set(replies):
        raise damaged
    document["prompts"] = prompts
    document["replies"] = replies
    return document


def check_same_request(args, recording):
    """Refuse a compare run whose requests would differ from the recorded ones."""
    settings = recording["settings"]
    why = "comparing different requests is meaningless"
    if args.model != recording["model"]:
        raise Fail(f"the recording was made with model '{recording['model']}' and this run names "
                   f"'{args.model}'; {why}")
    if args.prompts:
        digest = prompts_hash(load_prompts(args.prompts))
        if digest != recording["prompts_sha256"]:
            raise Fail(f"the prompt set in {args.prompts} (sha256 {digest[:12]}) is not the recorded one "
                       f"(sha256 {recording['prompts_sha256'][:12]}); {why}")
    checks = (
        ("--seeds", args.seeds, settings["seeds"], listing),
        ("--temperatures", args.temperatures, settings["temperatures"], listing),
        ("--max-tokens", args.max_tokens, settings["max_tokens"], str),
        ("--extra-json", args.extra_json, settings["extra"], json.dumps),
        ("--base-extra-json", args.base_extra_json, recording["base_extra"], json.dumps),
    )
    for option, given, recorded, show in checks:
        if given is not None and given != recorded:
            raise Fail(f"the recording was made with {option} {show(recorded)} and this run asks for "
                       f"{show(given)}; {why}")
    if args.logprobs and not settings["logprobs"]:
        raise Fail(f"the recording was made without --logprobs and this run asks for it; {why}")


def run_compare(args):
    recording = load_recording(args.against)
    check_same_request(args, recording)
    settings, prompts, digest = recording["settings"], recording["prompts"], recording["prompts_sha256"]
    controls, base = recording["controls"], recording["replies"]
    candidate_extra = args.candidate_extra_json or {}
    if args.json:
        check_writable(args.json, "--json", keep=[("--against", args.against), ("--prompts", args.prompts)])
    endpoint = Endpoint(args.url, "--url", read_api_key(args.api_key_env), args.timeout)
    recorded_from = f"recording {args.against}, made {one_line(recording.get('recorded_at'))} from " \
                    f"{one_line(recording.get('url'))}"
    print_header("compare", [("base", recorded_from, recording["base_extra"]),
                             ("candidate", endpoint.shown, candidate_extra)],
                 recording["model"], prompts, digest, settings)
    print_controls(controls, "Controls on the base, from the recording")
    say()
    cells = all_cells(prompts, settings)
    candidate = run_cells(endpoint, "candidate", recording["model"], prompts, settings, candidate_extra, cells,
                          args.progress)
    rows, summary, verdict, reason, code = compare_and_report(cells, base, candidate, controls, settings)
    result = result_base("compare", recording["model"], digest, prompts, settings)
    result.update({
        "base": {"recording": args.against, "url": recording.get("url"),
                 "recorded_at": recording.get("recorded_at"), "extra": recording["base_extra"]},
        "candidate": {"url": endpoint.shown, "extra": candidate_extra},
        "controls": controls, "summary": summary, "cells": rows})
    return finish(args, result, verdict, reason, code)


def run_live(args):
    prompts = load_prompts(args.prompts)
    digest = prompts_hash(prompts)
    settings = settings_from(args)
    base_extra, candidate_extra = args.base_extra_json or {}, args.candidate_extra_json or {}
    if args.json:
        check_writable(args.json, "--json", keep=[("--prompts", args.prompts)])
    key = read_api_key(args.api_key_env)
    base_end = Endpoint(args.base_url, "--base-url", key, args.timeout)
    candidate_end = Endpoint(args.candidate_url, "--candidate-url", key, args.timeout)
    print_header("live", [("base", base_end.shown, base_extra), ("candidate", candidate_end.shown, candidate_extra)],
                 args.model, prompts, digest, settings)
    if base_end.url == candidate_end.url and base_extra == candidate_extra:
        say("NOTE: both arms are the same URL with the same settings, so this compares the server with itself")
        say()
    cells = all_cells(prompts, settings)
    base = run_cells(base_end, "base", args.model, prompts, settings, base_extra, cells, args.progress)
    controls = run_controls(base_end, args.model, prompts, settings, base_extra, cells, base, args.progress)
    print_controls(controls, "Controls on the base")
    say()
    candidate = run_cells(candidate_end, "candidate", args.model, prompts, settings, candidate_extra, cells,
                          args.progress)
    rows, summary, verdict, reason, code = compare_and_report(cells, base, candidate, controls, settings)
    result = result_base("live", args.model, digest, prompts, settings)
    result.update({"base": {"url": base_end.shown, "extra": base_extra},
                   "candidate": {"url": candidate_end.shown, "extra": candidate_extra},
                   "controls": controls, "summary": summary, "cells": rows})
    return finish(args, result, verdict, reason, code)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except Fail as error:
        print(f"{PROG}: error: {redact(one_line(error, 600))}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # The reader of standard output went away, as with "| head". No verdict was delivered.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
