# Learning the author's preferences (soft, IME-style)

The goal of going paragraph by paragraph is not to learn rules about how the
author writes — it's to get *familiar* with how this author decides, so that
your suggestions converge on their taste without ever turning one comment into
a permanent rule. This file describes that mechanism. It is a direct port of
how a self-learning input method "gets to know you" (Metasequoia IME
`user_dictionary_journal.cpp`; corroborated by libpinyin and ZFVimIM).

## The failure mode this replaces

The older version of this skill learned **rules**: the author says "don't write
it that way" once → it enters a kill-list → every later sentence is silently
driven to avoid it, even where the author would happily accept it. That is
over-absorption. A rule that applies everywhere, forever, from one observation.

The IME does the opposite. It never bans a word; it just raises the one you
keep choosing, *in that context*, *after you've chosen it enough times*. A
single "don't" is not a rule — it's one downvote.

## The three ideas, ported

1. **Record the choice, not the rule.** Every decision is logged as a tuple of
   `(context, option, weight)`. The option isn't "the author likes X" — it's
   "in this kind of sentence, the author chose this rewrite." Nothing is
   inferred beyond what was chosen.

2. **Change only after repetition, and only within the context.** Each
   `(context, option)` has a pick count. Nothing shifts until it's picked enough
   times (a small threshold). And the shift is scoped to the context that
   produced it — the same option in a *different* role/section is a separate
   entry and doesn't inherit the bump.

3. **Nudge, never delete.** A preferred option rises; a rejected option dips.
   Neither is removed. This is what keeps the author's voice from being
   flattened — the full range of options stays on the table, just reordered.

## The context is the sentence's job, not its topic

In the IME the context key is the preceding text (so "我吃" and "我是" learn
separately). Here the analog is the sentence's **role in the argument** plus
the **section** it lives in:

- **section:** abstract / intro / methods / results / discussion / conclusion
- **role:** topic (opens the paragraph's claim) / support / transition /
  conclusion / detail / hedge

So "prefer mid-length topic sentences in the intro" is one tracked preference,
while "prefer lighter edits in the discussion" is another. They don't bleed
into each other, any more than "我吃" bleeds into "我是". If the author is
writing a methods section, their taste there is learned separately from their
taste in an abstract.

## How to fold it back in

`library/learn.py` exposes the computation. When you're about to propose a
sentence's rewrite options, call `bias` to get the current weight for each
option in this context, and lead with the higher-weighted ones. Do **not**
drop the low ones — the point is exactly that they stay available.

- If the author picks an option, `record` it — a small upward `weight` on
  `(context, option)`.
- If the author rejects an option or steers away from it, `signal` it — a small
  downward weight. This is **not** a ban. It competes back up if the author
  later prefers it.
- Only when the author says "I always want this" (explicitly, across papers) do
  you write a `global` entry. That is the only thing that behaves like a rule,
  and it must be earned by an explicit statement, never inferred.

Trust the author at each step: when you apply a nudged preference, let them see
it working. If they say "not here," that's a `signal`, not a new global rule.

## Prune, don't accumulate

Stale preferences are as bad as wrong ones. At each paragraph pause, run
`prune` — drop entries that have not been picked recently or were barely ever
picked. The IME does the same by rebalancing its managed weight range; a tight,
clean preference set is more useful than an accumulating archive.

## Keep it soft even when it's obvious

Re-read a few of the author's kept-original sentences periodically. Those are
the ground truth for their voice. If your proposals are starting to sound
uniformly different from those sentences, pull back — the author's clean
original is the target, not a textbook ideal. The mechanism should reorder
options within the author's voice, not erode it.
