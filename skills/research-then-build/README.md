# Research then build

If you're betting a new app on a package, make it pass your tests in scratch before it gets anywhere near your project.

[Agent instructions](SKILL.md) · [Worked Node.js example](references/node-example.md) · [All skills](../../README.md#skill-directory)

## When to use it

You're picking a stack, adding a major integration, or making an upgrade where the answer depends on what works today.

## Install

```bash
hermes skills install MaverickKB/skills/skills/research-then-build
```

## Try it

```text
Use research-then-build. I need a Node.js JSON API that accepts a string and rejects numeric input. Check the live Node and package docs, then read the code and tests in a widely used upstream repo. Run two pinned candidates against the same request tests on the deployment runtime. Show me the links, versions, commands, and results. Start building once a candidate passes.
```

The [Node.js example](references/node-example.md) shows how a framework passed four request cases and silently converted a number to a string on the fifth.

[Fair bench](../fair-bench/README.md) helps when the decision comes down to a speed claim.
