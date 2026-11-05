# sonder

An interactive academic paper revision skill for Claude Code. It revises a paper **one paragraph at a time** — the author sets the shape first, then confirms each paragraph before the next one starts — while stripping AI writing tells (stop-slop) without flattening the author's own voice.

> **Forked/adapted from** [caidish/cAI-tools](https://github.com/caidish/cAI-tools) — `plugins/science-skill/skills/hardworking-paper-writer`. Original author: [caidish](https://github.com/caidish). This version is reworked and re-published under GPL-3.0.

## What it does

Given a `.tex`/`.md`/`.txt` paper, it runs three beats in order:

1. **Read it all, then profile the voice.** Read the whole paper, note each paragraph's structure and a first-use map of key terms, and form a *hypothesis* about the author's style — not a rule set.
2. **Brainstorm the blueprint.** Lay the whole paper's paragraph shape (what each paragraph is for, how it flows) in front of the author and let them **confirm, revise, or restructure** — merge, split, reorder, delete, or add paragraphs. Only when the shape is agreed does segmentation and editing begin.
3. **Revise one paragraph at a time.** Work paragraph by paragraph. Within a paragraph, propose Light/Medium/Bold rewrites for each sentence, let the author choose, and track every decision. At the end of each paragraph, pause and get the author's go-ahead before moving on. Resume is seamless.

Two design points that make it "sonder":

- **The author decides every sentence and the shape.** You propose; they dispose. The paper stays theirs.
- **It gets better as it goes without over-correcting.** Preferences are learned the way a self-learning input method "gets to know you": the actual accepted candidate's words/phrases, optional POS/dependency/slot structure, and a small academic argument layer (method → result → comparison → limitation → conclusion) get soft, context-scoped, decaying weights. Every candidate round uses `rank-file` then `choice-file` with a persisted `round_id` so retries cannot learn the same author decision twice; the older `record`/`signal` label API remains compatible and breaks only equal candidate-score ties. Ordinary feedback never bans an expression; only an explicitly confirmed lasting preference enters the cross-paper text profile. The skill also keeps the author's own kept/self-written sentences as voice exemplars (`store`) and retrieves the most similar one (`nearest`) to write rewrites *in the author's register*, not a generic editor's. Implemented in `library/learn.py` (phrase features, optional local syntax features, auditable academic cues, and CBR sentence memory, with legacy label compatibility).

The current default for new workspaces is `adaptive-v3`. With reviewed relation
inventories, it can learn different preferences for conditional and unconditioned
predicates, share voice evidence across verbal/nominalized wording, and retain
opposing choices without rewarding fields merely being marked unknown. This is
soft, contextual online learning, not a trained parser or a guarantee of prose
quality. Existing saved policies remain unchanged on resume; upgrades preserve
native weights and old receipts, starting the new layer empty. See
[relation coverage](references/relation-learning.md) and
[adaptive learning](references/adaptive-learning.md) for limits and configuration.

An author can explicitly exclude a single editorial decision from learning.
Save `learn_from_choice:false` in its candidate file before finalizing it. The
same choice command then records a retry-safe editorial receipt, with no phrase,
syntax, label or exemplar update and no feature/model work. Ordinary choices
still learn by default; this does not erase older learning or manuscript files.

New writing rounds also save their section and rhetorical role as
`learning_context`. Ranking and feedback check this saved context, so recovering
a decision after a blueprint change cannot silently learn under a different
role. New receipts retain the context; legacy files and receipts stay compatible.

Custom section/role names also stay distinct when they contain colons. Schema 9
preserves ordinary keys and uses a versioned pair encoding where the old `::`
concatenation could collide. If an old ambiguous key already has history, new
ranking/learning stops with an explicit error and preserves those rows; it cannot
safely guess which context owns them. Historical receipt replay and explicit
non-learning choices remain available. See [upgrade guidance](references/runtime-setup.md).

Rhetorical uncertainty is conservative: masking an attribute on unchanged
evidence cannot improve a candidate's score or create a new learned action
position. Stable known parts remain usable; old complete inputs and saved
receipts retain their behavior.

## Install

Claude Code loads skills from `~/.claude/skills/<skill-name>/`. Install or update from the repository with:

```bash
# from the repo root
python3 scripts/install.py
```

The installer updates program files, verifies the installed engine's capabilities, then publishes the new `SKILL.md`. It initializes `memory/style-profile.md` only if absent. Existing author memory and skill-directory symlinks are preserved. For another host, pass `--target /path/to/skills/sonder`. Keep the installation directory when upgrading. Existing paper databases migrate on first use; the installation check does not open them. A failed update reports an error and does not publish new Skill instructions; individual managed files may already have been copied, so retry the complete update before use.

On every new or resumed revision, the Skill runs `scripts/check_runtime.py` from its own installation. It rejects missing or older engines that cannot understand the current structure annotations and retry-safe choices. `library/learn.py engine-info` also provides the capability report directly. These commands use only the standard library and do not load models. Code support does not mean a given paper has enabled its structure backend; an explicit `syntax-backend off` stays off.

Optional packages installed in a virtual environment require that environment's
absolute `bin/python` path for the checker and all engine calls. Keep that path
as-is; resolving its symlink to the base Python can lose the environment. Ranking
reports the requested/effective syntax backend and any fallback reason. A mixed
parser/fallback round still learns the selected sentence, but does not interpret
missing parser features as rejection. Saved choice receipts preserve these
diagnostics on retry.

The optional Stanza parser uses SHA256 content identity for its selected model
files and provider version. A renamed label or moved identical model keeps its
learned weights; different contents get a separate namespace. Legacy tag-only
Stanza rows are retained without automatic adoption. Phrase/exemplar and
writing-model structure memory are unchanged, and old receipts still replay.

Then in Claude Code, give it a paper path:

```
/sonder path/to/paper.tex
```

It creates a `<paper-stem>-revision/` sibling directory holding `original/` (untouched), `working/`, `blueprint.md`, `revision-log.md`, `style-profile.md`, and `preference.db`.

## Skill layout

An optional [local stdio MCP companion](references/mcp.md) keeps the existing
engine and local model in one process for a single revision workspace. Install
the pinned SDK in a separate environment with `scripts/requirements-mcp.txt`;
the normal CLI still needs only Python's standard library. It exposes status,
binding, exemplar retrieval, ranking, explicit choice recording and pruning.
The author workflow and database are shared with the CLI, including safe
retries across server restarts. Configure it in your host only when wanted.

- `SKILL.md` — the main skill: the stop-slop principles, the three-phase flow, and the paragraph-by-paragraph loop.
- `references/segmentation.md` — splitting prose into sentence IDs without breaking math, citations, or environments (used *after* the blueprint is finalized).
- `references/templates.md` — the blueprint, revision-log, and style-profile formats.
- `references/preference-learning.md` — how feedback is learned soft/weighted/context-scoped, to avoid over-absorption.
- `scripts/install.py` — repeatable installation and updates that preserve author memory.
- `scripts/check_runtime.py` — verifies that this installation's engine supports the Skill's input/feature versions and feedback receipts, without accessing author databases.
- `references/runtime-setup.md` — optional interpreter/model/MCP setup, fallback diagnosis and update guidance; normal writing follows the shorter main Skill and reads detailed protocols when its step needs them.
- `references/annotation-binding.md` — declarative drafts for `learn.py bind-file`, which locates exact fragments within their scopes and computes hashes without per-round binding scripts; ambiguous text requires an explicit occurrence. Use `prepare-file DB SECTION ROLE DRAFT OUTPUT [--summary]` (or MCP `sonder_prepare`) to bind and rank the same draft in one checked call: a binding failure cannot fall through to ranking an older output. It never approves or learns; ranking failure can leave the valid canonical for retry.
- `library/learn.py` — the ported preference-learning engine (SQLite-backed). New revision rounds use `rank-file`/`choice-file` to learn the actual candidate sentence's context-scoped word/phrase features, versioned POS/dependency/slot features, and parser-independent academic argument cues; `store`/`nearest`/`list` keep complete author exemplars. The older `record`/`signal`/`bias` label API remains compatible, with edit intensity used only for equal candidate-score ties. `nearest` has three swappable backends (`backend` command): `ngram` (default, zero-dependency char 3-gram TF-IDF), `sklearn` (scikit-learn char TF-IDF), or `embed` (pretrained sentence-embedding model). The optional sentence-structure backend is selected separately with `syntax-backend`: `heuristic` (default, zero-dependency conservative cues), `stanza` (local `zh-hans` `default_fast` model directory), or `off`. The syntax backend lazy-imports and never downloads a model during a revision; the separate `embed` similarity backend retains its own optional model-loading behavior.
- `memory/style-profile.md` — explicitly confirmed standing text preferences, with author/language/scope and confirmation provenance. The writing assistant reads these for new papers. Ordinary phrase/structure weights and author exemplars stay in each paper's database; the legacy `global` CLI only changes labels within that database and does not save this profile.

## Adaptation notes (relative to the upstream skill)

- **Added the blueprint phase.** Upstream reads the paper and goes straight into a sentence-by-sentence pass with only a paragraph *end* checkpoint. This version won't fix a sentence until the author has agreed on what each paragraph is for — and can restructure paragraphs first.
- **Made it one paragraph at a time.** Upstream crawls the whole paper; this version pauses for the author's go-ahead after each paragraph.
- **Replaced rule-based preference learning with a sentence, phrase, optional-structure and academic-move IME-style store.** The upstream skill treats a steer as a standing rule and applies keep/kill-lists automatically (the "over-absorption" problem). This version ports how a self-learning input method gets familiar with you — record accepted wording and structure, nudge context-local features, never ban an expression, and *forget it as it ages* — from Metasequoia IME `user_dictionary_journal.cpp`, corroborated by libpinyin (`pinyin_remember_user_input` / `pinyin_train`), ZFVimIM (history-driven re-ranking), and Rime's user-dictionary `c`/`d`/`t` decay. The academic layer adds a small auditable vocabulary of argument moves and their order; it is a domain adapter, not a claim that a general UD parser was trained on papers. On top of that it keeps the author's own sentences as CBR-style voice exemplars (`store`/`nearest`), so rewrites are written in the author's register. Implemented in `library/learn.py`.
- **Segments against the blueprint, not the draft.** Sentence IDs follow the agreed paragraph structure.

## Validation

New writing rounds also use [grounded structure analysis](references/structure-analysis.md)
from the writing model already reading the manuscript. Optional candidate
annotations bind rhetorical actions, polarity and assertion strength to exact
text spans and a whole-sentence hash. The engine validates that binding and
learns context-scoped action-order preferences; it does not certify semantic
labels or train a personal neural model. Unknown analysis is allowed. No extra
API or Stanza installation is required. Legacy inputs keep working without it.

[Grounded grammar frames](references/grammar-frames.md) additionally describe
verbal/nominalized events, voice, participant order and subordinate relations.
Input version 2 also covers direct property predicates and copular/value
statements, while keeping existing version-1 weights and decisions usable.
Input version 3 adds temporal clauses, distinguishing their fronted, embedded
or trailing placement from logical conditions. Existing version-1/2 inputs,
weights and receipts remain valid; committed unknown relations are not upgraded.
Input version 4 adds comparative framing, such as a comparison reference before
or inside the main claim. It reuses grounded head/dependent and matrix-predicate
evidence to learn placement across different wording; a comparison verb or a
quoted name does not automatically become this relation. Version-1/2/3 inputs
and memory remain valid. Uncertain scope can still abstain.
Input v5 adds explicit recipient slots, allowing information/object recipient
order to transfer across different words. New rounds use v6 across
alternatives; mixed older annotations cannot count missing roles as rejection.
Input v6 makes each expression's role certainty explicit: unresolved roles keep
known form/voice evidence but cannot teach a complete argument-order template.
This depends on the writing model reporting uncertainty, not automatic detection
of every missing argument. Old input versions and memories remain usable.
Their separate soft weights can transfer to different words. These are fallible
model annotations validated against exact text, not a complete grammar parser.
Grounded main-clause predicates and participants additionally distinguish
fronted, embedded and trailing condition/cause/concession clauses. Missing
evidence abstains; older weights remain usable without feedback replay.

Explicit grammar annotations also take precedence over the heuristic's raw
character templates, including when the writing model abstains. This avoids
mistaking word-internal markers (for example 并 in 归并) for learned syntax.
`heuristic_template_active` reports whether that fallback family is used.
Clause length, punctuation, grounded constructions and real parser signals
remain separate inputs. Legacy unannotated inputs and stored receipts stay
compatible; existing raw template weights are retained.

```bash
bash tests/run_e2e.sh
python3 tests/check_fault_detection.py
```

The optional MCP gate uses real SDK clients and stdio subprocesses, separately
from the standard-library suite. It fails if the SDK is absent. With a local
model directory, it additionally requires actual Stanza results and records
cold/repeated-call timings:

```bash
/absolute/venv/bin/python -B -I tests/check_mcp_sdk.py --evidence /tmp/sonder-mcp-check
/absolute/venv/bin/python -B -I tests/check_mcp_sdk.py --evidence /tmp/sonder-mcp-model-check --model-dir /absolute/stanza-models
```

The first command runs standard-library tests for the learning engine, phrase/syntax-memory migration, idempotent feedback receipts and concurrent retries, literal manuscript input, documented commands and installation. The second separately breaks legacy weights, sentence similarity, candidate ranking, syntax extraction, lexical cue guards, whole-round feedback, receipt lookup and decision fingerprinting in temporary copies and verifies that the gate fails. These are automated integration checks, not a simulation of a real author revising a paper.

Routine CLI ranking can use `rank-file DB SECTION ROLE JSON --summary`; the
MCP equivalent is `sonder_rank` with `summary:true`. Both preserve complete
candidate prose, ranking and diagnostics while omitting repeated annotations
and feature lists. Keep the saved candidate file for feedback and auditing;
omit the flag to retrieve full matched-feature details. Default API output
remains unchanged. The installer checks the `compact_ranking` capability.

For manuscript sentences, the skill uses `store-file` / `nearest-file` with UTF-8 files. Candidate rounds use `rank-file` and `choice-file` with UTF-8 JSON files, so the learned object is the real wording rather than a Light/Medium/Bold label. Structure learning is an optional local secondary signal: the zero-dependency path emits conservative cues plus auditable academic moves such as `method`, `result`, `hedge`, and `limitation`; a local parser adds POS/dependency evidence when available. Without a local parser the heuristic remains active; `off` explicitly disables structure learning. The heuristic guards known word-internal cue collisions, but does not provide general semantic parsing. Structure version `v3` keeps old `v2` weights isolated while preserving author exemplars and phrase history. Direct `store` / `nearest` calls remain compatible; use an argument array when the text contains shell-sensitive characters.

## License

[GPL-3.0](LICENSE). The preference-learning engine in `library/learn.py` is a port of the adaptive-candidate mechanism in Metasequoia IME (`user_dictionary_journal.cpp`, GPL-3.0), corroborated by libpinyin and ZFVimIM, so this repo is distributed under GPL-3.0. The upstream `cAI-tools` repo had no license; this repo's license is its own choice and applies to this fork's code and adaptations.


Rhetorical input v2 can preserve one proposition across multiple exact fragments,
for example a result interrupted by a method phrase. New rounds use units/parts;
legacy spans and receipts remain compatible. A separate learned surface-topology
score distinguishes an interrupted unit from independent units without diluting
old preferences when untrained. It does not infer logical dependencies or certify
semantic labels. See [structure analysis](references/structure-analysis.md).


Phrase learning now excludes pure numeric tokens and explicit TeX math/citation/
reference payloads, while retaining ordinary formatting prose and named terms.
This prevents sample counts and citation keys from becoming lexical preferences.
Original text, full author examples and old receipts remain unchanged. See the
[format guard and its limits](references/preference-learning.md#scientific-literals-and-phrase-learning).

New writing rounds save a shared `edit_scope` for all candidates; merging two
sentences requires complete two-sentence alternatives, including Keep. The
existing bind/rank/record-choice commands validate the scope and preserve it in
new receipts. File editing and stable-ID recovery remain host responsibilities.
See [edit scope and recovery](references/edit-scope.md); no additional MCP tool is needed.

Legacy ranking uses scoring version 2: base syntax and rhetorical analysis are averaged
independently, alongside grammar and topology. This prevents unrelated parser
feature counts from changing the strength of otherwise identical rhetorical
evidence. New rank rows expose the version and analysis_score; old learned data
and saved choice receipts are retained, while annotated scores may change.

The current Skill initializes the paper's `adaptive-v3` policy with
`learning-policy DB ensure-adaptive`, preserving every saved policy, including
`legacy` and `adaptive-v1`. Adaptive scoring versions 3 (v1) and 4 (v2) separate relative preferences from a bounded,
decaying profile of accepted expressions. A lone author sentence still teaches
the profile; comparable alternatives teach differences. The first activation
keeps old weights/times as a fixed reference and preserves original exemplars and
receipts. Queries never learn, and retries never populate a second model from an
old decision. V2 additionally accumulates local contrary evidence so sustained
changes can correct an old preference without shrinking unrelated memories.
V1 and v2 share learned weights; switching into or out of v2 clears only its
transient correction trace. CLI and MCP use the same transaction and setting. See
[adaptive learning](references/adaptive-learning.md) for the formula, diagnostics,
switching policy and limits. Existing direct callers remain legacy until enabled.

Author exemplar retrieval compares supported formatting-transparent text and
returns the exact saved author sentence. For Markdown sentence files use
`nearest-file DB FILE [N] --text-format markdown` (and the same flag on a
separate `store-file` import); MCP nearest accepts `text_format:"markdown"`.
Normal choices propagate their saved source format automatically. Schema 5
upgrades only derived exemplar indexes, leaving author/history fields intact.
Old rows retain legacy interpretation rather than guessing Markdown. Keep a
backup; older code is not a downgrade path for upgraded databases.

New writing decisions save a durable receipt with
`choice-file DB SECTION ROLE JSON --receipt-file FILE` (MCP: optional
`receipt_file` inside `choice-receipts/`). Create the output directory first.
The original DB receipt is saved atomically without replacing conflicting files;
retries keep the same decision ID and do not reinforce again. A save failure
after commit is reported explicitly. Link the saved receipt from the revision
log using normal file edits; this does not automate manuscript application.

New drafts use explicit grammar input v7 to preserve distinct coordinated
predicates with shared literal arguments, such as 清洗 and 干燥 in
“研究者清洗并干燥样品”. Equal outer ranges no longer force removal of an argument;
separate grounded anchors and coverage targets are still required. Historical
input versions and receipt identities remain unchanged. See the shared-evidence
section in `references/grammar-frames.md`.
