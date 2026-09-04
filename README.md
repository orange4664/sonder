# hardworking-paper-writer

An interactive academic paper revision skill for Claude Code. It revises a paper **one paragraph at a time** — the author sets the shape first, then confirms each paragraph before the next one starts — while stripping AI writing tells (stop-slop) without flattening the author's own voice.

> **Forked from** [caidish/cAI-tools](https://github.com/caidish/cAI-tools) — `plugins/science-skill/skills/hardworking-paper-writer`. Original author: [caidish](https://github.com/caidish). This repo is an adapted, privately-maintained version for in-group use.

## What it does

Given a `.tex`/`.md`/`.txt` paper, it runs three beats in order:

1. **Read it all, then profile the voice.** Read the whole paper, note each paragraph's structure and a first-use map of key terms, and form a *hypothesis* about the author's style — not a rule set.
2. **Brainstorm the blueprint.** Lay the whole paper's paragraph shape (what each paragraph is for, how it flows) in front of the author and let them **confirm, revise, or restructure** — merge, split, reorder, delete, or add paragraphs. Only when the shape is agreed does segmentation and editing begin.
3. **Revise one paragraph at a time.** Work paragraph by paragraph. Within a paragraph, propose Light/Medium/Bold rewrites for each sentence, let the author choose, and track every decision. At the end of each paragraph, pause and get the author's go-ahead before moving on. Resume is seamless.

Two design points that make it "hardworking":

- **The author decides every sentence and the shape.** You propose; they dispose. The paper stays theirs.
- **It gets better as it goes without over-correcting.** Preferences are learned the way a self-learning input method "gets to know you": soft, weighted, and decaying. Every choice is a `record` (small upward weight) or `signal` (small downward weight), keyed by the sentence's `section::role` context, and weights *decay by age* so a preference the author stopped reinforcing fades rather than piling up. Nothing is banned, and only an explicit "always" becomes a `global`. The skill also keeps the author's own kept/self-written sentences as voice exemplars (`store`) and retrieves the most similar one (`nearest`) to write rewrites *in the author's register*, not a generic editor's. Implemented in `library/learn.py` (two-layer: weighted choices + CBR-style sentence memory).

## Install

Claude Code loads skills from `~/.claude/skills/<skill-name>/`. Copy this directory there:

```bash
# from the repo root
mkdir -p ~/.claude/skills/hardworking-paper-writer
cp -R SKILL.md references memory library ~/.claude/skills/hardworking-paper-writer/
```

Then in Claude Code, give it a paper path:

```
/hardworking-paper-writer path/to/paper.tex
```

It creates a `<paper-stem>-revision/` sibling directory holding `original/` (untouched), `working/`, `blueprint.md`, `revision-log.md`, `style-profile.md`, and `preference.db`.

## Skill layout

- `SKILL.md` — the main skill: the stop-slop principles, the three-phase flow, and the paragraph-by-paragraph loop.
- `references/segmentation.md` — splitting prose into sentence IDs without breaking math, citations, or environments (used *after* the blueprint is finalized).
- `references/templates.md` — the blueprint, revision-log, and style-profile formats.
- `references/preference-learning.md` — how feedback is learned soft/weighted/context-scoped, to avoid over-absorption.
- `library/learn.py` — the ported preference-learning engine (SQLite-backed), two layers: `record`/`signal`/`bias`/`global`/`prune` for weighted choices (with age-decay), and `store`/`nearest`/`list` for CBR-style sentence memory. `nearest` has three swappable backends (`backend` command): `ngram` (default, zero-dependency char 3-gram TF-IDF), `sklearn` (scikit-learn char TF-IDF), or `embed` (pretrained sentence-embedding model). The optional ones lazy-import on first use and print an actionable error if the dependency is missing.
- `memory/style-profile.md` — cross-paper (`global`) preferences, written only when the author confirms a rule as lasting.

## Adaptation notes (relative to the upstream skill)

- **Added the blueprint phase.** Upstream reads the paper and goes straight into a sentence-by-sentence pass with only a paragraph *end* checkpoint. This version won't fix a sentence until the author has agreed on what each paragraph is for — and can restructure paragraphs first.
- **Made it one paragraph at a time.** Upstream crawls the whole paper; this version pauses for the author's go-ahead after each paragraph.
- **Replaced rule-based preference learning with a two-layer IME-style store.** The upstream skill treats a steer as a standing rule and applies keep/kill-lists automatically (the "over-absorption" problem). This version ports how a self-learning input method gets familiar with you — record a choice, count it, nudge it up *in context*, never ban it, and *forget it as it ages* — from Metasequoia IME `user_dictionary_journal.cpp`, corroborated by libpinyin (`pinyin_remember_user_input` / `pinyin_train`), ZFVimIM (history-driven re-ranking), and Rime's user-dictionary `c`/`d`/`t` decay. On top of that it keeps the author's own sentences as CBR-style voice exemplars (`store`/`nearest`), so rewrites are written in the author's register. Implemented in `library/learn.py`.
- **Segments against the blueprint, not the draft.** Sentence IDs follow the agreed paragraph structure.

## License

The **upstream repo has no license file**. This fork preserves that ambiguity — it must not carry a license the original doesn't. If the group wants to publish or share this beyond itself, **the group owner needs to decide the license first**, ideally in agreement with the upstream author. Until then, treat this as in-group only.
