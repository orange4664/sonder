---
name: sonder
description: Revise an academic paper paragraph by paragraph with the author, stripping AI writing tells (stop-slop) while preserving the author's own voice. Use this whenever the user wants to polish, tighten, edit, proofread, or "de-slop" a paper, manuscript, draft, abstract, or thesis chapter — especially when they want careful interactive revision rather than a one-shot rewrite. Trigger on requests like "help me revise my paper", "clean up my draft", "make my writing sound less AI", "go through my abstract line by line", or when they hand over a .tex/.md/.txt paper to improve.
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - AskUserQuestion
---

# Sonder

Revise a `.tex`, `.md` or `.txt` paper paragraph by paragraph with its author.
Read the whole paper, agree on its paragraph blueprint, then revise sentences.
The author chooses both the structure and every wording change. Learned preferences
are soft, contextual guidance; keep the author's voice and scientific meaning.

Use the host's author-choice tool (`AskUserQuestion` in Claude Code, or its local
equivalent). Show actual proposed text and a short explanation. Technical
annotations, diagnostics and successful setup details stay internal unless asked.

## Read references when their step needs them

| Step or situation | Reference |
|---|---|
| Seed the workspace, blueprint and decision log | [templates](references/templates.md) |
| Segment after the agreed blueprint | [segmentation](references/segmentation.md) |
| Prepare the first candidate round | [binding](references/annotation-binding.md), [edit scope](references/edit-scope.md), [rhetorical analysis](references/structure-analysis.md), [grammar](references/grammar-frames.md), [relation coverage](references/relation-learning.md); read these once, then revisit relevant cases |
| Choose section/role; interpret steering or a standing preference | Relevant sections of [preference learning](references/preference-learning.md): the context is the sentence's job, feedback scope, explicit exclusion and cross-paper persistence |
| Configure Python/models/MCP, diagnose fallback, or update an installation | [runtime setup](references/runtime-setup.md); use [MCP](references/mcp.md) when that transport is available |
| Investigate engine behavior, cache/schema migration or scoring | [maintenance contract](references/learning-engine-contract.md) |

The latter two rows are conditional. Ordinary writing uses the configured runtime;
it does not require loading implementation details or installing a parser.

## Writing judgment

Preserve claims, evidence strength, negation, necessary conditions, attribution,
numbers, units, formulas, citations, labels and defined terms. Never add a result
or turn a plan, hypothesis or selected clause into a completed finding.

Remove filler, unsupported emphasis, vague claims and mechanically repeated
structures when they actually impair the passage. Prefer concrete subjects and
clear verbs; active voice often helps, but respect disciplinary usage. Vary rhythm
without forcing every sentence into one pattern. Keep the argument's necessary
density and the author's register. A clean sentence is a valid outcome: recommend
keeping it instead of inventing a fault.

Also read as an outside researcher. Track terms, symbols and acronyms used before
their introduction. Check each paragraph's main claim, supporting progression and
connection to the next; handle structural problems through the blueprint and
paragraph checkpoints, with author approval.

## 0. Locate the paper and prepare or resume

Resolve this installation's `scripts/check_runtime.py` and `library/learn.py` to
absolute paths. Before opening a paper database or changing the revision workspace:

<!-- sonder-command:runtime -->
```bash
python3 -I "<runtime-check-path>"
```

Require `status:compatible`, then use its absolute `engine_path` as `<learn-path>`.
If the checker is missing or fails, report the incompatible installation and pause
workspace edits/learning. Do not bypass it by dropping annotations or switching
to an unrelated engine. This verifies code support, not semantic accuracy.
The current checker requires schema 9, context-key version 2 and
`injective_context_keys`. If a paper reports `ambiguous legacy context`, preserve
its database and saved context; read the [upgrade guidance](references/runtime-setup.md).
Do not rename the context or clear history to bypass the error.

Use absolute paths in every fresh shell. If dependencies are in a virtual
environment, use that environment's absolute `bin/python` for both checker and
engine; do not resolve its symlink to the base interpreter. Keep an explicitly
configured `off` backend and explain that structural learning is disabled.

Find the source path from the user's request; ask for it if absent. For a folder,
prefer `.tex`, then `.md`. If only PDF is available, explain that this workflow
needs an editable text source; use an available conversion workflow if requested.

If `<paper-stem>-revision/` already has `revision-log.md`, follow **Resuming**.
Otherwise create a sibling workspace:

```text
<paper-stem>-revision/
  original/<source file>       untouched source backup
  working/<source file>        live editable copy
  blueprint.md
  revision-log.md
  style-profile.md
  candidate-inputs/            complete drafts, candidates and saved decisions
  choice-receipts/             original feedback receipts saved by the engine
  preference.db               this paper's learning database
```

Copy the source into `original/` and `working/`; edit only `working/`. Seed tracking
files from the templates and tell the author where the workspace is.

Before seeding a fresh profile, read this installation's `memory/style-profile.md`
if present. Carry over only explicitly confirmed entries whose author, language
and scope apply. Placeholders/examples are not confirmations. This text profile
is the only cross-paper carryover: ordinary weights and exemplars stay in each
paper's database and survive resuming that paper.

Initialize this paper's learning policy after resolving its workspace/database:

<!-- sonder-command:learning_policy -->
```bash
python3 -I "<learn-path>" learning-policy "<db-path>" ensure-adaptive
```

Use this on setup and resume. It enables `adaptive-v3` only when unset and
preserves every saved policy, including `legacy`, `adaptive-v1` and `adaptive-v2`. It saves old weights as a reference once;
it does not approve text, replay choices or alter the author's original examples.
Accepted expressions inform a bounded profile; comparable choices teach relative
preferences. Both remain contextual and decay. Read [adaptive learning](references/adaptive-learning.md)
only when investigating its scores, policy or migration.

For a workspace already using an older policy, keep that saved setting. Explain
once that relation-conditioned preference learning requires `adaptive-v3`; if
the author requests the upgrade, select it explicitly through `learning-policy`
or the matching MCP call. This preserves native learned weights and old receipts,
starts the new relation layer empty, and resets only transient correction traces.
Do not turn a policy change into a sentence approval or replay old decisions.

## 1. Read the whole paper

Read `working/` end to end before proposing the blueprint or editing prose.
Record tentative observations about voice in the local style profile, explicitly
as hypotheses. They must not prohibit otherwise good alternatives.

Record structure notes in the revision log: each paragraph's topic sentence or
missing/buried main claim, weak transitions, and a first-use map of terms, symbols
and acronyms. These are material for the author to consider, not permission to edit.

Only an explicit standing preference becomes a cross-paper profile entry. Save
its wording, applicable author/language/scope and confirmation source/date in the
installation's `memory/style-profile.md`, preserving unrelated entries; reflect
it in this paper's profile too. A later explicit correction updates that entry,
while a local exception stays local. A preference confirmation does not select a
pending sentence. Read the persistence procedure in the preference guide when
making this update; the legacy `global` command is not a substitute.

## 2. Agree on the paragraph blueprint

For each paragraph, propose its main thread in one or two sentences and its
relationship to neighboring paragraphs. Let the author confirm, revise or disagree.
They may merge, split, reorder, delete, move or add paragraphs. Flag a change that
would corrupt the argument, then resolve it with the author.

Save the agreed paper orientation, paragraph threads, structural changes and
first-use map in `blueprint.md`. Only after that agreement, segment against the
final structure using the segmentation guide. Record stable sentence IDs and total
count in the log, and tell the author how many prose sentences will be reviewed.
A later structural change updates the blueprint and affected
mapping; preserve prior IDs and decision history.

## 3. Work through one paragraph

Read the whole paragraph before its sentences. Test it against its agreed main
thread. For each unreviewed sentence, show the previous/current/next context and
judge the actual wording. The loop below keeps meaning, author choice and learning
in the same saved round.

### A. Fix context and retrieve author examples

Choose section (`abstract`, `intro`, `methods`, `results`, `discussion`, `conclusion`)
and role (`topic`, `support`, `transition`, `conclusion`, `detail`, `hedge`) from
the sentence's job. Read the context definitions in the preference guide once.
Log the paragraph purpose, primary job, plausible secondary functions and any
uncertainty before ranking. Use comparable prior decisions consistently. A keyword,
negative wording, position, annotation label or score must not determine the key.

Save the exact current sentence as a UTF-8 file, preserving every character. Use
file input; never interpolate manuscript text into shell commands.

<!-- sonder-command:nearest -->
```bash
python3 -I "<learn-path>" nearest-file "<db-path>" "<sentence-file>" 3
```

Append `--text-format markdown` for a sentence from Markdown. Omit it for
TeX/plain/legacy input; do not infer Markdown from asterisks. Matching compares
supported formatting-transparent views and returns exact saved author text.

Retrieval searches author examples throughout this paper; it does not filter
by section/role. Phrase and structure weights are scoped to the saved section/role,
so an example can be available here even when this context has no learned scores.

Treat retrieved examples as approved wording in their original contexts. Before
borrowing rhythm or construction, compare study design, conditions, evidence
strength, negation/quantifiers and whose verification is described. An example
about an established cause cannot supply a causal claim for an inconclusive study.
Use a compatible lower-ranked example, or the local profile if none fits. Similarity
and a difference in wording do not by themselves establish a style preference.

### B. Draft alternatives and bind the complete round

When rewriting is useful, offer three meaningful levels: Light for minimal repair,
Medium for a clearer recasting, Bold for a larger restructuring. Keep every version
faithful to the source. Include Keep original when defensible; recommend it for a
clean sentence. If a candidate merges neighbors, all candidates, including Keep,
must cover the same full span. Identify the affected IDs to the author.

Read the binding, edit-scope, analysis and grammar guides before the first round.
Prepare a literal-fragment draft with:

- A fresh persisted `round_id` for this decision, reused for retries.
- Exact `learning_context:{"section":"...","role":"..."}` for every call.
- `edit_scope`: ordered stable IDs, exact `before_text`, and `keep_original_id`
  when Keep is offered. All candidates cover this complete span.
- Root `text_format:"markdown"` for new Markdown rounds. Omit for TeX/plain or
  existing legacy decisions. This belongs on the whole envelope, not candidate rows.
- Exact candidate IDs, labels and texts, each with `analysis.units` (v2) and
  `grammar` frames with explicit `version:7`. Include Keep. Supply
  `slots_complete` on expressions. Distinct predicates may share the same
  necessary evidence range and explicit participants; keep their separate event
  slots. Do not drop shared arguments or pad punctuation to evade a range clash.
  Preserve the saved version on historical drafts and author decisions.
- Candidate-level `relation_coverage` (v1), referencing this draft's grammar frame
  indexes. Review each predicate's complete incoming relations and each link's
  event/non-event/unknown target using the relation guide. Missing information
  stays unknown; never fill all predicates as complete mechanically. The binder
  remaps these indexes and fingerprints the canonical evidence.

Use the guides' semantic distinctions. Preserve necessary conditions and nested
negation; an inner predication can have grammatical form without being asserted
true. Ground identifiable embedded events and their participants, while ordinary
names/values remain slots. Unknown attributes and honest abstention are valid;
do not omit known clauses, duplicate evidence, force certainty, or relabel a
candidate to improve its score. `complete` is not a semantic certificate.

Before binding, compare conserved propositions across the alternatives. Keep
their negation, modality and scope visible at a consistent semantic granularity;
a new sentence boundary or conditional wrapper must not turn an unchanged
negative consequence into a wholly affirmative unit. Preserve genuine changes
in organization and use unknown when scope cannot be separated faithfully.
See the analysis guide's **Conserved propositions across a round** for examples.

Use the binder for offsets/hashes instead of writing a binding script per round.
Disambiguate repeated fragments as documented. Preserve rejected drafts and fix
their actual problem; do not discard annotations to make validation succeed.

For the ordinary writing loop, use checked preparation in step C. Binding alone
remains useful when inspecting annotations without opening the paper database:

<!-- sonder-command:bind -->
```bash
python3 -I "<learn-path>" bind-file "<draft-file>" "<candidate-file>"
```

The binder validates and publishes a complete canonical file without overwriting
a different file. It does not approve a choice, generate the round ID or infer
meaning. Keep approved files immutable; do not retrofit newer format/version
fields into historical decisions. See binding guidance for preparing an older,
genuinely unapproved Markdown pool into a new canonical filename.

### C. Prepare, rank, then ask the author

Bind the draft and rank its exact candidates in one checked call:

<!-- sonder-command:prepare -->
```bash
python3 -I "<learn-path>" prepare-file "<db-path>" "<section>" "<role>" "<draft-file>" "<candidate-file>" --summary
```

Require success and match the returned `binding.output` and `binding.round_id`
to this saved round. Use its `candidates` for presentation and retain the complete
canonical file for the later choice. A binding failure stops before ranking;
do not follow it by ranking an old file left at that path. File existence alone
does not prove that the new draft succeeded. If ranking fails after publication,
the valid canonical remains; fix the reported problem and retry the same draft
and output. A different existing output still requires a new filename.

To refresh an already validated canonical pool without rebinding, use:

<!-- sonder-command:rank -->
```bash
python3 -I "<learn-path>" rank-file "<db-path>" "<section>" "<role>" "<candidate-file>" --summary
```

Use `--summary` for the normal sentence loop; omit it when inspecting individual
feature matches. It reduces repeated annotation/match output and is a display
view: retain the complete saved envelope for every later rank and choice. Never
feed summary rows back as candidates or strip their omission markers.

Use the returned order and keep all candidates available. Text scores combine
phrase and scaled structure contributions; legacy label weights only break equal
text-score ties. Scores are preference evidence, not correctness or approval.
Inspect the effective backend and required annotation diagnostics; configured
`off` and an actual fallback must be reported accurately. With explicit grammar,
expect `heuristic_template_active:false`. Use runtime setup for diagnostics if
something is missing or inconsistent. Keep routine successful details internal.

Show actual rewritten sentences in the author's options, with brief explanations
of edit level and purpose. Allow Keep and free-text Other. A refresh of this same
pool after another session's feedback is a read, not another author choice.

### D. Interpret the reply and save the decision

- A selected version approves its exact text. Keep leaves the text unchanged.
- Other containing a complete final sentence approves that author's exact wording.
  If it is unclear whether a reply is a final sentence or a steer, clarify once.
- An instruction such as “shorter” or “use #2 but remove the adverb” is steering.
  Generate fresh proposals for the same unresolved sentence and ask again; no
  manuscript edit or feedback yet. Treat the steer as local unless the author
  explicitly confirms broader scope. Local steering alone is not a learning opt-out.

For new Other wording, bind its analysis/grammar to a separate output using the
same round ID, then add that candidate to the original complete pool. Retain all
old alternatives and the original edit scope/Keep reference; do not submit the
one-candidate temporary file as feedback. For an exact-text Other match within
this round, reuse the saved annotations, including unknown/absent fields. Changed
text needs fresh analysis. The binding guide covers temporary Keep-reference handling.

The merged pool now contains **canonical** candidates, including their bound
fingerprints. Save the complete pool to a new candidate filename; do not send it
through `bind-file` or `prepare-file` again, or strip its fingerprints to turn it
back into a draft. To inspect refreshed rankings, validate and rank that file:

<!-- sonder-command:other_rank -->
```bash
python3 -I "<learn-path>" rank-file "<db-path>" "<section>" "<role>" "<candidate-file>" --summary
```

This optional refresh is not another author approval. Keep the complete merged
file for the choice in step E; a rank summary is never its replacement.

Before editing, save `chosen` and truthful `author_owned`: true only for Keep or
the author's own wording, false for an adopted generated candidate. An explicit
request not to learn saves root `learn_from_choice:false` under the same ID.
Authorship false is not an opt-out. Preserve this intent on recovery, and do not
use an excluded choice as profile/global preference evidence.

### E. Apply, record once and log

Check the actual working-file location, paragraph and surrounding boundaries;
the engine validates declared scope, not file placement. Replace the entire
approved span exactly, or verify no change for Keep. Reconcile an already-applied
span without repeating it or leaving merged neighbors duplicated.

<!-- sonder-command:choice -->
```bash
python3 -I "<learn-path>" choice-file "<db-path>" "<section>" "<role>" "<candidate-file>" --receipt-file "<receipt-file>"
```

Choose a stable new `<receipt-file>` path for this round inside
`choice-receipts/`; its parent directory must exist. The engine saves the
original database receipt atomically and refuses conflicting output files.
`receipt_file` identifies the saved file; `receipt_file_reused` reports an exact
reuse. The file's `result` is the original receipt, while a retry's command
response may report `replayed:true`. It is not proof that the manuscript edit
or human-readable log is complete.

Require a successful receipt with the saved round ID and `idempotent:true`.
Ordinary new feedback reports `feedback_applied:true`; an opt-out reports false
and `author_exemplar:false`, while still recording its editorial receipt. Old
receipts retain their historical shape. Replay returns the original result
without another reinforcement. A conflicting payload under an existing ID is an
error: never invent a new ID merely to bypass it. A later separately confirmed
redecision uses a new round/file and the then-current source span.

Capture exit status and raw output before parsing it. A logger/parser failure is
not proof that feedback failed. Inspect the result and retry the same saved
decision/ID if needed. File export can fail after the database committed; the
error says feedback may already be recorded. Preserve a conflicting file and,
if needed, choose a new empty receipt path while keeping the original decision
and ID. Never trust a sidecar alone as evidence that another database learned.
Do not additionally call `record`, `signal` or `store-file`;
`choice-file` already handles this round's learning and author exemplar.

Log the source IDs, locator, exact before/final text, version, fixes, candidate
path, round ID, saved context, learning intent and saved receipt-file path.
Use the host's file-writing/editing tool to maintain this readable log and link
the exported receipt; do not reconstruct receipt JSON in an inline logging
script. The engine does not rewrite the log for you. Merges use
one shared decision: keep the lead ID and mark consumed neighbors `merged_into`
that lead; do not renumber later IDs or learn separately for each member.

Ordinary choices gradually adjust context-local words and structures, with decay
and later correction. Rejected wording stays available. Do not infer permanent
keep/kill lists or a cross-paper rule from a single choice. Advance to the next
unreviewed sentence without an extra “keep going?” between sentences.

### F. Check and confirm the paragraph

Review the complete revised paragraph against its blueprint: main claim,
support, opening/closing, first-use definitions and transition. Propose any
needed fix and obtain a choice before applying it.

<!-- sonder-command:prune -->
```bash
python3 -I "<learn-path>" prune "<db-path>"
```

Prune is upkeep, not an author choice; its successful CLI output is plain `ok`,
not JSON. Explain what changed in the paragraph and ask whether to continue to
the next, pause/save, or revisit wording/profile/blueprint. Do not start the next
paragraph without the author's go-ahead.

## Resuming

Run the installation check first. Read the working text, blueprint, profile,
revision log and saved candidate decisions. Find the first unresolved sentence,
including one inside a partly completed paragraph; a paragraph counter alone
is not enough. Confirm the resume point before presenting the next choice.

After resolving this saved workspace, run step 0's `learning-policy ... ensure-adaptive`
command before ranking or feedback. Preserve an explicit legacy policy and all
existing backend settings; never enable learning by replaying old decisions.

For a saved approved decision with an incomplete edit, receipt or log, reconcile
the entire original span and surrounding boundaries, then retry its complete
file with the same ID, context, source mode, annotations and learning intent.
Do not reconstruct context from today's blueprint or treat a found substring as
proof that a merged span was fully applied. Read edit-scope guidance for merges
and redecisions. Pending files without `chosen` remain pending regardless of rank.

Recover old call context from a saved receipt, command or reliable log. If it is
missing, report the uncertainty instead of guessing. Do not give completed legacy
decisions new IDs and replay them. Never rewrite an approved old payload to add
new annotations, scopes or format fields. An existing receipt conflicting with
requested intent needs reconciliation, not a new ID or removed opt-out. A clearly
logged old intentional skip with a saved ID and no feedback may be finalized as
an editorial-only decision; see the preference guide for that narrow recovery.

Use actual nearest/rank calls for the pending sentence. `reshow` below is only
legacy label history, not the phrase/structure memory query.

## Finish or pause

Summarize reviewed paragraphs and edited/kept sentences, including agreed
structural changes. Show the diff from `original/` to `working/` or a section
summary when large. Point to the edited working file and preserved backup; let
the author choose whether to copy it over the source. Leave the revision state
ready to resume.

## Optional transport and legacy commands

If Sonder MCP tools are available, read the MCP guide and verify `sonder_status`
matches this workspace/database/installation before using it. Prefer a matching
server for bind/nearest/rank/choice/prune; otherwise use CLI. The same saved
envelopes, approval and IDs apply across transports. A timeout/reconnect does
not create a new decision, and the server does not edit the manuscript.

The following commands support explicit imports, legacy callers or requested
configuration. They are not extra steps after ordinary `choice-file` feedback.
For imported Markdown text append `--text-format markdown` to `store-file`.

<!-- sonder-command:store -->
```bash
python3 -I "<learn-path>" store-file "<db-path>" author "<sentence-file>"
```

<!-- sonder-command:record -->
```bash
python3 -I "<learn-path>" record "<db-path>" "<section>" "<role>" "<version>"
```

<!-- sonder-command:bias -->
```bash
python3 -I "<learn-path>" bias "<db-path>" "<section>" "<role>" Light Medium Bold "Keep original"
```

<!-- sonder-command:resume -->
```bash
python3 -I "<learn-path>" reshow "<db-path>"
```

Configure a backend only when intended; this example is not permission to reset
an existing `off` or model configuration. Read runtime setup before a change.

<!-- sonder-command:syntax -->
```bash
python3 -I "<learn-path>" syntax-backend "<db-path>" heuristic
```
