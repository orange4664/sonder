# Learning the author's preferences

The point of going one paragraph at a time is not just to fix sentences — it's to build a model of how *this* author wants to write, so your suggestions converge on their taste. A well-run session needs heavy steering at sentence 3 and almost none by sentence 30. This file is about how to get there — and how to get there *without* turning a one-off "don't do that" into a rule that quietly governs every later sentence.

## Read both the choice and the rejection

Every `AskUserQuestion` answer carries two signals:

- **What they picked** tells you the edit aggressiveness and wording they want.
- **What they passed over** tells you what to stop proposing. If they keep rejecting the Bold option, stop leading with bold rewrites. If they always take Light, they want a proofread, not a rewrite — shrink your edit distance across the board.

Their free-text steers ("shorter", "keep 'utilize', it's the field term", "don't merge sentences") are the strongest signal of all. But a signal is not a standing rule.

## The one rule that prevents over-absorption

**Never let a single steer silently become a general rule.** Every piece of feedback arrives at a scope, and the scope is set deliberately, not by default. A "don't write it like that" spoken to one sentence must never become a ban that applies to a later sentence the author would happily accept. This is the failure mode this skill exists to stop, and it's why the profile carries a `scope` on every entry.

### Three scopes, and how each is set

- **sentence** — applies to this instance only. It never enters the profile. Example: "drop the adverb here." You apply it, log it against the sentence, and move on. It is not a signal for future sentences.
- **local** — applies to this **paper** only. Example: "keep 'utilize', it's the field term for this venue." You record it under the `local` scope. It dies with the paper; it is not carried into the author's next paper.
- **global** — applies to all the author's papers. This is the rarest scope, and it is only ever set by **explicit confirmation**. Example: "I always use 'we', never passive, in every paper I write." It is written to the cross-paper memory and persists.

### The two ways a preference moves

1. **By explicit confirmation.** If the author says a preference is a standing one ("from now on always…", "I never do this in any paper…"), record it at the scope they stated. That's the only time `global` gets set.
2. **By repeated pattern — but only locally.** If the author consistently picks the same option or repeats the same steer across many sentences within this paper, you may infer a `local` preference and record it. Do *not* upgrade it to `global` without asking.

Everything else — most steers, most one-off choices — stays at `sentence` scope and is not learned as a rule at all.

### When a steer might be a rule, ask once

If a steer is broad enough that you suspect the author wants it everywhere ("shorter", "less formal"), do not silently generalize. Ask once, plainly: "Should I apply this to the rest of the paper, or just this sentence?" One short question at the moment of ambiguity beats enforcing a rule the author never intended.

## What to track in style-profile.md

Update after meaningful choices (not every single sentence — when something is revealed):

- **Edit aggressiveness:** running sense of Light / Medium / Bold preference. Bias future option-spreads toward it.
- **Voice markers:** "we" vs passive vs "the authors"; first vs third person. Match it.
- **Vocabulary:** words they insist on keeping (field terms, pet phrases) and words they consistently cut. Maintain a keep-list and a kill-list — each entry scoped.
- **Punctuation:** do they accept em-dashes, semicolons, parentheticals, or reject them? Honor it.
- **Hedging:** do they want claims stated flatly or softened ("may suggest")? Field- and author-specific.
- **Structural limits:** will they let you merge/split sentences, or do they want one-in-one-out edits?

## How to fold it back in

When generating the next sentence's versions:

1. Start from the author's learned aggressiveness, not a fixed Light/Medium/Bold ladder. If they've shown they want light edits, make all three options lighter and closer together. If they want bold, push harder.
2. Apply a confirmed keep- or kill-item **only where its scope says it does**. A `local` kill-list applies within this paper; a `sentence` item doesn't apply anywhere else. And when a rule would actively silence a word, don't do it silently — let the author see the application. That's the moment they can say "not here."
3. Keep the spectrum's *purpose* even as you recenter it: the three options should still differ enough to be a real choice, just within the band the author has shown they live in.

## Prune, don't accumulate

The profile is a model, not an archive. A growing list of stale rules is worse than a shorter one.

- **At each paragraph pause**, and **when the author stops**, skim the profile. Ask whether each pref still holds — a preference confirmed for one paragraph may not for the next.
- **Conflict resolution:** if a newer choice contradicts an older entry, the newer one wins. Update the entry, don't stack a contradiction on top of it.
- **Don't keep dead entries.** A kill-item that hasn't been exercised in many paragraphs, or one that came from a one-off you later generalized, is a candidate to drop. Better to re-learn a preference than to keep enforcing a stale rule the author never meant.

## Preserve, don't homogenize

The failure mode to guard against: every sentence drifting toward the same clean, generic cadence until the paper loses its author. Re-read a few of their kept-original sentences periodically. Those are ground truth for their voice. If your proposals are starting to sound unlike those sentences, pull back — the author's clean original is the target, not a textbook ideal.

## Cross-paper memory

`global` preferences live in the persistent cross-paper profile at `~/.claude/skills/hardworking-paper-writer/memory/style-profile.md`. Load it at the start of a new paper (Phase 0) and write to it **only** when the author explicitly confirms a preference as lasting. Everything else stays in the workspace `style-profile.md`, which dies with the paper. The line between the two is the line between "what this author wants in general" and "what this author wants in this paper" — don't blur it.
