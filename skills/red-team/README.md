# Red team

> **Use this skill only on hardware you control and are authorised to test.** You are responsible for how you use it and for the consequences of your testing.

Validates written security-review scope and records approvals and evidence in a hash-chained log.

## When to use it

You need to document what a defensive security review covers and make its evidence auditable.

Check the written scope against the approved inventory and verify the engagement log before reporting findings.

## What's included

The [scope checker](scripts/scope_check.py) reads the scope file, and the [engagement log](scripts/engagement_log.py) records decisions and verifies evidence hashes.
