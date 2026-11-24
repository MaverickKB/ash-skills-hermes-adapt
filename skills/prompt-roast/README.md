# Roast My Prompt

Give me a brief I can use and I'll get to work. Give me "make it better" and you're getting a roast.

[Agent instructions](SKILL.md) · [All skills](../../README.md#skill-directory)

## When to use it

Install this if you want your agent to call you out when your brief leaves the goal, target, or finish line open. You can also ask for a roast on purpose.

## Install

```bash
hermes skills install MaverickKB/skills/skills/prompt-roast
```

## Try it

```text
Roast this prompt: make the dashboard better.
```

> Come on, fam, you're meant to be the clever one and "better" is the entire fucking spec.

```text
Improve [dashboard screen] so I can [complete a specific task]. Keep [what must stay the same]. I'll call it done when [observable result].
```

Fill the brackets and send the rewrite. You get one jab, then the better prompt.

## Try a contradiction

```text
Roast my prompt: Give me exactly five bullet points with a maximum of two bullets.
```

> Come on, fam, five bullets only fit under a two-bullet cap if three of them are fucking invisible.

```text
Give me [exactly five bullet points or at most two bullet points] about [topic].
```

The order of the limits can change.

```text
Roast the prompt: Use no more than 4 sentences and write exactly 6 sentences about the release.
```

> Fam, you're asking for six sentences under a four-sentence cap and acting like the last two are fucking free.

```text
Write a release update about [what changed] for [audience] in [exactly 6 sentences or at most 4 sentences].
```

## Pair it with

[Spec check](../spec-check/README.md) · [Slopchop](../slopchop/README.md)
