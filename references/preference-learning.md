# Learning the author's preferences (soft, IME-style)

The current Skill initializes `adaptive-v3` when this paper has no explicit
policy. It separately learns accepted expression profiles and comparable choice
differences, with a bounded decaying reference to earlier data. A single accepted
author sentence still informs the profile. See [adaptive learning](adaptive-learning.md)
for scoring versions 3/4/5 and migration/diagnostic boundaries. Every saved policy,
including `legacy` and `adaptive-v1`, remains respected; direct old API calls stay legacy until configured.
The phrase/syntax counters and version-2 formulas described below remain the
compatibility observation layer, not the complete adaptive rank formula.

For implementation changes, see [the learning-engine maintenance contract](learning-engine-contract.md)
for migration, CLI, installation and regression requirements.

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

The IME does the opposite. It never bans a word; it nudges a choice upward
*in that context*, and repeated choices reinforce the nudge. A single "don't"
is not a rule — it's one downvote.

## The three ideas, ported

1. **Record the accepted text, not the display label.** Every candidate round
   supplies the actual sentence text. The engine updates short word/phrase
   features under `(section::role, feature)` and keeps the accepted full
   sentence as an exemplar when it is the author's own wording. The label
   Light/Medium/Bold is only a legacy auxiliary signal.

2. **Start with a small nudge, and keep it within the context.** Each accepted
   candidate immediately adds a small weight to its extracted features;
   repeated choices reinforce those expressions. The same phrase in a
   *different* role/section is a separate entry and doesn't inherit the bump.

3. **Nudge, never delete.** A preferred option rises; a rejected option dips.
   Neither is removed. This is what keeps the author's voice from being
   flattened — the full range of options stays on the table, just reordered.

## Explicit exclusion of one choice

A clear “do not learn this choice” overrides ordinary feedback. Save the JSON
boolean `learn_from_choice:false` alongside the saved choice and round ID,
keep `author_owned` accurate, then finalize with the usual choice-file/MCP
operation. Its editorial receipt has `feedback_applied:false` and
`author_exemplar:false`; it does not extract features, reward or reject any
option, or store a voice exemplar. Do not treat that choice as evidence for a
style-profile or global preference either. Keep its exception in the revision
log so a new session can preserve it.

Omission or true preserves normal learning; ordinary local steering is not an
automatic opt-out. Preserve the same intent/file/ID on retries. Opposite intent
under an existing ID conflicts, and this control cannot undo prior learning.
Old normal receipts may lack feedback_applied and replay unchanged. A clearly
logged older intentional skip without engine feedback can be finalized as an
editorial-only decision using its saved ID and false flag; do not invent a new
ID to replay a completed legacy decision.

## Complete supported phrase coverage

Every distinct supported word/CJK bigram/trigram from the masked prose is used
for ranking and new feedback. There is no sorted-prefix quota: a long Chinese
span must not crowd out its Latin terms or higher-sorting Chinese fragments.
Rank rows, including the summary view, report `phrase_feature_count`; ordinary
choice receipts retain their `features` count. Runtime capability
`complete_phrase_features` identifies this behavior.

The feature count grows with supplied prose length. Literal masking, feature
keys, context, decay and weight bounds are unchanged; this is not linguistic
word segmentation or a semantic correctness check. Existing weights remain
usable. Scores for previously truncated long text can change because all query
features now count in the mean. Old receipts keep their original counts and
replay without backfilling omitted features; only a new approved choice learns
new evidence. Short text that stayed within the old quota retains its scores.

## Ordinary text formatting and phrase continuity

With `formatting_text_continuity`, balanced standard single-argument text
wrappers preserve adjacent visible wording. For example, `不支持` and
`\emph{不}支持` have the same phrase features; likewise `precondition` and
`pre\textbf{condition}`. A formatting-only choice must reinforce the accepted
shared wording rather than downvote fragments split by markup.

Supported wrappers are `emph`, `textbf`, `textit`, `textmd`, `textnormal`,
`textrm`, `textsf`, `texttt`, `textup`, `textsl`, `textsc`, and `underline`,
with balanced brace arguments. They can nest. Macro names are case-sensitive;
starred/unknown commands, malformed groups, and groups with TeX comments retain
conservative handling. This bounded view does not interpret macro redefinitions
or render arbitrary TeX. Escaped percent signs are ordinary escaped characters.

The raw candidate, annotation offsets/hashes, edit scope and complete author
example stay byte-for-byte as supplied. One shared scanner supplies two views:
the phrase view still masks formula/citation/reference regions with boundaries;
the base-syntax view retains those literal regions unchanged and removes only
supported text wrappers. Heuristic and optional Stanza extraction use that
base-syntax view, so extra formatting characters cannot move the same visible
connective to a different position bucket or become fake parser tokens. The
host's evidence-bound analysis/grammar remain tied to the original raw text.

Previously split query features may now join and formatted base-syntax features
can change, but stored weights and historical receipts are not rewritten or
replayed to backfill missing evidence. This does not equate arbitrary different
host annotations or render arbitrary TeX.

## Explicit Markdown interpretation

New Markdown rounds carry root `text_format:"markdown"` in the saved candidate
envelope. Runtime capability `markdown_text_format` identifies support. Missing
format preserves the entire existing legacy path, including TeX literal guards;
there is no `plain` or `tex` enum to add. Do not infer Markdown from `a*b*c`,
which can be ordinary technical notation outside Markdown.

In explicit Markdown mode, supported inline `*`/`_` emphasis and strong markers
are interpreted with delimiter-run/flanking rules rather than globally removed.
Escaped markers and stars inside protected code, math and markup destinations
must not be treated as emphasis. Phrase and base-syntax extraction share the
mode; raw candidate/host annotations and author examples remain unchanged.
This is an extraction guard, not a full Markdown renderer or document parser.

The format flag is part of a new decision's identity and its normal or opt-out
receipt. Adding, removing or changing it on an approved round conflicts; preserve
the original payload on retry. Old absent-format receipts replay without a new
field or backfilled learning. Keep the original whole envelope as input: rank
candidate rows alone do not carry source-format, edit-scope or context metadata.
New author exemplars also save the source format for retrieval. `nearest-file`
and separate `store-file` imports take trailing `--text-format markdown`; MCP
`sonder_nearest` takes optional `text_format:"markdown"`. Omission preserves
legacy interpretation. Retrieval compares the supported surface view, including
within-word emphasis, and returns exact stored author text. It retains math,
citations and scientific literals rather than using the phrase-masked view.

Schema 5 adds nullable exemplar `text_format` and independent `ngram_version`.
Old rows stay legacy/NULL: neither asterisks nor past event history can reliably
recover their source mode. Migration rebuilds only the derived cache (index
version 1), preserving author text, IDs, dates, rewards, weights and receipts.
A new confirmed choice of the same exact text refreshes its format/cache and
normal reward history without creating a duplicate. Replaying an old receipt
or opting out never overwrites a newer mode or reinforces the exemplar. Keep
a backup; old engines cannot downgrade upgraded databases.

## Consistent reads during feedback

With `consistent_rank_snapshot`, a single rank call uses one committed SQLite
snapshot and one decay instant for all label, phrase and structure weights.
Identical text/annotations cannot acquire different text scores merely because
feedback committed between candidates. New feedback may arrive after that
snapshot; a later rank call sees it without recording the choice again.

All candidate feature/model extraction runs before the read transaction. The
read lock covers scoring only and is released on success or failure. This does
not change the database journal mode; normal SQLite writer waiting can still
occur during scoring. Model configuration/sidecar changes remain separate from
the database snapshot and should occur between revision calls.

## Cross-paper persistence

The installation's `memory/style-profile.md` stores only explicitly confirmed
standing preferences. When the author clearly says a preference applies to
future papers, record the exact preference, applicable author/language/scope,
and confirmation source/date there; preserve other confirmed entries. Mirror
the applicable entry into the current paper's profile. On a new paper, read
this file before drafting and seed only applicable confirmed entries. Examples
and placeholders in a bootstrap template, including older templates, are not
confirmation. If scope or author is unclear, resolve that before carrying an
entry forward. Do not infer a standing preference from frequent choices.

A later explicit standing correction updates the matching entry. A one-sentence
exception stays in the revision log. A choice with `learn_from_choice:false`
must not update either style profile as evidence. Confirming a standing
preference alone does not select a pending sentence or create a feedback event.
Verify the saved profile before reporting that the preference will persist;
if saving fails, say it has not been saved.

Each paper retains its own `preference.db`: phrase/structure weights and author
exemplars survive a resume of that paper, but are not transferred to another
paper. The profile is read by the writing assistant; `rank-file` does not parse
its prose or convert it to numeric weights.

The legacy `global DB OPTION [WEIGHT]` API is a different mechanism. It writes
an option label under `__GLOBAL__` in that one database. In `rank-file` its
decayed weight only breaks equal text-score ties for matching labels; legacy
`bias` also includes it. It neither writes the text profile nor transfers
weights to a fresh database. Passing a terminology instruction as OPTION does
not teach its meaning. Keep this API for compatible label-based callers, not
for saving standing prose preferences.

## Three memory layers and one compatibility layer

Partial rhetorical labels remain conservative: unknown attributes cannot raise
scores on otherwise fixed evidence. Stable known features still learn, while
uncertain action boundaries and missing features contribute no invented reward.
The scoring capacity is retained so withholding a weak feature does not boost
the mean. This is not permission to relabel or remove evidence after ranking.

`library/learn.py` keeps three primary stores, plus the old label store:

- **`phrase_prefs`** — context-scoped word and phrase weights. `rank-file`
  scores real candidate texts from these features, so accepted wording—not a
  display label—leads the next proposal.
- **`exemplars`** — sentence memory. The author's own kept or self-written
  sentences are canonical examples of their voice. `nearest` retrieves the
  most similar one and anchors the register of new rewrites.
- **`prefs`** — legacy edit-intensity choices. Old `record`/`bias` callers
  remain supported, but new sentence rounds must use `choice-file`; labels are
  used only when candidate text scores are equal. Their accumulated weight
  never adds points to the phrase/structure score. Local/global decay still
  applies to these ties; equal label weights preserve the input order.
- **`syntax_prefs`** — optional context-scoped structure weights. A local
  parser or the conservative zero-dependency fallback converts a sentence into
  versioned POS/dependency/connective/slot features and a small academic
  argument layer. The academic layer recognizes explicit moves such as method,
  result, comparison, hedge and limitation, then learns their positions and
  order. These features are a soft secondary signal for `rank-file`; they never
  become a global grammar rule.

Legacy scoring version 2 gives base syntax and surface rhetorical analysis separate
means, alongside the existing independent grammar-family and topology scores.
A candidate's additional untrained parser keys cannot dilute an otherwise equal
rhetorical signal. Rank diagnostics expose `analysis_score`; `base_syntax_score`
now describes base syntax alone. The sum remains scaled by the existing syntax
factor and added to phrase_score. Input feature keys and learning weights do not
change, but annotated candidates can receive different scores/order than under
the older combined mean. This does not resolve semantic label ambiguity; inspect
actual matched features and preserve the author's choice.

The structure backend is selected independently with
`syntax-backend DB heuristic|stanza|off [MODEL_DIR] [MODEL_VERSION]`. `heuristic` is safe without extra
packages and records clause boundaries, punctuation, connectives, replaceable
slots and auditable academic cues/move order. `stanza` is allowed only when the
author points it at an existing local `zh-hans` `default_fast` model directory;
the revision flow never downloads a model. Its pipeline runs tokenize, POS,
lemma and dependency parsing, in that order; the dependency parser requires
lemma annotations even though features do not retain lemma values. Provision
those models and their declared pretrain dependencies together. Parser memory
uses a content fingerprint of the selected files, their roles and the Stanza
version. The optional model-version tag is a human-readable diagnostic label;
retagging or moving identical files does not discard learned parser weights.
Changing model content automatically isolates its parser memory. The academic layer is a domain adapter on top of the
parser, not a claim that a general UD model was trained on academic prose.
Missing packages, missing models and parse failures fall back to the heuristic,
so phrase and exemplar memory remain usable. `off` is an explicit setting.

Ranking reports the requested and effective backend plus a fallback reason.
When one candidate parses and another falls back, their missing base features
are incomparable: the engine suppresses base syntax rejection for that round,
while still reinforcing the chosen sentence. Phrase/exemplar feedback and the
independent host analysis/grammar gates remain active. If all candidates use the
same fallback, normal base learning remains possible. Choice receipts retain
each candidate's fallback reason and `base_syntax_negative_feedback`; retries
return that saved evidence rather than describing the current parser state.
The same base-feedback protection applies when model fingerprints differ within
a round. Results report the fingerprint and label attached to the parser actually
used. New receipts also list per-candidate `syntax_model_fingerprints`. A cached
parser is reloaded when its configured tag, provider version, resource manifest
or model-file state changes; unchanged cache hits do not reread model contents.
Resource drift during resolution/hashing/loading causes an explained fallback.
Perform model maintenance between revision calls.

Earlier tag-only Stanza rows are preserved but cannot be attributed to a verified
model automatically, so new parser features use `stanza-id1-<fingerprint>`.
Old receipts replay exactly. Phrase/exemplar memory, the heuristic and the
independent writing-model analysis/grammar namespaces remain available unchanged.
For optional packages in a virtual environment, use its absolute `bin/python`
path for both runtime checking and all engine commands, without resolving the
symlink to the base interpreter.

Structure keys now use `syntax:v3`. Earlier `v2` rows are retained but not used
under the changed extractor, so known false cues cannot silently keep affecting
new ranks. All complete author exemplars and phrase history are retained.
The default extractor filters known nominal compounds such as 可见光, 显示器
and 通过率 across cue and slot paths. This guard is not general word segmentation:
unlisted constructions can still be missed or misclassified. Do not report
keyword-based features as full grammar understanding.

## The sentence memory (CBR-style exemplars)

Before ranking new candidates, the writing host also provides optional
[grounded structure analysis](structure-analysis.md): exact text spans, their
argument roles, polarity and degree of assertion, tied to the whole sentence's
hash. This can recognize an unfamiliar wording without extending a keyword
list. It is not an extra model call or personal model training. Model meanings
remain fallible even when spans validate.

These features use the separate `syntax:analysis-v2:writing-model:` namespace.
Consecutive identical, fully known action signatures form one learning unit,
so dividing the same action into more evidence spans does not weaken its score.
Unknowns or changes in polarity/certainty preserve boundaries. New input v2
units/parts and legacy input v1 spans both feed these coarse analysis-v2
features. Older analysis-v1 feature rows remain stored but are not reinterpreted.
Explicit analysis replaces lexical academic guesses for that sentence; an
empty/unknown analysis is allowed. Missing annotations do not become rejection
evidence: negative annotation updates require a fully analyzed candidate round.
An author's Other sentence is analyzed afresh. `off` disables these signals too.
The rest of the soft weights, context separation, decay and exact author memory
remain the same. Inspect rank diagnostics for the evidence actually matched.

This is the part that "remembers sentences." It is case-based reasoning, not a
neural net.

- When the author picks **Keep original**, or types their own wording in
  "Other" and it becomes the final text, `choice-file` stores that exact sentence
  as an `author` exemplar in `preference.db`. Do not call `store` again for the
  same decision; use it only for separate, explicit exemplar imports.
- The same author-owned sentence also reinforces its structure features. With
  a local parser these include a coarse POS sequence, dependency shape,
  connective position, academic move order and a slot template such as `通过
  <N> 测量 <N>`. A zero-dependency fallback records only the structure and
  academic cues it can identify reliably.
- Before proposing options for a new sentence, `nearest` finds the most similar
  stored author sentence (by character 3-gram TF-IDF cosine — pure Python, no
  training, no external deps).
- Use compatible examples as soft references for **register**. They are approved
  sentences in their own contexts, not evidence that their facts, certainty or
  constructions transfer to the current claim. Check study design, evidence
  strength, conditions, negation/quantifier scope and whose verification is meant
  before borrowing wording. A controlled causal result and an observational
  limitation can share many words while requiring different constructions.
  A lower-ranked compatible example may be more useful than the closest one;
  use the paper's profile and current context when none fits. Similarity or a
  differing example alone does not establish a style preference. Learn reusable
  phrasing and rhythm while preserving what the current source actually says.
- The similarity matcher is intentionally replaceable: swap the cosine for a
  `sentence-transformers` model and the calls stay the same. The categorical
  jump to a neural network only adds *generalization beyond exact overlap* — it
  costs data, and it is not needed to remember what the author already wrote.

## The context is the sentence's job, not its topic

The context key is the sentence's primary job in this paragraph, together with
its manuscript section. It controls which preference rows are read and updated;
it is a host judgment, not a label inferred by the engine from the prose.

For new writing rounds, use the established section names abstract / intro /
methods / results / discussion / conclusion and these role meanings:

- **topic**: states the paragraph's main proposition, question or focus. Being
  first is not sufficient: an opening procedure step can still be detail.
- **support**: supplies evidence, an example or a reason for the paragraph's
  proposition. A constraint used as a reason for an inference can be support.
- **detail**: specifies a procedure, setup, definition, measurement condition or
  other descriptive particulars. A definite operational failure or coverage
  limit in a methods description can be detail; negative wording alone is not a hedge.
- **hedge**: primarily qualifies a claim's confidence, interpretation or scope
  of applicability. The qualification itself can be certain. A reported device
  failure is not automatically hedge merely because it limits an operation.
- **transition**: guides the reader between parts of the argument or explains
  what comes next. An experimental operation happening next is not, by itself,
  a rhetorical transition.
- **conclusion**: draws or synthesizes an inference from the preceding discussion.
  Being last in a paragraph is not sufficient.

Read the full paragraph and its approved blueprint before selecting the key.
The same sentence can legitimately have different jobs in different paragraphs:
a time-window limit can describe the method, constrain a conclusion, or supply
a reason for another claim. The intended argumentative job decides; keywords,
sentence position, polarity and the candidate's analysis.move do not decide it.
In particular, analysis labels such as method or limitation are not alternative
spellings of the root context role. Keep these two levels separate.

Before the first binding/ranking, record a short **context basis** in the revision
log: the paragraph's purpose, the target's primary job, and any plausible secondary
job or uncertainty. Compare earlier decisions with the same purpose in this paper
and use their established convention when it fits. If several roles remain
reasonable, choose the best-supported primary role and retain that uncertainty;
do not duplicate one choice into several contexts or invent a synonym to avoid
the decision. Do not choose a context because it gives a preferred ranking.
The author decides the paragraph's meaning and purpose, not internal tag names.

Then save section/role in learning_context and keep them fixed for that round.
The rationale is ordinary host documentation, not a new input field or an engine
certification. On resume, a saved approved context wins over a fresh classification;
never rename historical roles, move their weights or rebind a committed decision
to make it fit this guide. Older/custom context strings remain valid historical
identifiers. When continuing a paper with an established custom identifier for
the same job, keep that identifier; this glossary is not a namespace migration.
A genuinely different job in a later independent round may use a different
context, with its own rationale.

Storage keys are opaque identifiers. Schema 9 keeps ordinary keys such as
`methods::detail` unchanged, but encodes pairs that would collide under simple
concatenation. Keep the original section/role pair in `learning_context`; never
split a `reshow` key on `::` to reconstruct it. When an old ambiguous key has
history, ranking and new learning fail explicitly while preserving the rows.
Old receipts can still replay, and explicitly non-learning editorial choices
can still be recorded. There is no automatic assignment of mixed old history;
see [upgrade guidance](runtime-setup.md) before resolving it.

Learned phrase and structure preferences remain isolated across these contexts. A choice learned as
methods::detail does not automatically affect methods::hedge or an abstract.
If apparently familiar wording has no learned matches, inspect the saved context
and its basis before diagnosing lost memory. Context selection can still be
fallible; this guide makes the judgment explicit and consistent where supported,
not a rigid sentence classifier or a guarantee of agreement between writers.

Author-exemplar retrieval has a different scope: `nearest` searches this paper's
exemplar table without filtering by section/role. An example learned in methods
can therefore be returned for a discussion sentence even when every ranking
score there is zero. Judge its applicability using the current paragraph and
evidence; retrieval alone does not establish a preference in that context.
Both forms of memory remain in the paper's database. Only explicitly confirmed
standing text preferences carry over to another paper.

## How to fold it back in

The writing loop also attaches [grammatical frames](grammar-frames.md) to real
candidates. Their grounded event form/voice, participant slots and subordinate
relations distinguish constructions sharing the same rhetorical action.
Independent `syntax:grammar-v1:writing-model:` weights follow the same decay
and author-confirmed transaction. Expression/link scores are averaged within
their families and added to base syntax, so a learned relative relation remains
visible beside a shared expression. Unknown/missing input is not rejection;
these are soft preferences, not grammar rules or a full dependency parser.
Independent grounded relations can also learn their surface order through
the `link_pair_order` family, even when both propositions have the same
rhetorical role. Nested/overlapping or unknown relation pairs abstain. The
family starts with zero weight in old stores and learns only from new approved
choices; it does not reconstruct historical feedback or distinguish arbitrary
content with identical grammatical signatures.
Input v5 can distinguish the recipient from transferred content and learn their
order. A recipient is grounded in the exact sentence, not guessed from a cue.
Mixed older/v5 annotations with recipients suppress argument-shape rejection
in both directions while retaining selected reinforcement and independent
feedback. New ordinary receipts expose `argument_shape_negative_feedback`;
historical decisions and their receipts are unchanged.
Input v6 requires expression slots_complete. False retains known form/voice
positive feedback but excludes complete argument-shape keys and reserves zero
scoring evidence; the overall partial grammar also disables grammar rejection.
True is a producer judgment, not a semantic accuracy guarantee. Legacy rows and
receipts stay unchanged; unreported missing roles remain a limitation.

`library/learn.py` exposes the computation. When you're about to propose a
sentence's rewrite options, put the actual candidates in a JSON file and call
`rank-file`. Lead with the higher-scored candidate, but keep every option
available. After the author chooses, call `choice-file` once so accepted
phrase and structure features rise once per round. Only features absent from
the chosen text and present in unchosen candidates receive one downward nudge;
shared features are positive evidence, and duplicate alternatives cannot
multiply rejection. The
phrase signal is primary; the structure signal is secondary and the whole
mechanism is not a ban — an option can compete back up if the author later
prefers it. `record` and `signal` remain for old label-only callers and tests.
- Only an explicit preference for future papers is saved in the installation's
  text profile, following [cross-paper persistence](#cross-paper-persistence).
  Ordinary candidate feedback remains local to this paper and context.

Trust the author at each step: when you apply a nudged preference, let them see
it working. If they say "not here," preserve that local steering and let the
eventual approved choice carry its normal feedback, unless explicitly excluded.
Do not add a separate legacy `signal` call to the same candidate decision.

## Feedback time under concurrent use

New feedback uses one time captured after obtaining the database writer lock,
including an author's exemplar and the decision receipt. A request that starts
earlier but finishes parsing later must not reset newer weights to its old
start time. Extraction stays outside the write lock; a receipt retry does not
refresh any time or repeat learning. This uses local wall time, not a claim to
resolve clock skew between machines. Existing receipts and timestamps remain
historical evidence.

## Retrying a decision

Each new Skill decision carries a persisted `round_id`. The same id and
normalized decision return the original receipt with `replayed:true`; they do
not refresh timestamps or add weight/rewards. A new author decision gets a new
id and can reinforce the same text again. Reusing an id with changed content,
choice, ownership or context is an error. Candidate display order does not
change decision identity.

Old callers without `round_id` retain their original per-call behavior and
return `idempotent:false`. Do not retroactively submit already-completed legacy
choices with new ids. Receipts are preserved by pruning so an old retry cannot
revive forgotten preferences. The saved decision file and revision log are
needed to reconcile the working manuscript after an interrupted session.

## Learn to forget

A preference is not a tax on your memory. The store applies **exponential decay**
when it reads and before writing feedback — a weight halves every `HALF_LIFE_DAYS` (default 30) since the
last time the author exercised it. New feedback is added to that decayed value, within the managed weight range; it does not revive the old raw weight. So a preference the author stopped
reinforcing quietly fades, instead of accumulating into a pile of stale rules
that no longer match what they want. This is the same reason Rime's user
dictionary keeps a `d` (decay) and `t` (timestamp) alongside the count.

## Prune, don't accumulate

Stale preferences are as bad as wrong ones. At each paragraph pause, run
`prune` — drop compatibility counters and exemplars that have gone stale. It
does not delete adaptive model/snapshot rows; those decay on read/update and do
not use the old integer threshold. A compatibility entry is removed only when it is
both **old** (no reinforcement for `stale_days`, default 30) *and* **light**
(its age-decayed weight has fallen below the floor). Pick count is deliberately
not a criterion: a fresh one-off confirmation survives every paragraph pause,
and a heavily-used preference the author has since abandoned still gets removed
once it has decayed. A downweighted-but-recent option stays, so a rejection
never becomes a deletion. The IME does the same by rebalancing its
managed weight range; a tight, clean preference set is more useful than an
accumulating archive.

Example memory follows the same forgetting principle: old exemplars are removed when their decayed reward count drops below one. At capacity, the just-accepted sentence is retained and the least useful older history is evicted by decayed reinforcement, then age. Repeated confirmation does not make an exemplar immortal.

Exemplar reinforcement refreshes its timestamp and improves retention. Its
reward count does not directly boost retrieval rank: `nearest` ranks by
similarity multiplied by age decay. This differs from the legacy `prefs` layer, where
repeated choices increase a display-label prior.

An old exact-match exemplar can therefore remain the nearest sentence after
later choices change the phrase/structure preferences. A non-author-owned
proposal choice updates those preferences without becoming an author exemplar.
This is not a failed feedback update: inspect the real candidate ranking and
the saved choice receipt. The nearest list supplies historical wording for
contextual judgment, not a leaderboard of the author's current preferences.

## Keep it soft even when it's obvious

Re-read a few kept-original or self-written sentences together with the recent
confirmed revisions in the relevant context. Earlier originals are evidence of
the author's voice at that time; they are not an immutable template that overrules
later choices. A uniform drift is a reason to check current intent and meaning,
not to discard newly learned preferences automatically. Preserve a clean original
as a real option, follow the returned candidate order, and apply the author's
actual choice even when it is not first. One local choice creates neither a ban
nor a standing rule; later feedback can gently reshape the preferences again.


New rounds use analysis v2 units/parts. Whole author sentences remain the memory
examples; their fragments are not independent assertions. Alongside existing
coarse rhetorical features, separate pair-trace weights learn surface organization
such as a result interrupted by a method. They have their own mean inside syntax
scoring, preserving old scores until new keys learn. Unknown attributes retain
zero-evidence capacity. Rejecting topology requires complete v2 on every candidate;
missing legacy representation is not dislike. These remain soft, context-scoped,
decaying preferences. They do not learn causal truth or guarantee correct labels.


### Scientific literals and phrase learning

Runtime capability `phrase_literal_guard` keeps pure numbers (including decimal
and scientific notation), complete explicit TeX math, citation/reference/label
arguments and link targets out of phrase features. TeX control names are also
excluded; ordinary formatting arguments remain prose. Masked regions separate
fragments, so deleting a formula cannot invent a Chinese phrase across it.
Technical alphanumeric terms such as H2O, p53 and ResNet50 still participate.

This changes feature extraction for new ranking and feedback only. Original
sentences, annotation offsets/hashes, whole-sentence exemplars and historical
receipts stay exact; structure extraction still sees the original text. Old
literal rows are retained, but cannot match when their only occurrence is in
a guarded literal. Old aggregate word keys do not record occurrence provenance:
a same-spelled ordinary word can still match its historical weight. No automatic
retroactive cleanup or backfill is claimed.

The guard recognizes balanced dollar/paren/bracket math, common math environments
and known citation/reference macros with balanced arguments. It is not a full
TeX renderer or semantic fact detector. Ordinary prose after malformed or
unclosed markup is retained; unmarked variables, plain author-year citations
and numbers spelled in words are outside this syntactic guard. The writing
workflow must continue preserving all claims, values, equations and references.
