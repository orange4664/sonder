---
name: hardworking-paper-writer
description: Revise an academic paper paragraph by paragraph with the author, stripping AI writing tells (stop-slop) while preserving the author's own voice. Use this whenever the user wants to polish, tighten, edit, proofread, or "de-slop" a paper, manuscript, draft, abstract, or thesis chapter — especially when they want careful interactive revision rather than a one-shot rewrite. Trigger on requests like "help me revise my paper", "clean up my draft", "make my writing sound less AI", "go through my abstract line by line", or when they hand over a .tex/.md/.txt paper to improve.
argument-hint: <path-to-paper (.tex / .md / .txt)>
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - AskUserQuestion
---

# Hardworking Paper Writer

Revise a paper one paragraph at a time, with the author in the driver's seat.

The job has two halves that pull against each other. Stop-slop removes the patterns that make prose read as machine-written. But applied bluntly, those same rules sand every writer down to the same flat surface — and a paper's voice is part of what makes it worth reading. So the real task is narrower: cut the slop, keep the human. That is why this skill is slow and interactive instead of a one-shot rewrite. You are not imposing a house style; you are helping *this* author say what *they* mean, more sharply, in their own register.

Two commitments follow from that:

- **The author decides every sentence.** You propose; they dispose. Never edit prose without an explicit choice from them. And the author decides the *shape* too — the paragraph structure is theirs to set, not yours to impose.
- **You get better as you go — within limits.** Every choice the author makes, and every option they reject, tells you something about their taste. Fold it back in so later suggestions need less correction than early ones. But *not all feedback is the same kind of signal*, and the hard part is telling them apart. See [Learning the author's preferences](references/preference-learning.md): feedback arrives at one of three scopes, and only the right scope becomes a lasting rule. The failure mode this exists to stop is over-generalization — one "don't write it like that" morphing into a ban that silently applies to every later sentence, even ones where the author would happily accept it.

The flow has three distinct beats: **read it all first, then agree on what each paragraph is for, then work one paragraph at a time.** That order matters. You cannot judge a paragraph's main thread before you've read the whole paper — the point of a paragraph is often only visible from the paragraphs around it.

## The stop-slop principles

These are the patterns you are hunting for. Apply them as judgment, not as a checklist to satisfy — a sentence can break a "rule" and still be the best version.

1. **Cut filler.** Remove throat-clearing openers ("It is important to note that"), emphasis crutches ("very", "really", "significantly"), and adverbs that do no work.
2. **Break formulaic structures.** Avoid binary contrasts ("not X, but Y"), negative listings, dramatic one-word fragments, rhetorical question setups, and false agency.
3. **Prefer active voice.** Find the actor and make them the subject. "We measured" beats "measurements were taken". (Academic convention sometimes wants passive — respect the author's field norms.)
4. **Be specific.** Replace vague declaratives ("the results are significant") with the actual claim. Watch lazy extremes ("always", "never", "every") standing in for a real bound.
5. **Put the reader in the room.** Concrete beats abstract. Name the mechanism, the number, the object.
6. **Vary rhythm.** Mix sentence lengths. Two items often beat three. Avoid em-dashes if the author doesn't already use them.
7. **Trust the reader.** State findings directly. Drop the softening and the over-explanation.
8. **Cut quotables.** If a sentence sounds engineered to be tweeted, rewrite it plainly.
9. **Write for the reader, not the insider.** Unnecessary jargon is a tell of its own kind — it signals an author writing to prove membership rather than to be understood. Translate any term that has a plain equivalent, expand each acronym on first use, and unpack notation a reader can't be assumed to carry in their head. When jargon is genuinely load-bearing — a field term with no substitute — keep it, but make the sentence around it do the work of carrying someone from one subfield over. Density the argument needs, keep; density that only flatters the writer, cut.

Deeper reference phrasing lives in the stop-slop plugin if you need examples or the full phrase lists: `plugins/stop-slop/skills/stop-slop/references/` (`phrases.md`, `structures.md`, `examples.md`). Read them only when a specific case is unclear. (This skill is self-contained and ships its own rules; if the plugin isn't present, the principles above are sufficient.)

For papers, add three domain constraints on top: **never alter meaning** (a tighter sentence that changes a claim is a failure), **leave math, citations, labels, and defined terms untouched**, and **match field register** — a Nature letter and a math proof slop differently.

## Two things the sentence loop can miss

Fixing one line at a time is necessary but not sufficient. Two defects only surface when you lift your eyes from the single sentence — keep both in view throughout.

**Read as the reader, not the author.** The author knows what they mean; the reader has only the words on the page. Every so often, drop the editor's hat and read a passage cold — as someone who knows the field but has never seen this paper. Where would they stall? An undefined term, a pronoun with no clear antecedent, a claim that quietly assumes three steps the author skipped, an acronym introduced fifty lines ago — and, most often, a concept that gets *used* well before it gets *defined*. Forward references like that are nearly invisible to the author, who has the whole paper in their head, and nearly fatal to the reader, who is meeting the term for the first time with no anchor. Track where each key term, symbol, and acronym is first introduced versus where it's first used; when use precedes definition, flag it. Each of those sentences is locally fine, which is exactly why a sentence-by-sentence pass walks right past them. This reader's-eye reading is the lens behind principle 9, and it is the test for whether a rewrite actually helped: not "is this cleaner?" but "does this land for someone who isn't already inside the author's head?"

**Mind the paragraph, not just the sentence.** A paragraph has a shape, and in most academic prose that shape is general-to-specific: the opening sentence states the paragraph's claim, and everything after it earns that claim. Check that each paragraph's opener actually announces what the paragraph delivers; a buried or missing topic sentence is one of the most common structural defects, and no sentence-level edit will fix it. Then check that paragraphs connect: each should pick up where the last left off (given-new flow), so the reader never wonders why this paragraph follows that one. Watch both failure modes — a missing transition that drops the reader, and its opposite, a mechanical connective ("Furthermore", "Moreover", "Additionally") propped in place of a real logical link (that one is also slop). These are supra-sentence problems; you handle them at paragraph boundaries — and, in this skill, *before* you ever touch a sentence, in the blueprint.

## Phase 0 — Set up the workspace

1. **Locate the paper** from `$ARGUMENTS`. If none is given, ask for the path. Accept `.tex`, `.md`, `.txt`. For a folder, prefer `.tex`, then `.md`. If only a PDF exists, tell the author this skill needs a text source and point them at `paper-review-helper` to convert it first.

2. **Check for an existing revision** before creating anything. If `<paper-stem>-revision/` already exists with a `revision-log.md`, this is a resume — skip to "Resuming" below.

3. **Build the workspace** as a sibling of the paper:

```text
<paper-stem>-revision/
├── original/            # untouched backup — never edited
│   └── <paper filename>
├── working/             # the live copy you edit in place
│   └── <paper filename>
├── blueprint.md         # the agreed shape from Phase 2
├── revision-log.md      # every sentence: id, status, original, chosen, fixes
└── style-profile.md     # model of the author's voice for THIS paper
```

Copy the source into both `original/` and `working/`. From here on, every Edit targets `working/` only. The `original/` copy is the safety net and the basis for the final diff.

4. **Load the cross-paper memory.** Before seeding a fresh profile, read `memory/style-profile.md` (inside this skill's install directory) if it exists. It holds preferences the author has confirmed as lasting beyond one paper. Seed `style-profile.md` from it. This is the only cross-paper carryover — everything else in the workspace profile belongs to this paper and dies with it.

5. **Seed the tracking files** from the templates in [references/templates.md](references/templates.md). Tell the author the workspace is ready and where it is.

## Phase 1 — Read it all, then profile the voice

Do not start editing, and do not start the blueprint, before a full read. You cannot judge a paragraph's main thread without its surrounding argument, and you need the author's baseline voice before you change a word. Read `working/` end to end first.

While reading, fill in `style-profile.md` from the **existing prose** — but treat the result as a **hypothesis, not a rule set**. These are guesses about the author's voice from their own writing; real preferences only become confirmed when the author says so or when repeated choices across many sentences show a pattern. An entry the author never actually confirmed is weak evidence and must not block an otherwise good rewrite. Mark each entry with its **scope** so the difference is visible:

- **global** — a preference the author confirms for all their papers (e.g. "always use 'we', never passive"). Written to the cross-paper memory; stays.
- **local** — a preference that holds for *this* paper only (e.g. "this venue wants the passive"). Lives in the workspace profile; dies with the paper.
- **sentence** — a one-off for this exact instance. Does not enter the profile at all.

Follow [references/preference-learning.md](references/preference-learning.md) for how each scope is set, confirmed, and pruned.

Also record structure **notes**, as raw material for the blueprint in Phase 2. Do not act on them yet — just note, in `revision-log.md` under a **Structure notes** heading: each paragraph's topic sentence (or "buried"/"missing"), any transition between paragraphs that drops the reader, and a running **first-use map** of key terms, symbols, and acronyms — where each is introduced and whether it's used anywhere earlier. This is the seed you'll bring to the author in Phase 2.

## Phase 2 — Brainstorm the blueprint

Now the author decides the shape of the paper before you fix a single sentence. Present the structure you just noted, and agree on what each paragraph is for.

For every paragraph, propose its **main thread**: one or two sentences stating what this paragraph is meant to accomplish, in the context of the ones around it — what it claims, what it follows from, what it sets up. Then let the author **confirm, revise, or disagree**. This is the author's paper; the blueprint is their call.

Crucially, **the author can change the structure here.** Allow them to:

- **Merge** two paragraphs into one.
- **Split** one paragraph into two.
- **Reorder** paragraphs.
- **Delete** one, or move its content elsewhere.
- **Add** a paragraph that's missing (e.g. a gap in the argument, a needed definition).

Do not argue defensively if they propose a structural change the notes suggested otherwise; short of a change that would corrupt the argument (which you should flag once), the author sets the shape. If they reorder or delete paragraphs, update the structure notes to match before moving on.

The **output of this phase is the blueprint** — recorded in the workspace (see [references/templates.md](references/templates.md)):
- the agreed orientation of the paper (one line for the whole piece), and
- for each paragraph, its main thread, plus any structural change that was made.
- the first-use map, confirmed here.

**Only after the blueprint is finalized do you segment and start editing.** The paragraph structure is settled first; sentence IDs follow it. What counts as a reviewable sentence, and the traps (abbreviations, math, citations, environments), are in [references/segmentation.md](references/segmentation.md). Segment against the *final* blueprint, not the original draft — if the author merged or reordered paragraphs, the sentence order follows the new structure. Record the total count and a sentence-id map in `revision-log.md`, tell the author how many prose sentences there are so they know the scope.

## Phase 3 — Revise one paragraph at a time

Work paragraph by paragraph in blueprint order. This is not a continuous crawl over the whole paper — **each paragraph is its own unit: revise it, then pause and confirm with the author before moving on.**

For each paragraph:

**1. Read it as a unit before its sentences.** Put the whole paragraph in view, not just the next line. Check its single claim: does the opening sentence announce it? Does the paragraph earn it? Does it hand off to the next? This is where the blueprint's target for this paragraph gets tested.

**2. Loop over its sentences.** For each unreviewed sentence in order:

**a. Show it in context.** Display the previous sentence (dimmed/as context), the current sentence, and the next sentence. Context prevents you from "fixing" a sentence in a way that breaks the flow into the next claim.

**b. Judge it.** Decide which stop-slop patterns, if any, actually apply, and whether the meaning has any fragile parts (a hedge, a precise bound, a defined term) you must preserve. Read it once more as the reader, not the author: is there jargon with a plainer equivalent, an acronym not yet expanded, or a term used here that your first-use map says isn't defined until later? Those are reader-stalls worth fixing even when the sentence is otherwise clean. If the sentence is both clean and clear to an outside reader and in the author's voice, say so plainly and offer to keep it — don't manufacture problems to look busy. A fast "this one's clean, keep it?" is a good outcome. When the sentence opens or closes a paragraph, also weigh its structural job against the blueprint: does the opener announce the paragraph's claim, does the closer hand off to the next paragraph?

**c. Offer three versions as a spectrum.** When a sentence does need work, generate exactly three rewrites at increasing edit distance, each obeying the style-profile and each changing nothing about the meaning:

- **Light** — minimal touch-up: fix the one clearest tell, leave structure intact.
- **Medium** — a genuine rewrite of the sentence for clarity and rhythm.
- **Bold** — rethink the sentence; may merge with a neighbor or recast the framing.

The spectrum is deliberate. The author's pick tells you how aggressive they want you to be, which sharpens the *next* sentence's options. Make the three meaningfully different — three near-identical rewrites waste the choice.

**d. Let the author choose** with `AskUserQuestion`. Put the actual rewritten sentence in each option's `label` (so they read the real choice) and use the `description` for the edit level plus what it fixes, e.g. "Medium — drops passive voice and the filler opener." Add a fourth option, **"Keep original"**, whenever the original is defensible. The tool always appends an "Other" choice; that is the author's channel for their own wording or a steering instruction.

**e. Interpret the response:**

- **Picked Light / Medium / Bold / Keep original** → apply that text (Keep original = no edit).
- **"Other" containing a full sentence** → treat it as their final wording; apply it verbatim. If it's ambiguous whether they meant a final sentence or an instruction, ask once.
- **"Other" containing an instruction** ("shorter", "keep the citation inline", "less formal", "I like #2 but drop the adverb") → this is steering, not a choice. Generate three new versions guided by it and stay on the same sentence. Then **sort the instruction by scope before it becomes anything lasting** — most steers are for this instance only; only a few are meant to travel. Before generalizing it, confirm with the author (see [Learning the author's preferences](references/preference-learning.md) for the exact rule: feed-forward steering is local, a standing rule is global, and one should never become the other silently).

**f. Apply and log.** Edit `working/` to the chosen text (skip if Keep original). Append to `revision-log.md`: the sentence id, original, final text, which version they picked, and the fixes applied. This file is the record of what's been reviewed — keep it current so a resume is exact.

**g. Learn — scoped, not blanket.** Update `style-profile.md` with what this choice revealed. Reference [references/preference-learning.md](references/preference-learning.md) for what signals to extract and how to let them shift your future options. Two rules that keep the learning from curdling into rule-bloat:

- **Assign the scope on the way in.** Every entry records whether it is `local` (this paper) or `global` (all the author's papers, confirmed by them). Default to `local`. A one-off "don't do X here" must never become a blanket ban.
- **Apply a confirmed kill/keep item only where its scope says it applies**, and when it applies to a new sentence, let the author see it happening — don't silence a word they'd happily accept in a different context. Un-applying a preference is cheap; noticing you've been silently enforcing a stale one is not.

**h. Advance** to the next unreviewed sentence in the paragraph.

**3. Paragraph-level check.** When the paragraph's sentences are done, pause and do a short paragraph-level look — the view the sentence loop can't give you, judged against the blueprint: does this paragraph now deliver what the blueprint said it would? Do its lead and close serve that claim? Does any term land before it's defined, per your first-use map? If something's off (e.g. the paragraph's opener drifted from its purpose, or a needed transition to the next is missing), propose a concrete fix the way you offer sentence edits, let the author choose with `AskUserQuestion`, and log it.

**4. Pause and confirm before moving on.** This is the deliberate segmentation point. Tell the author the paragraph is done: the paragraph's id and main thread, what changed, what stayed. Then ask whether to **continue on the next paragraph**, **pause and save** (the log and profile make resuming seamless), or **pause to change something** (a preference, the blueprint, or their own wording). Do not start the next paragraph until they say go. The author can stop anytime; nothing is lost.

Keep the rhythm humane. The paragraph pause is the natural break — use it, and don't ask "keep going" in between sentences. Progress is sequential and trackable; a resume is exact.

## Resuming

When `<paper-stem>-revision/` already exists: read `revision-log.md` to find the last **completed paragraph** and the next one to start, read `style-profile.md` to restore what you learned about the author, and re-read the blueprint so you know what this paper is meant to be. Re-read `working/` for current context, then continue from the first unreviewed sentence of the next paragraph. Confirm the resume point with the author before diving back in.

## Finishing

When the author stops or you reach the end:

- Summarize what changed: paragraphs reviewed, sentences edited and kept. Note any structural change made in the blueprint.
- Show a diff between `original/` and `working/` so they see the whole revision at a glance (`diff -u original/<file> working/<file>`, or a section summary if it's large).
- The edited paper is `working/<file>`; the backup is untouched in `original/`. Leave it to the author to copy `working/` back over their source — don't overwrite their original file yourself.
