---
name: slopchop
description: Cuts machine-sounding writing out of prose and leaves text that says something specific in a plain voice. Finds the patterns that mark text as generated, such as filler, stock vocabulary, stock sentence shapes, chatbot openers and closers, and formatting habits, and rewrites them. Ships a scanner for the patterns a program can find. Use on any prose written or edited for a human reader, including replies, documentation, READMEs, commit messages, pull request descriptions, posts and emails, and whenever a draft reads as generic or machine-made.
metadata:
  version: "1.0.0"
  hermes:
    tags: [writing, editing, voice, style]
    related_skills: [handover, spec-check]
---

# Slopchop

Slop is text that takes the reader's time and gives nothing back. Readers spot it within a line, and once they have spotted it they skim everything after it. The cure has two parts: cut what says nothing, then say the rest in specific, plain words.

## What to leave alone

Never change these, whatever they contain.

- code, commands, file paths, identifiers, error messages and log output
- quoted speech and quoted sources
- legal, contractual and regulatory wording
- a voice the person asked for on purpose

## Voice and punctuation

- Remove every em dash (`U+2014`) from editable prose, including headings, lists, captions, and link text. End the sentence, use a comma where it fits, or recast the sentence. Apply the same rule to HTML entities that render as an em dash.
- Use first person when writing on someone's behalf. Use `I` for the writer's own actions and `we` for work the writer genuinely shares with others. Address the reader as `you` or start with the action.
- Remove third-person narration about the writer or reader. Rewrite phrases such as `the author` and `the user` from the speaker's point of view. In technical instructions, give the action directly or name the component doing the work.

## The pass

1. **Scan.** Run the scanner for the patterns a program can find. It skips code, links and quotes.

   ```bash
   python3 scripts/scan.py DRAFT.md
   ```

2. **Cut.** Delete every sentence that fails the swap test below.
3. **Rewrite.** Fix what the scanner flagged and what the lists below describe.
4. **Reread as the reader.** Does the first sentence carry the point? Could a stranger act on this?
5. **Scan again.** Rewrites add new slop.

The scanner finds surface patterns. It cannot tell whether a sentence says anything, so steps 2 and 4 are the real work.

## The swap test

Take a sentence and ask whether it could appear unchanged in a different document about a different subject. If it could, it says nothing about this one. Cut it, or replace it with the fact it was standing in for.

- "This is a powerful and flexible tool." Could describe anything. Cut.
- "It reads a 40 GB log in 90 seconds on a laptop." Could only describe this. Keep.

## Five kinds of slop

### 1. Sentences that say nothing

- **Inflation.** A plain event dressed as a milestone, a turning point or a tribute to something. Say what happened.
- **Unnamed authorities.** `Experts say`, `studies show`, `many believe`. Name the source or drop the claim.
- **The closing bow.** A last line about the future being bright or the journey continuing. End on the last fact.
- **The trailing gloss.** A clause tacked on with `-ing` that explains what the reader should feel: `highlighting its importance`, `ensuring a smooth experience`. Delete it, or state the consequence as its own fact.
- **Padding phrases.** `In order to` is `to`. `Due to the fact that` is `because`. `It is worth noting that` is nothing at all.

### 2. Stock words

Some words appear far more in generated text than in anything a person writes by hand. Each has a plainer one.

| Stock | Plain |
|---|---|
| `utilize`, `leverage` | use |
| `facilitate` | help, let |
| `robust`, `comprehensive`, `seamless` | say what it handles, covers, or connects to |
| `crucial`, `pivotal`, `vital` | say what breaks without it |
| `delve into`, `dive into` | look at, read |
| `showcase`, `highlight`, `underscore` | show |
| `enhance`, `elevate`, `streamline` | say what got better, and by how much |
| `serves as`, `stands as`, `acts as` | is |
| `a testament to`, `a tapestry of`, `the landscape of` | cut |
| `additionally`, `furthermore`, `moreover` | and, also, or a new sentence |

The same goes for metaphors borrowed to sound technical, such as a `north star`, a `flywheel`, or `bedrock`. Use the real name of the thing. A literal technical term stays. A vector of floats is a vector, and a test harness is a harness.

### 3. Stock shapes

- **`Not just X, but Y`.** Say Y.
- **Threes by reflex.** Three adjectives, three benefits, three examples, because three sounds finished. Use the number you have.
- **The false range.** `From startups to enterprises`, when the two ends are not on any scale. List what you mean.
- **Synonym rotation.** Calling one thing the tool, the utility, the solution and the platform in a paragraph. Pick one name and repeat it.
- **Hedge stacks.** `Could potentially perhaps`. One hedge, placed on the claim that is in doubt.
- **The colon reveal.** A setup, a colon, then the point. Write the point as a sentence.

### 4. The chatbot voice

- openers that praise or agree: `Great question`, `Certainly`, `You're absolutely right`
- announcements: `Let me explain`, `Here's a breakdown`
- closers: `I hope this helps`, `Let me know if you have questions`
- disclaimers about what the writer cannot know, placed before the answer
- excitement about ordinary results

Delete all of them. Start with the answer and stop when it is given.

### 5. The look

- For a pause, end the sentence or use a comma where grammar allows. Keep hyphens that belong to a word or identifier.
- Bold on many phrases. Bold the one thing the reader must not miss.
- List items that open with a bold label and a colon, then restate the label. Write a sentence.
- Headings In Title Case. Use sentence case.
- Emoji as decoration in headings and lists.
- Curly quotes in text that will be pasted into code or a terminal.

## Then make it specific

Cutting is half of it. What remains has to carry something.

- **Name the actor.** "The loader parses the file", where the draft said "the file is parsed".
- **Give the number.** "Fell from 800 ms to 120 ms", where the draft said "much faster".
- **Use the stronger verb.** An adverb holding up a weak verb means the verb is wrong.
- **One idea per sentence.** If the reader has to go back, split it.
- **Say what it does.** "Types that follow your schema" names a feeling. "A column rename fails the build" names what happens.

## Match the genre

| Writing | Voice |
|---|---|
| Documentation, reference, reports, commit messages, changelogs | Plain and neutral. No personality added. The facts carry it |
| Replies to a person | Answer first. Address the reader directly |
| Emails, updates and posts on someone's behalf | Use the writer's own point of view |
| Essays, posts, opinion | A real point of view. First person where it fits. Sentences of different lengths. A reaction, where the draft had a list of pros and cons |

## Before and after

A sentence from documentation.

> **Before:** Our robust caching layer seamlessly enhances performance, ensuring a smooth experience for users.
>
> **After:** The cache answers repeat queries in 2 ms. A cold query takes 40 ms.

A reply to a question.

> **Before:** Great question! There are several important considerations when choosing between these two approaches. Let me break them down for you.
>
> **After:** Use the queue. You already run Redis, and at 50 jobs a second either option handles the load.

A paragraph of opinion.

> **Before:** Remote work is not just a trend, but a fundamental shift in how we think about productivity, collaboration, and work-life balance.
>
> **After:** I get more done at home and I miss the hallway. Both are true, and most writing about remote work admits only one.

An update written on someone's behalf.

> **Before:** The developer fixed the export. Users can now download the report.
>
> **After:** I fixed the export. You can download the report now.

## Last check

1. Does the first sentence carry the point?
2. Does every sentence pass the swap test?
3. Is every claim a fact, a number, or a named source?
4. Did the scanner come back clean, apart from words used in their literal sense?
5. Does the final prose contain zero em dashes, including ones rendered from HTML entities?
6. Does writing on someone's behalf speak in their voice and address the reader directly?
7. Did you leave code, quotes and commands exactly as they were?
