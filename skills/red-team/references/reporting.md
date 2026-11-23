# Reporting

This guide covers step 8 of the method in `SKILL.md`. A report has to let the owner fix the problem and let someone else check the fix. Write for two readers: a decision maker who reads one page, and an engineer who reads one finding.

## Contents

- Before you write
- Finding template
- Executive summary shape
- Rest of the report
- Before the report is sent

## Before you write

1. Verify the log and the evidence files:

```sh
python3 "$SKILL_DIR/scripts/engagement_log.py" verify --log engagement_log.jsonl --check-files
```

2. Give the operator the `head` hash that `verify` prints. Put it in the report.
3. Read every warning that `verify` prints. An action logged as not in scope, a missing approval, or a change to the scope file has to be explained in the report.
4. Go through the state file. Every finding needs a status. Rejected hypotheses stay in the record, with the reason.
5. Redact. Search the report and the evidence for passwords, keys, tokens and personal data.

## Finding template

Use one block for each finding. Keep the order of the fields.

```text
Title
  A short statement of the flaw and where it is.

Identifier
  F-001

Affected target
  The name, address, URL, repository, file hash or device identifier,
  as written in the scope file. Add the component and its version.

Severity
  Critical, high, medium, low or informational.
  Reasoning: what an attacker needs, what they gain, and why that
  matters on this system. Give the CVSS score and vector if you used
  one, and say how you adjusted it for this environment.

Confidence
  Low, medium, high or confirmed, with the reason.

Reproduction steps
  1. Starting conditions: the tester's position, the account and role.
  2. Each step, with the exact request or command. Redact secrets.
  3. What to look for in the response.
  Stop at the minimal proof. Do not include a working attack tool.

Evidence
  The file name and SHA-256 of each evidence file, and the sequence
  number of its log entry. Quote the smallest extract that shows the
  flaw.

Impact
  What could be read, changed or stopped. Which data and which users.
  Describe what was shown and what is inferred, and keep the two apart.

Fix
  The specific change: the version to upgrade to, the setting to
  change, or the code to correct. Add a short-term measure if the fix
  will take time.

How to retest
  The check that shows the fix worked: the same step, and the result
  that a fixed system gives.

References
  The advisory identifiers and vendor notes that the finding rests on.
```

Notes on the fields:

- Severity needs reasoning. A label without a reason cannot be checked.
- Keep fact and inference apart. "The server returned the test account's own record" is a fact. "Other users' records are probably exposed" is an inference. Say which is which.
- Reproduction steps must work for someone who was not there. Test them from a clean start if you can do that without new risk.
- For a flaw that looks new and unpublished, mark the finding as unpublished. Keep the steps at the minimum. Disclosure goes through the operator.

## Executive summary shape

Keep it to one page. Use plain language and no tool names.

1. What was tested: the engagement name, who authorized it, the window, and the scope in one or two sentences.
2. The overall result, in two or three sentences. Say how exposed the system is, and why.
3. The findings that matter most. Give three to five, each in one line with its severity and what it means for the organization.
4. A count of findings by severity.
5. What to do first: the few actions that reduce the most risk.
6. Limits of the test: what was out of scope, what could not be tested and why, and any stop that happened.

## Rest of the report

- Scope and method: the scope file by name and hash, the technique classes used, and the dates.
- Findings: one block each, ordered by severity.
- What was tested and found sound. This is useful to the owner as well.
- Open questions and items left for the operator, such as names that looked related but were not in scope.
- Record of the work: the log file name, the number of entries, the head hash, and a list of approvals and stops.
- Evidence handling: where the evidence is kept and the retention rule from the scope file.

## Before the report is sent

- The operator reads it first. The operator decides who else receives it.
- Send it only through the channel named in the scope file or by the operator.
- Check once more that no secret and no personal data is in the report or its attachments.
- Do not delete evidence after the report goes out. The operator disposes of it under the retention rule.
