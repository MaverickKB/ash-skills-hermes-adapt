#!/usr/bin/env python3
"""Run the eval cases against any OpenAI-compatible chat endpoint, with and without a skill.

Use this to find out whether a skill changes what a given model does. It works
with any server that speaks the chat completions API, including a local
inference engine.

Each case under ./evals/<skill>/<case>/ has a prompt.md and a graders/ folder. Every case
is sent twice: once bare, and once with the skill as the system prompt. For
<skill> the system prompt is skills/<skill>/assets/always-on.md when that file
exists, otherwise the body of skills/<skill>/SKILL.md. Use --system-file to
override it.

Grading:
  regex graders   scored here
  llm graders     scored by a judge model if you pass --judge-model, otherwise
                  left ungraded with the reply saved so you can grade it
  tool graders    skipped, since a bare chat endpoint has no tools
  arm: with-only  a grader marked this way checks the skill's own format. It is
                  scored only with the skill loaded, shown as "format", and
                  left out of both totals so the comparison stays fair

  run_evals.py --base-url http://localhost:8080/v1 --model my-model
  run_evals.py --base-url URL --model M --judge-model J --cases 'my-skill/*'

The prompts are sent to the endpoint you name. The API key is read from the
environment variable named by --api-key-env and is never printed.
"""
import argparse
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request

JUDGE_SYSTEM = (
    "You grade one reply against one criterion. Read the criterion and the reply. "
    "Answer with the single word PASS or FAIL on the first line, then one sentence giving the reason."
)


def strip_frontmatter(text):
    fields = {}
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            for line in text[4:end].splitlines():
                key, sep, value = line.partition(":")
                if sep and not line.startswith((" ", "\t")):
                    fields[key.strip()] = value.strip().strip("'\"")
            return fields, text[end + 5:].strip()
    return fields, text.strip()


def default_system(root, skill):
    always_on = os.path.join(root, "skills", skill, "assets", "always-on.md")
    if os.path.isfile(always_on):
        with open(always_on, encoding="utf-8") as handle:
            text = handle.read()
        marker = "\n---\n"
        return text.split(marker, 1)[1].strip() if marker in text else text.strip()
    skill_md = os.path.join(root, "skills", skill, "SKILL.md")
    if os.path.isfile(skill_md):
        with open(skill_md, encoding="utf-8") as handle:
            return strip_frontmatter(handle.read())[1]
    return None


def load_cases(evals_dir, pattern):
    cases = []
    for skill in sorted(os.listdir(evals_dir)):
        skill_dir = os.path.join(evals_dir, skill)
        if not os.path.isdir(skill_dir) or skill == "results":
            continue
        for name in sorted(os.listdir(skill_dir)):
            prompt_path = os.path.join(skill_dir, name, "prompt.md")
            if not os.path.isfile(prompt_path) or not fnmatch.fnmatch(f"{skill}/{name}", pattern):
                continue
            with open(prompt_path, encoding="utf-8") as handle:
                prompt = strip_frontmatter(handle.read())[1]
            graders = []
            grader_dir = os.path.join(skill_dir, name, "graders")
            for filename in sorted(os.listdir(grader_dir)) if os.path.isdir(grader_dir) else []:
                with open(os.path.join(grader_dir, filename), encoding="utf-8") as handle:
                    fields, body = strip_frontmatter(handle.read())
                graders.append({"name": filename[:-3], "fields": fields, "criteria": body})
            cases.append({"skill": skill, "name": name, "prompt": prompt, "graders": graders})
    return cases


def chat(base_url, model, messages, api_key, timeout, temperature):
    body = json.dumps({"model": model, "messages": messages, "temperature": temperature}).encode("utf-8")
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"] or ""


def grade_regex(fields, reply):
    flags = re.S
    if "i" in fields.get("flags", ""):
        flags |= re.I
    found = re.search(fields["pattern"], reply, flags) is not None
    return found if fields.get("match", "contains") == "contains" else not found


def grade_llm(criteria, reply, judge, votes):
    passes = 0
    for _ in range(votes):
        verdict = chat(judge["base_url"], judge["model"], [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"Criterion:\n{criteria}\n\nReply to grade:\n{reply}"},
        ], judge["api_key"], judge["timeout"], 0)
        first = verdict.strip().split()[0].strip("*:.,").upper() if verdict.strip() else ""
        passes += first == "PASS"
    return passes * 2 > votes


def with_only(grader):
    """A grader that checks the skill's own format, which a bare model cannot know."""
    return grader["fields"].get("arm") == "with-only"


def grade(case, reply, judge, votes, arm="with"):
    results = {}
    for grader in case["graders"]:
        kind = grader["fields"].get("type", "llm")
        if arm == "without" and with_only(grader):
            continue
        if kind == "regex":
            results[grader["name"]] = grade_regex(grader["fields"], reply)
        elif kind == "llm":
            results[grader["name"]] = grade_llm(grader["criteria"], reply, judge, votes) if judge else None
    return results


def score(results, skip=()):
    """Passed and graded counts, leaving out the with-only graders named in skip."""
    graded = [value for name, value in results.items() if value is not None and name not in skip]
    return (sum(graded), len(graded))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="for example http://localhost:8080/v1")
    parser.add_argument("--model", required=True, help="model under test")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="environment variable holding the key, if one is needed")
    parser.add_argument("--cases", default="*", help="glob over <skill>/<case>, for example 'mythos-mode/*'")
    parser.add_argument("--system-file", help="system prompt for the with-skill arm, overriding the default")
    parser.add_argument("--runs", type=int, default=1, help="runs per case and arm")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per request")
    parser.add_argument("--judge-model", help="model that scores llm graders")
    parser.add_argument("--judge-base-url", help="endpoint for the judge (default: --base-url)")
    parser.add_argument("--judge-api-key-env", help="environment variable for the judge key (default: --api-key-env)")
    parser.add_argument("--votes", type=int, default=3, help="judge votes per llm grader")
    parser.add_argument("--evals-dir", default="evals", help="default: ./evals, with skills in ./skills beside it")
    parser.add_argument("--out", help="write replies and grades to this JSON file")
    args = parser.parse_args(argv)

    if args.runs < 1 or args.votes < 1:
        print("error: --runs and --votes must be at least 1", file=sys.stderr)
        return 2
    if not os.path.isdir(args.evals_dir):
        print(f"error: {args.evals_dir} is not a directory", file=sys.stderr)
        return 2
    cases = load_cases(args.evals_dir, args.cases)
    if not cases:
        print(f"error: no cases match '{args.cases}'", file=sys.stderr)
        return 2
    override = None
    if args.system_file:
        try:
            with open(args.system_file, encoding="utf-8") as handle:
                override = handle.read().strip()
        except OSError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
    api_key = os.environ.get(args.api_key_env, "")
    judge = None
    if args.judge_model:
        judge = {"base_url": args.judge_base_url or args.base_url, "model": args.judge_model,
                 "api_key": os.environ.get(args.judge_api_key_env or args.api_key_env, ""), "timeout": args.timeout}

    rows, totals = [], {"with": [0, 0], "without": [0, 0]}
    try:
        for case in cases:
            system = override or default_system(os.path.dirname(os.path.abspath(args.evals_dir)), case["skill"])
            if not system:
                print(f"error: no system prompt found for skill '{case['skill']}'. Pass --system-file.", file=sys.stderr)
                return 2
            row = {"case": f"{case['skill']}/{case['name']}", "arms": {}}
            format_only = {grader["name"] for grader in case["graders"] if with_only(grader)}
            for arm in ("without", "with"):
                messages = ([{"role": "system", "content": system}] if arm == "with" else []) + [
                    {"role": "user", "content": case["prompt"]}]
                runs = []
                for _ in range(args.runs):
                    reply = chat(args.base_url, args.model, messages, api_key, args.timeout, args.temperature)
                    results = grade(case, reply, judge, args.votes, arm)
                    runs.append({"reply": reply, "graders": results})
                    passed, graded = score(results, format_only)
                    totals[arm][0] += passed
                    totals[arm][1] += graded
                row["arms"][arm] = runs
            rows.append(row)
            parts = []
            for arm in ("without", "with"):
                passed = sum(score(run["graders"], format_only)[0] for run in row["arms"][arm])
                graded = sum(score(run["graders"], format_only)[1] for run in row["arms"][arm])
                parts.append(f"{arm} {passed}/{graded}")
            if format_only:
                shown = [run["graders"].get(name) for run in row["arms"]["with"] for name in sorted(format_only)]
                shown = [value for value in shown if value is not None]
                if shown:
                    parts.append(f"format {sum(shown)}/{len(shown)}")
            print(f"{row['case']:<44} {'   '.join(parts)}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        print(f"error: request failed: {error}", file=sys.stderr)
        return 2
    except (KeyError, IndexError, json.JSONDecodeError) as error:
        print(f"error: the endpoint did not return a chat completion ({error!r})", file=sys.stderr)
        return 2

    print(f"\ntotal: without {totals['without'][0]}/{totals['without'][1]}   with {totals['with'][0]}/{totals['with'][1]}")
    if not judge:
        print("llm graders were not scored. Pass --judge-model, or read the replies in the --out file and grade them.")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump({"model": args.model, "runs": args.runs, "judge": args.judge_model, "cases": rows}, handle, indent=2)
        print(f"replies and grades written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
