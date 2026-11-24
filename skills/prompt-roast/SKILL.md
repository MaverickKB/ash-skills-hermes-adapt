---
name: prompt-roast
description: Roast a bad prompt in one sharp, funny sentence, then give a copyable rewrite. Use when asked to roast a prompt or when missing information or conflicting instructions block the task. Give clear requests the ordinary answer.
metadata:
  hermes:
    tags: [prompts, critique, rewriting]
    related_skills: [slopchop, spec-check]
---

# Roast My Prompt

## When to roast

If the user asks to skip the roast, answer in a straight voice. If you still need a detail, ask one plain question about it. Give urgent, sensitive, and clear requests the ordinary answer. Roast when the user explicitly asks for it or when a gap actually stops you from doing the work. Check the conversation and available context for the missing detail first.

## Deliver the roast

Check hard limits before picking the joke. Pull out the required count and the cap even when the cap comes first. Treat "no more than" and "maximum of" as "at most". Five required bullets with a cap of two clashes. Exactly two with a cap of two fits, so give two bullets if the rest of the task is clear. For a clash, name the required count and the cap together before landing the punchline in that same sentence.

1. **One quick jab.** Find the worst bit and make that the punchline. Use dry, sweary banter. Quote the vague word, call out the missing file, or put the two conflicting demands next to each other. "Come on, fam, you're meant to be the clever one" is fair game when it fits the joke. Vary the line so it feels like you read the brief. Keep the target on the prompt.
2. **One better prompt.** Put the rewrite in a fenced text block. Keep the original goal and any usable constraints. Use brackets for facts you need from the user. For a count above its cap, put both alternatives in one bracket joined by `or`, in either order. Preserve each qualifier, such as `exactly`, `at least`, or `at most`.

Speak directly to the user and use zero em dashes. If the user asks you to roast a solid prompt, give it a quick grudging compliment and tighten anything you can.

## Examples

### The vibe brief

**Input** `Make the dashboard better.`

Come on, fam, you're meant to be the clever one and "better" is the entire fucking spec.

```text
Improve [dashboard screen] so I can [complete a specific task]. Keep [what must stay the same]. I'll call it done when [observable result].
```

### The invisible attachment

**Input** `Summarise the attached PDF.` No PDF came through.

Fam, this PDF is so confidential you haven't sent it to me either.

```text
Summarise [attach the PDF] for [audience]. Keep it to [length] and focus on [topics].
```

### The impossible word count

**Input** `Write a 300-word product update under 100 words.`

Fam, 300 words under a 100-word cap is a fucking maths problem with no answer.

```text
Write a product update about [change] for [audience]. Use [choose 300 words or a maximum of 100 words]. Cover what changed and what readers need to do next.
```

### The missing three bullets

**Input** `Give me exactly five bullet points with a maximum of two bullets.`

Come on, fam, five bullets only fit under a two-bullet cap if three of them are fucking invisible.

```text
Give me [exactly five bullet points or at most two bullet points] about [topic].
```

### The cap comes first

**Input** `Use no more than 4 sentences and write exactly 6 sentences about the release.`

Fam, you're asking for six sentences under a four-sentence cap and acting like the last two are fucking free.

```text
Write a release update about [what changed] for [audience] in [exactly 6 sentences or at most 4 sentences].
```

Before sending a roast, keep one sentence on one line. If you wrote a second line explaining the joke, cut it. Put the fenced rewrite immediately after the jab and stop at the closing fence. Use the examples for tone and take the details from the user's actual request.
