# Learning-engine maintenance contract

## 1. Scope and triggers

Use this contract when changing `library/learn.py`, its CLI, installation, or
the documented revision commands. The default engine uses only the Python
standard library. All database tests use disposable stores; author databases
are not test fixtures.

Current schema is **9**; context-key format is **2**. `adaptive-v3` uses scoring version **5** and optional
candidate `relation_coverage` version **1**. Existing absent-coverage payloads keep
their exact canonical shape and decision hash. See [relation coverage](relation-learning.md)
and [adaptive learning](adaptive-learning.md) for author-workflow and model rules.

### Relative relation learning (current integration)

- `relation_features.py` owns coverage validation, draft-to-canonical frame
  remapping, evidence fingerprints, graph certainty and fixed group capacity.
  `relative_learning.py` owns finite-group nonnegative readout and its actual
  score gradient. Both are required sibling modules; runtime failure is explicit.
- Canonical coverage contains exactly version, text/grammar hashes, expressions
  and links. Draft coverage has only version/expressions/links with draft indexes.
  Expression inventories distinguish complete empty from missing/uncertain;
  event/non_event/unknown link targets are checked against physical anchors.
  Identical text must carry identical analysis, grammar and coverage. Partial
  known observations may contribute, but new comparisons require complete known
  inventories across distinct compared texts. Backend off blocks the layer.
- The canonical candidate field enters the existing decision hash under every
  policy, including opt-out. Stale fingerprints or different covered decisions
  cannot reuse a receipt ID. Missing old fields remain absent; no backfill.
- Schema 8 creates `adaptive_relative` transactionally and preserves all existing
  rows, receipts and times. The new layer starts empty; supported real product
  stores have no research facet coordinates to convert. Old-version refusal is
  mandatory. Policy transitions retain learned state and reset only transient
  correction traces; repeated selection and ensure do not reset them.
- New/unset workspaces select v3; an explicitly saved legacy/v1/v2 policy remains.
  Candidate extraction prepares both base and relation-aware vectors, then the
  active policy is rechecked under the serialized feedback lock. No parsing,
  schema upgrade or policy transition is an author choice. All new states,
  compatibility counters, examples and receipts commit or roll back together.
- CLI and existing MCP calls delegate to the same bind/rank/choice functions.
- Relation link `head_kind` must be a string enum before any set-membership
  lookup. Arrays/objects are input errors, not internal `TypeError`s. Shared
  validation reports `candidates[i].relation_coverage: links[j].head_kind`;
  bind, prepare, rank and choice fail before publication/store access, and a
  corrected pending input remains usable in the same MCP process.
  Full rank includes canonical coverage; summary removes it and remains output-only.
  V3 adds relation score/count/coverage status. Numeric ranks express preferences,
  not meaning, correctness or a template requirement.

## 2. Signatures

Existing CLI forms remain supported:

```text
engine-info
bind-file DRAFT_JSON OUTPUT_JSON
prepare-file DB SECTION ROLE DRAFT_JSON OUTPUT_JSON [--summary]
record DB SECTION ROLE OPTION [WEIGHT=1000]
signal DB SECTION ROLE OPTION [WEIGHT=-1000]
global DB OPTION [WEIGHT=100000]
bias DB SECTION ROLE [OPTIONS...]
store DB KIND TEXT [--text-format markdown]
nearest DB TEXT [N=5] [--text-format markdown]
list DB [KIND]
prune DB [STALE_DAYS=30] [MIN_WEIGHT=1000]
reshow DB
backend DB [ngram|sklearn|embed]
syntax-backend DB [heuristic|stanza|off] [MODEL_DIR] [MODEL_VERSION]
learning-policy DB [legacy|adaptive-v1|adaptive-v2|adaptive-v3|ensure-adaptive]
rank-file DB SECTION ROLE CANDIDATE_JSON [--summary]
choice-file DB SECTION ROLE CANDIDATE_JSON [--receipt-file FILE]
```

Literal UTF-8 text input is available without shell interpolation:

```text
store-file DB KIND FILE [--text-format markdown]
nearest-file DB FILE [N=5] [--text-format markdown]
python3 scripts/install.py [--target DIRECTORY]
python3 scripts/check_runtime.py
```

Database version 6 uses `PRAGMA user_version`, unique `(kind,text)` exemplars,
the same padded character 3-gram definition for cached features and queries,
context-scoped `phrase_prefs` and `syntax_prefs` tables, and versioned syntax
feature names plus durable `choice_events` receipts. Version 1 and unversioned stores
migrate without dropping old `prefs`, `seen`, or `exemplars` data.
Upgrading version 3 adds the event table without inventing receipts for past choices:

```sql
CREATE TABLE choice_events (
  round_id TEXT PRIMARY KEY NOT NULL,
  payload_hash TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created TEXT NOT NULL
);
```

New Skill candidate files include `round_id`, a non-empty string of at most
200 characters without surrounding whitespace. The CLI keeps it optional for
old callers. Choice output adds `round_id` (string or null), `idempotent`
(boolean) and `replayed` (boolean) alongside the existing fields.

`learn_from_choice` is an optional root JSON boolean, default true. False
requires a saved round_id when finalizing. New normal receipts additionally
report `feedback_applied:true`; a false decision returns chosen/label, round_id,
idempotent/replayed, `feedback_applied:false` and `author_exemplar:false` without
model diagnostics because no feature/model extraction ran. It creates an
editorial choice_events receipt only, not preference reinforcement/rejection or
an exemplar. Normal DB creation/schema migration can still occur.

## 3. Contracts

- **Runtime capability:** `engine-info` takes no extra arguments and performs no
  store/config access or optional model loading. It returns `engine:sonder`,
  `info_version:1`, `database_schema:9`, `context_key_version:2`, `exemplar_index_version:1`,
  `receipt_export_version:1`, capability `choice_receipt_export`,
  capability `exemplar_format`, supported analysis/grammar input versions,
  syntax/analysis/grammar feature versions, and capability names for literal text
  files, phrase memory, author exemplars, choice receipts, `adjunct_position`,
  `annotation_binding`, `syntax_fallback_diagnostics` and `syntax_model_identity`.
  Current callers additionally require `choice_id_guard` for the optional MCP
  choice entry point.
  Adaptive support additionally requires `adaptive_preference_learning` and `local_opposition_learning`,
  `supported_scoring_versions:[2,3,4,5]`, `learning_policies:["legacy","adaptive-v1","adaptive-v2","adaptive-v3"]`.
  The code-default `scoring_version` remains 2; a configured adaptive ranking
  reports version 3 (v1) or 4 (v2). The sibling adaptive module is part of the installation,
  and missing/incomplete code must fail the no-database capability command.
  `grounded_grammar_precedence` additionally requires explicit grammar to
  suppress raw heuristic templates in rank and new feedback, including unknown
  or empty frames. New output reports `heuristic_template_active`; it must
  describe the effective backend (including heuristic-fallback), not merely the
  requested parser. Existing saved receipts are never backfilled.
  The latter requires `feature_versions.syntax_model_identity=v1`.
  Share version
  constants with actual validators/diagnostics, not a separate stale report.
  The installed checker invokes its own `library/learn.py` in an isolated Python
  process and validates against the Skill's independent requirements. Missing
  files, nonzero status, timeout, malformed JSON, wrong field types or missing
  capabilities fail clearly. Boolean values are not protocol version integers.
  New/resumed revisions run this guard before workspace writes or DB use. It
  reports code support, not workspace configuration, model accuracy or package
  availability. The rank diagnostics still govern whether structure is enabled;
  never bypass a mismatch by dropping annotations or changing an author's off
  setting. Existing stores and receipt payloads are unchanged.

- **Upgrade:** schema 6 added `adaptive_settings`, `adaptive_weights`,
  `adaptive_legacy` and `adaptive_legacy_mass` in the same migration transaction.
  Schema 7 adds `adaptive_opposition` in that schema transaction, initially empty;
  no historical feedback is replayed or interpreted as a correction trace.
  The new model is empty until configured/newly confirmed; original rows and
  receipts are not reinterpreted or backfilled. Schema 5 added nullable `exemplars.text_format` (NULL/markdown)
  and integer `ngram_version`. An old row remains NULL, never inferred from
  text or receipts. Only unversioned stores merge duplicate kind/exact-text
  rows, keeping the smallest ID, sum of rewards and chronological date bounds.
  Versioned upgrades preserve original author/history fields exactly. Rebuild
  stale derived caches transactionally from the supported surface view, not the
  phrase-masked view. Index version 1 uses the same cached counts for DF and
  document vectors, and the same surface/count algorithm for queries. Future
  versions fail instead of being downgraded. Any failure rolls back schema,
  caches and version; reopening an up-to-date store makes no derived writes.
  `store`/`nearest` accept keyword-only `text_format=None`; their CLI/file forms
  accept trailing `--text-format markdown`. All matchers compare surfaces and
  return exact raw text; explicit Markdown rows also report `text_format`.
  New author confirmations update the existing exact-text row's mode/cache
  together with ordinary rewards/dates. Old replay and opt-out never refresh
  that mode. Existing hashes, receipts and feedback scoring remain unchanged.
  Retain upgrade backups; an old engine is not a database downgrade tool.
- **Binding:** `bind-file` prepares exact annotations without any DB/model use.
  Its strict draft requires a stable round_id and both analysis/grammar objects
  per candidate, with explicit empty arrays for abstention. It fills only
  missing version/source/hash/coordinates, then reuses the existing validators.
  A supplied stale hash/offset fails. Frame/analysis spans bind inside the whole
  candidate; slots bind only inside their frame. Multiple literal matches,
  including overlapping ones, require 1-based occurrence within that scope or
  an explicit absolute start; the two selectors cannot be combined. Output
  preserves literal text and existing decision identity. Root metadata is an
  explicit object outside feedback; unknown draft fields fail. Shared validators
  retain the same accepted inputs and canonical values. Fragment failures name
  the candidate/annotation/evidence input path; annotation validation names the
  candidate and annotation, and duplicate frame kind/range failures name both
  original frame indexes. Diagnostic array indexes are zero-based; occurrence
  remains 1-based within its existing scope. ValueError and the original reason
  remain available; callers must not treat human-readable messages as a schema.
  Validate/serialize completely before publishing a private temporary file as
  the new target without replacement. An identical target is a no-write retry;
  a different existing target fails. Publication failures clean up temporary
  files and do not touch input/work/author stores. This is single-file atomic
  publication, not a manuscript/DB transaction or a semantic correctness check.
- **Feedback:** within one write transaction, decay the prior weight to one
  captured instant, apply the delta, clamp to `[1, 100000000]`, and set the
  timestamp to that instant. Rejecting an absent or already-faded option must
  not create/revive it; rejection does not increment picks.
  Runtime capability `feedback_commit_time` requires new choice timestamps to
  be captured only after acquiring the writer lock and rechecking the receipt.
  Slow extraction must not backdate feedback that commits after a newer request.
  Reuse that single instant for phrase/syntax/label weights, the author exemplar
  and receipt. Model extraction remains outside the lock. Opt-out timestamps
  follow the same lock rule, while replays retain their old receipt and times.
  Standalone legacy store locks before its exemplar timestamp. Existing history
  is never rewritten; this does not solve an actual backward-moving wall clock.
- **Ranking:** the first accepted choice already contributes a small weight in
  its section/role context. `picks` records choice history; it is not a minimum
  count that must be reached before `bias` can return a nonzero weight.
- **Candidate ranking:** `rank-file` reads `{candidates:[{id,label,text}]}`. The
  legacy policy reports `score = phrase_score + SYNTAX_SCALE * syntax_score` (six decimal
  places), with optional syntax as a soft secondary signal. Sort descending by
  this score, then by existing effective `label_weight`, then input order.
  Legacy local/global labels break only equal-score ties; they never add points
  to the text score, however large their history. All candidates remain.
  Renaming labels cannot reverse unequal text scores. Legacy bias/record/signal,
  label rows and feedback receipts are unchanged. Candidate scores/order can
  intentionally differ from older additive-label ranking; capability
  `text_first_ranking` identifies this contract. Explicit author prose constraints
  remain the writing workflow's responsibility, not a legacy numeric label.
  Adaptive version 3 uses the policy-specific contract in
  [adaptive learning](adaptive-learning.md). Shared input validation, full text,
  all candidates, label-only equal-score tie-break and read snapshot still apply.
- **Explicit Markdown format:** `markdown_text_format` enables optional root
  `text_format:"markdown"`; absence preserves legacy behavior and hashes. Reject
  null/unknown/non-string values and candidate-level placement before store IO.
  Binder validates/preserves it. Direct rank accepts a keyword-only format;
  CLI rank-file and WorkspaceTools.rank forward the saved root mode. Phrase and
  base-syntax extraction share that mode, including optional parser fallback.
  Only explicit format enters a new choice hash and normal/opt-out receipt;
  legacy receipts remain exact. Do not put this envelope field on rank rows or
  append it to approved history. Test actual marked-file bind/rank/choice and
  metadata-loss conflicts, not only a private emphasis helper. Emphasis parsing
  protects escaped/code/math/destination markers and preserves original texts
  and annotation coordinates; it is not arbitrary Markdown rendering.
- **Formatting continuity:** `formatting_text_continuity` makes supported
  balanced single-argument text wrappers transparent in shared extraction views.
  Remove their control/group syntax without inserting word breaks. The phrase
  view retains excluded math/citation/reference boundaries; the base-syntax view
  keeps those literal regions unchanged. Feed the latter to both heuristic and
  Stanza extraction, avoiding false position shifts and markup tokens. Keep host
  analysis/grammar bound to original raw text. Unknown/starred/malformed or
  comment-containing wrappers retain conservative handling; do not expand user
  macros. Original text, offsets/hashes, exemplars and decision identity remain
  untouched. New formatted choices reinforce shared wording/structure instead of
  rejecting it; historical receipts replay unchanged without backfill. Test
  whole visible features and connective placement, including actual feedback,
  not only words inside a formatting argument or one score component.
- **Consistent rank snapshot:** `consistent_rank_snapshot` requires one SQLite
  read transaction and one decay instant for all candidate label/phrase/syntax
  weights in a rank call. Extract all candidate features before starting that
  transaction, and close the connection on success or any exception. Concurrent
  feedback must not make identical text/annotations get different text scores.
  A later call sees newly committed feedback. Keep model work outside the read
  lock, retain the existing journal mode, and do not change score formulas,
  receipts or the schema. Sidecar/model configuration is not transactional with
  preference rows; change it between calls. Validate both WAL interleaving and
  the default rollback mode with a paused parser; retain exact quiescent output.
- **Complete phrase coverage:** `complete_phrase_features` requires every
  distinct supported word/CJK2/CJK3 feature after the existing literal mask,
  without a lexicographic quota. A retained subset must not turn a shared
  accepted phrase into negative evidence or erase all Latin terms. Rank adds
  phrase_feature_count (also retained by summary); receipt features counts the
  accepted set. Keys/schema/formula remain unchanged. New long-text scores and
  feedback may differ from truncated behavior; old receipts/weights are not
  rewritten, and replay never backfills omitted features. Count is bounded by
  supplied text length, not an arbitrary vocabulary prefix.
- **Compact rank view:** capability `compact_ranking`; CLI `--summary` and MCP
  boolean `summary:true` use one post-ranking projection. The default full output
  is unchanged. Preserve exact candidate text, ordering, scores and all other
  diagnostics; omit only analysis/grammar payloads and matched-feature arrays.
  Include `summary_version:1`, explicit omitted field names and match counts.
  This is not a candidate input or a new scoring path. Never overwrite a saved
  pool from the transport view. Unknown flags and nonboolean adapter inputs fail
  before DB access; input validation/context guard still precedes ranking.
  Required `rank_summary_input_guard` reserves summary_version, omitted_fields
  and matched_feature_counts on candidate rows as output-only. Any one present
  fails shared candidate validation before ranking/choice can access a store,
  including opt-outs and retries. This does not reinterpret old stored receipts
  or restore lost annotations. Default full rank rows and unmarked legacy input
  remain accepted; root metadata is not mistaken for a summary row.
- **Persisted decision context:** runtime capability `choice_context_guard`.
  Optional root `learning_context` contains exactly section and role, each a
  non-empty string without surrounding whitespace. The binder preserves it;
  placing the field in an individual candidate or using an invalid shape fails.
  `rank-file`, `choice-file`, MCP rank and MCP record-choice compare their
  explicit context arguments with the saved values before store access. The
  lower-level `rank_candidates` API accepts a candidate list and explicit context
  as before; it has no document envelope to check.
  New receipts, including editorial-only receipts, record effective context.
  Existing hashes already include section/role; the envelope adds no redundant
  hash field. Old missing-context inputs remain supported, accurately supplying
  their original context preserves hashes, and saved receipts are not backfilled.
  New Skill rounds freeze context before ranking. Reclassification in a later
  blueprint cannot change the feedback context of an approved decision. Recover
  legacy context from actual evidence, not the latest classification. This is
  caller-consistency protection, not a guarantee that the host chose the right
  semantic role or that a manually rewritten pending file is immutable.
- **Per-choice learning consent:** runtime capability `choice_learning_control`
  is required. Binder preserves the optional boolean; invalid types and missing
  IDs for false finalization fail before store access. Chosen is still required.
  Include false in the decision hash, preserving old absent/true hashes exactly.
  False follows its own immediate transaction with a receipt recheck and insert,
  before any feature/model work. All current-schema learning tables remain
  unchanged, including stored times and exemplar rewards. Retries/concurrency
  replay once; a change of intent under the same ID conflicts. Ordinary new
  choices continue learning. This is prospective exclusion, not historical
  unlearning, file erasure or a replacement for author approval. New normal
  diagnostics are additive; old receipts are never backfilled. The common receipt
  insert helper must run inside either path's transaction so failures roll back.
- **Candidate feedback:** `choice-file` requires the same candidate array plus
  `chosen`. It atomically raises selected phrase and syntax features, gently
  lowers features exclusive to unselected candidates once per round, records
  the selected legacy label, and stores an
  `author` exemplar only when `author_owned:true`. Missing ids, duplicate ids,
  empty text, and a missing `chosen` value fail without partial writes.
  Selected features receive positive feedback even when shared with alternatives;
  negative evidence is the union of unselected features minus selected features.
  Candidate order, duplicated alternatives and another id containing the same
  selected text cannot change phrase/syntax feedback. Extraction runs once per
  distinct text before the write lock; all weight/exemplar writes stay atomic.
- **Retry identity:** version-1 canonical JSON includes section, role, validated
  candidate objects sorted by id, chosen id and author_owned, serialized with
  stable keys and exact Unicode text, then SHA-256 hashed. Candidate order and
  unrelated metadata do not affect it; text, labels, choice, ownership and
  context do. Every new field affecting feedback must be included without
  changing hashes for older payloads that omit it. Runtime extractor/model
  settings and time are not another author decision.
- **Grounded analysis:** optional candidate `analysis` is validated by the same
  boundary for file commands and direct ranking, before opening the DB. Version
  1 requires `source:writing-model`, the full text's SHA-256, and at most 16
  exact Unicode spans. Strict fields/enums, complete content coverage (or empty
  abstention), non-overlap, and whole-text binding prevent stale evidence.
  Canonical span order enters the decision hash; absent analysis preserves old
  hashes. Duplicate text cannot carry conflicting analyses. See
  [structure-analysis.md](structure-analysis.md) for the input and enum contract.
  Evidence validation does not establish semantic correctness.
- **Annotation capacity:** grammar binding and canonical validation share the
  version/kind role inventory: expression v1 has three roles, v2–v4 four and
  v5–v7 five; links have two. Both accept at most 64 grammar frames and reject
  overflow before publishing or opening a database. Role uniqueness, evidence,
  overlap and version checks still apply. Analysis retains its 16-part bound.
  Runtime capability `grammar_binding_capacity` and `annotation_limits`
  distinguish this implementation from the older 16-frame/four-slot binder.
  Raising capacity does not change old payloads, features, scoring, or receipts.
- **Analysis learning:** independent `syntax:analysis-v2:writing-model:` features
  reuse syntax storage/decay/atomicity. Explicit analysis replaces lexical
  academic features for that candidate; base surface/parser features remain.
  `off` disables both. Unknown spans never bridge adjacent/full action-order
  features. Positive analysis feedback uses the selected sentence; negative
  analysis feedback requires complete known analysis for every candidate.
  An annotated choice does not penalize legacy academic features missing from
  its own extraction. Rank reports source, coverage status and matching analysis
  features. Choice receipts also report feature count and whether analysis
  rejection was enabled. These diagnostics do not certify model predictions.
  Feature extraction merges consecutive identical fully known action/polarity/
  certainty signatures, keeping unknowns and changed attributes as boundaries.
  Scores and feedback are invariant to such evidence subdivision; raw spans
  and decision hashes remain exact and immutable. Both input versions retain this coarse view; old
  v1 feature rows are retained without reinterpretation or automatic backfill.
  New diagnostics report analysis_feature_version; old receipts replay intact.
- **Analysis uncertainty:** capability `analysis_uncertainty_guard` preserves
  complete-input features/scores and conservatively handles partial attributes.
  Start with must-equal normalized runs. A bounded DAG represents compatible
  contiguous merges; retain a feature only if no path omits it. Unknown labels
  are never guessed, and stable known actions remain usable for new feedback.
  For n incomplete normalized runs, reserve at most two positional features per
  run, n-1 adjacencies and one sequence (capacity 2 for n=1, otherwise 3n).
  Complete capacity is the exact legacy feature count. Replace only the
  surface-analysis denominator with this capacity (independent in scoring v2), keeping
  grammar scoring independent. Further attribute masking cannot add stable
  features or reduce capacity. Rank and new receipts report
  `analysis_feature_capacity`; off/absent/empty analysis uses zero. Existing
  partial rows and receipts are not rewritten, but future partial extraction
  and scores can decrease. This contract fixes text and span boundaries; it
  does not infer evidence deleted or re-segmented by the producer.
- **Grammar frames:** optional candidate `grammar` versions 1, 2, 3, 4, 5, 6 and 7 have declared
  writing-model provenance, whole-text SHA-256 and bounded grounded expression/
  link frames. Validate strict enums, exact Unicode slices, frame coverage,
  unique roles, required event/head/dependent slots, within-frame disjointness,
  versioned frame identity and canonical offset order before any DB open. See
  [grammar-frames.md](grammar-frames.md) for the executable input contract.
  Normalize and hash grammar with the candidate; omitted grammar preserves old
  identity. Matching text must have matching grammar and analysis. Validation
  confirms binding, not linguistic correctness or exhaustive parse coverage.
  Version 1 retains its original form/slot enums. Version 2 additionally allows
  adjectival/copular forms and the expression complement slot; copular requires
  both event and complement. Version 3 adds temporal clause relations; version 4
  adds comparative framing; v5 adds recipient, and v6 requires slots_complete
  boolean on each expression (links reject it). Drafts declaring slots_complete
  infer v6; otherwise recipients default to v5; otherwise comparison defaults to v4, otherwise
  temporal drafts default to v3; other omitted-version drafts retain
  v2 so old binding retries are byte-exact. Explicit v1/v2 reject temporal and
  comparison, and v3 rejects comparison; v1–v4 reject recipient. Existing frames retain their feature
  keys under newer input versions, so grammar-v1 weights remain usable. New forms
  add new keys, not a replacement namespace. Diagnostics report input version
  separately from feature version. Committed input versions remain immutable.
- **Slot certainty:** v6 expression slots_complete=false means unresolved role
  inventory/assignment/boundary. Retain known form/voice positive evidence; omit
  both exact shape kinds from scoring and learning, reserving one zero per
  affected kind/frame without double counting unknown form/voice. Report partial
  grammar and use the existing conservative negative-feedback gate. True can
  represent genuinely absent arguments and does not certify semantic accuracy.
  Explicit v1–v5 reject the new field; v6 cannot omit or coerce its boolean.
  Complete v6 and old input features/scores match. Historical rows/receipts and
  unreported omissions are not repaired or reannotated. No DB/key migration.
- **Argument-shape comparability:** when any candidate contains recipient and
  any candidate grammar predates v5, suppress negative feedback for both
  `expression_slots` and combined `expression` keys, in both choice directions.
  Selected features still reinforce. Other ordinary grammar comparisons retain
  their completeness gates; adjunct positions use the independent gate below.
  This argument-shape version rule leaves all-legacy rounds unchanged. New ordinary receipts include
  `argument_shape_negative_feedback`; old replay and editorial-only receipts
  stay unchanged. No migration or historical reannotation is performed.
- **Grammar learning:** independent `syntax:grammar-v1:writing-model:` features
  reuse syntax decay, context, clamps and the atomic choice transaction. Selected
  features get positive feedback; ordinary grammar-exclusive rejection requires
  known grammar for every candidate. The position-only exception below permits
  voice uncertainty, not uncertainty in the rejected position. `off` disables
  grammar too. Rank averages within expression/link families, sums those
  scores, and adds this grammar score to the prior base syntax score before the
  existing syntax scale. A shared expression must not hide an additional learned
  relative/conditional link. Expose family scores, matched features, source,
  version and status. Without analysis or grammar, legacy base scores stay exact; saved receipts stay compatible.
  Within each family, average each feature kind first, then average over its
  fixed kinds (four expression, two link). Missing kinds are zero evidence;
  marking an unmatched form unknown must not raise the score.
  For multiple frames, each kind's divisor includes distinct known values plus
  one zero contribution per unresolved frame value. An unknown form affects
  form/slots/combined expression; unknown voice affects voice/combined
  expression; unknown relation affects link/order and possible position.
  Repeated unknowns remain separate zero evidence. They are not feature keys
  or rejection feedback. Within one scoring version, complete resolved inputs retain their prior scores;
  partial scores may decrease. Entirely omitted frames cannot be inferred by
  this rule. Existing saved decisions and receipts are never rewritten.
- **Adjunct placement:** known condition/cause/concession/temporal/comparison links can learn
  before/embedded/after keys when a matrix expression's event is inside the
  link head. Expression boundaries and explicit slots determine placement;
  no phrase list supplies it. Missing side evidence, unknown form, a subject-
  only head or conflicting event scopes produce no key.
  An unknown-form expression whose event is inside the head remains unresolved
  matrix evidence; ignoring it must not turn a scope conflict into a known
  placement. An unknown expression without an event still leaves attachment
  unresolved when its saved scope overlaps substantive head content. A partly
  overlapping event also abstains, even with otherwise complete enums. Disjoint
  or separator/whitespace-only overlap does not block valid attachment. Apply
  the same evidence to scoring, positive learning and the separate placement
  comparison gate. Runtime capability `unanchored_grammar_guard` is required;
  this extraction correction changes no schema, feature keys, hashes or old
  receipts. Each unresolved link retains a zero scoring contribution beside
  other known positions. Placement is a separate
  additive family under the existing grammar namespace, so old weights and
  scores remain usable with zero new contribution. Temporal requires input v3;
  comparison framing requires v4 and uses the same grounded matrix evidence,
  including uncertainty and negative-feedback guards. No cue-word detector
  creates this link. Content comparison verbs, quoted names and comparative
  predicates inside conditions need their actual structure, not an automatic
  modifier label. Old inputs retain their exact feature keys and receipts;
  the initial placement extension itself had no new input
  field, hash change or feedback backfill. Require complete placement evidence
  across relevant links for position-negative feedback, separately from enum
  completeness; expose this in `adjunct_position_negative_feedback`. Unknown
  placement does not penalize an alternative's known position. Other grammar
  feedback retains its existing gate. New runtime guards require the capability.
- **Independent position feedback:** placement extraction does not use voice.
  Permit position comparison when every candidate has nonempty grammar, all
  projected expression forms/role inventories and link relations/roles are
  resolved, and every derived placement is complete. Only voice may remain
  unknown. Project duplicate observations before checking, so conflicting forms
  or roles cannot masquerade as voice-only uncertainty. Absent/empty grammar,
  any disabled syntax result and all other uncertainty continue to block it.
  Both compatibility rejection and adaptive differences use the same eligibility
  and feature-family routing. `grammar_status` can remain partial and
  `grammar_negative_feedback`, argument-shape and link-pair gates remain false
  while `adjunct_position_negative_feedback` is true. Only positions become newly
  eligible grammatical coordinates; the existing joint comparative update still
  includes other independently eligible feature families. Positive extraction,
  scoring, schema, keys and immutable historical receipts do not change.
  Require runtime capability `independent_adjunct_feedback`.
- **Explicit predicate targets:** in new nested/coordinated clause annotations,
  a qualifying link can anchor its head to a content event while expression
  frames preserve the full clause and its arguments. Known shared scope over
  coordinated predicates is represented with one grounded link per target;
  nested relative/complement predicates are not automatically additional targets.
  Existing v6 already supports shared dependent spans across distinct links.
  Mixed before/embedded positions across different targets are valid; retain
  same-target uncertainty and all other expression evidence. Deduplicate learned
  keys as usual so two before links do not reinforce one key twice in a choice.
  This annotation guidance changes no input version, extraction algorithm, old
  keys or saved payloads. When the position comparability gate passes,
  adaptive normalized comparative coefficients can distinguish a mixed placement from a single placement even when their
  feature sets overlap; compatibility positive counters alone are not that
  comparative score. `tests/test_predicate_heads.py` covers the retained-evidence
  fixture, binding ambiguity, both policies, adaptive transfer/correction and
  immutable old-round replay.
- **Exact-text Other:** changed author text is analyzed anew. An exact match to
  a saved candidate in the same round reuses its saved analysis/grammar, including
  absent/unknown evidence. A new Other id/label may carry that same annotation;
  do not regenerate a conflicting copy or rewrite committed files. Keep the
  engine's duplicate-text consistency validation strict.
- **Choice receipts:** the same id/hash returns the stored result with
  `replayed:true`, before parsing or refreshing any weights/timestamps. Changed
  content under that id fails. Recheck under `BEGIN IMMEDIATE` after extraction
  to serialize concurrent first deliveries. Persist the receipt and every
  phrase/syntax/label/exemplar write in one transaction. Receipt failure rolls
  back learning too. `prune` retains receipts even when associated preferences
  expire. New decisions with identical text use new ids and reinforce normally.
  No-id legacy calls return `idempotent:false` and remain per-call feedback.
  Completed legacy decisions must not be assigned new ids and replayed on
  upgrade; their application status cannot be reconstructed from total weights.
- **Phrase features:** Chinese runs contribute character bigrams/trigrams;
  Latin words and named alphanumeric terms contribute whole-token features. Pure
  numeric tokens and explicit scientific literals are excluded by the guard below. Features
  are always keyed by `section::role`, never by a global word rule.
- **Syntax features:** `heuristic` records bounded clause, punctuation,
  connective and slot features without dependencies. It also adds the
  parser-independent academic layer: explicit `method`, `result`, `compare`,
  `contrast`, `hedge`, `limitation`, `conclusion`, `extension` and `note`
  cues, their clause-relative positions, move order, slot template and
  punctuation shape. `stanza` uses the local Chinese `zh-hans` `default_fast`
  package and requires an explicitly configured model directory; it never
  downloads during a revision, and reuses the same academic layer on the
  original surface text. Its processor order is `tokenize,pos,lemma,depparse`:
  the provider's dependency parser requires lemma annotations, although feature
  extraction uses surface/POS/dependency values. The selected model directory
  must already contain these processors and their declared dependencies.
  Do not bypass provider requirement checks or mark raw text pretagged.
  Missing backends fall back to the heuristic; `off`
  returns no structure or academic signal. Feature keys carry a version prefix
  so model/normalizer changes cannot silently reinterpret old weights. A
  configured local model version is retained as a diagnostic label; parser
  feature keys use the verified model fingerprint described below.
  The academic layer is a domain adapter;
  it is not a claim that the general UD parser was trained on paper corpora.
  `syntax:v3` isolates prior `v2` weights without deleting their rows or phrase/
  exemplar history. Surface cue matching is shared across academic, heuristic
  connective and slot features; known nominal continuations are excluded.
  Parser cue tokens must also align to the original surface and pass that guard.
  This is a bounded lexical correction, not complete semantic disambiguation.
  Stanza word ids are scoped to their sentence; preserve document order and
  never join dependency heads across sentence boundaries.
- **Model content identity:** use the provider's parameter/resource resolution
  to identify the same four native processors as Pipeline before loading. Each
  processor model must expose a file path; include every declared model/pretrain
  role, preserving order when a role uses several files. Compute SHA256 once per
  unique resolved file, then SHA256 of canonical sorted JSON containing identity
  version 1, provider name/version, language, processor list, role-to-file-digest
  mapping and non-path configuration options. Human labels, absolute paths,
  inode/timestamps and unrelated resource-manifest metadata are not identity.
  Stanza keys are `syntax:v3:stanza-id1-<full-64-hex-digest>:<kind>:<value>`;
  the legacy 48-character namespace limit must not truncate this identifier.
  Identical artifacts moved/retagged keep weights; changed file content or
  provider version gets an isolated namespace. This identifies model inputs,
  not semantic equivalence, artifact trust or bit-identical cross-platform inference.
- **Loaded-model provenance and cache:** retain a cheap state check for manifest
  and actual model files, including resolved paths, device/inode, size and
  nanosecond mtime/ctime. Compare manifest state across resolution, file state
  across hashing/loading and the loaded Pipeline's actual paths/options against
  the hashed configuration. Drift causes fallback without publishing a parser
  under an unverified identity. Missing provider version/model paths or unreadable
  files also fall back clearly. Cache keys include the configured label and
  provider version; unchanged hits skip content hashing. Unrelated files do not
  force reload. A sentence retains the loaded parser's fingerprint and label
  even when configuration changes during parsing. Base rejection requires the
  same effective backend and fingerprint across the round; differing labels
  for the same identity are compatible. Host analysis/grammar keep their gates.
  File-state guards are not an atomic upgrade protocol; maintain models between calls.
- **Identity upgrade:** preserve legacy tag-only parser rows, but do not assume
  which artifacts produced them or relabel them with today's fingerprint.
  Current parser extraction does not query that unverified namespace. Phrase,
  exemplar, heuristic, academic and host analysis/grammar namespaces, database
  schema and original receipts remain unchanged. Never replay old decisions to
  seed the new namespace. New rank/receipts report `syntax_model_fingerprint`
  (null for off/heuristic/fallback); new receipts also contain sorted
  `syntax_model_fingerprints` keyed by candidate ID. Replay returns saved fields
  exactly, without adding current identities to historical receipts.
- **Parser fallback evidence:** rank returns `syntax_requested_backend` and
  `syntax_fallback_reason` (null on success/off). Diagnostics are per extraction,
  never a global last-error value. Failure text is stripped and capped at 1000
  characters. New choice receipts also include a candidate-ID-to-reason map
  `syntax_fallbacks` and `base_syntax_negative_feedback`. Base rejection requires
  every distinct candidate text to use the same effective backend and model
  fingerprint, other than off. Mixed Stanza/heuristic fallback retains selected positive learning but
  does not penalize base features absent due to failure. Uniform fallback keeps
  its normal feedback. Phrase/exemplar and host analysis/grammar feedback keep
  their independent contracts. Existing receipts, decision hashes and schemas
  remain unchanged; replay never reparses or adds current diagnostics to an old
  receipt. Use the same absolute virtual-environment Python path for checker and
  engine calls when optional packages are installed there; resolving its symlink
  to the base interpreter can discard the environment.
- **Exemplars:** maintain at most 500 rows after `store`. Keep the sentence just
  accepted by the author and evict older candidates by decayed reward count,
  then age and ID. `prune` removes an exemplar only when stale and its decayed
  reward count is below one. A lifetime count of two is not permanent immunity.
- **Text:** file commands preserve exact Unicode text, including CRLF and
  trailing newlines. Query output still uses `nearest`'s JSON schema. Do not
  construct a shell command by placing arbitrary paper text in double quotes.
- **Resume:** resolve the absolute engine path before the create/resume branch;
  each tool call supplies it independently of shell state. Persist the approved
  decision JSON before editing working text; log its file, id and receipt.
  Resume the first unresolved sentence even inside an unfinished paragraph.
  Reconcile incomplete text/log work with that saved choice and retry the same
  id. SQLite and manuscript files are not one transaction; a missing author
  choice is not authorization to apply the top-ranked candidate.
- **Install:** update managed code/reference files. Create the memory template
  only when missing; preserve existing memory bytes, other user files and an
  existing symlink to the skill directory. Reject targets inside the source
  repository to avoid recursive copying. Verify the target's runtime before
  publishing new `SKILL.md`; failure is a nonzero installation result, never an
  installed-success message. Managed files can be partially copied on failure;
  this is not an atomic directory replacement. Retry a full update before use.
- **Cross-paper scope:** only the installation's explicitly confirmed text
  profile is carried into a new paper by the writing workflow, with applicable
  author/language/scope and confirmation provenance. Template examples are not
  confirmations; local exceptions and excluded choices cannot promote themselves.
  Phrase/syntax weights and exemplars remain in their paper database. The legacy
  `global` API writes option-label weights under the `__GLOBAL__` context in
  `prefs`; it does not persist semantic instructions, parse the profile, or
  transfer rows. Candidate ranking uses these labels only for equal text-score ties.
- **Tests:** the integration command exits nonzero for a failed assertion. Its
  success marker is printed only after the test process succeeds. These tests
  do not claim to validate a real author's writing interaction.

## 4. Validation and error matrix

The optional stdio MCP contract is specified in [mcp.md](mcp.md). One adapter
loads its own installation's engine once and serializes operations. All six
tools reuse core validation/scoring/transactions; they do not implement a second
learning algorithm. `choice_file(..., expected_round_id=...)` compares the exact
expected ID with the one parsed from its single validated candidate-file read,
before any DB access. Missing/mismatched IDs fail; omitting the keyword preserves
legacy CLI behavior. Receipt fingerprints and old stored receipts are unchanged.
The expected ID protects retries against replacement of a file with a different
round; it does not authenticate a human approval. The host must still obtain and
save the author's choice.

SDK tests are an explicit optional gate, not silently skipped standard tests.
They use actual stdio discovery/calls, legacy and automatic negotiation, concurrent
fresh submission, CLI/restart replay, structured errors, confinement and exact text.
Real-model mode requires Stanza (fallback is a failure) and records cold/warm
timings without a hardware-dependent pass threshold. Default tests exercise the
standard-library adapter, missing SDK, and expected-ID contract independently.

| Condition | Behavior |
|---|---|
| Old table lacks the unique key / cached Chinese grams | Migrate, retain history, rebuild features |
| Migration write/index failure | Roll back, close connection, nonzero CLI exit |
| Database version is newer than supported | Reject before schema/data changes |
| Empty option / non-positive record weight | Nonzero argument error |
| `signal` has positive or negative nonzero magnitude | Apply a downward delta |
| Empty / punctuation-only stored sentence | Nonzero argument error |
| Missing or unreadable text file | Nonzero error; no partial sentence stored |
| Missing syntax package/model or parse failure | Heuristic structure fallback; phrase/exemplar path remains available |
| Candidates mix Stanza and fallback representations | Explain fallback; chosen positive feedback continues; suppress only base syntax rejection |
| Same round_id and normalized decision | Return original receipt with replayed=true; no new learning |
| Existing round_id with changed decision/context | Nonzero argument error; no mutation |
| Invalid round_id type/length/whitespace | Fail before opening database |
| Concurrent identical deliveries | Exactly one applies; remaining callers replay its receipt |
| Receipt write failure | Roll back weights, picks, exemplar and event together |
| No round_id in legacy input | Per-call feedback; idempotent=false |
| Schema v3 receipt-table creation fails | Version and existing rows remain unchanged |
| Installed memory already exists | Preserve it byte-for-byte |
| Unknown CLI command | Nonzero argument error |
| Old/missing engine or unsupported capability response | Preflight fails without opening author DBs; update complete installation |
| `engine-info` given a DB or any extra argument | Argument error; no DB creation/migration |
| Ambiguous/missing/stale draft evidence | Binding fails before output publication or database access |
| Repeated binding to identical output | reused=true; bytes, mtime and round_id unchanged |
| Existing different binding output | Nonzero error; preserve it and use a new output path |
| Engine supports syntax but paper configured `off` | Capability check passes; rank still reports off; preserve setting |
| Target runtime fails during installation | Nonzero failure, new SKILL.md not published, existing memory preserved |

## 5. Good, base and bad cases

- **Good:** a 60-day-old weight of 100000 has effective weight 25000; rejecting
  it by 500 stores 24500, not 99500. Integer reads later may truncate slightly.
- **Base:** record an option without a weight, pause/prune immediately, then
  query its context. The preference remains and other contexts are unchanged.
- **Bad:** recreating a table only with `CREATE TABLE IF NOT EXISTS` cannot add
  a unique constraint to an existing table. Deleting the database to make the
  new schema work discards the very history this feature is meant to preserve.
- **Retry:** if the first response is lost after commit, resubmitting the same
  persisted decision returns a replay; picks and author rewards remain one.
  A new author decision can accept that exact text again with a new id.

## 6. Required assertion points

`tests/test_learning.py` verifies old schema/data migration, rollback, repeat
opening, default CLI calls, context separation, decay before feedback, bounds,
matching, literal file input, stale removal and capacity replacement.
Its Stanza adapter fixture also enforces prerequisite order and explicit local
no-download loading. Output-only parser fixtures cannot validate this boundary;
provider requirement checks and actual pretrained-model inference are separate
evidence, and neither a fallback success nor a fixture proves real inference.
The repeated-choice sequence also checks that a learned leading option can be
replaced by later choices without dropping alternatives or leaking to another
section/role.
`tests/test_installation.py` verifies first install, repeated updates, custom
memory, symlink targets, invalid source targets and incompatible target engines.
`tests/test_runtime.py` verifies no-DB capability inspection and rejection of
missing/legacy/malformed/unsupported responses, including successful exits with
insufficient capabilities. The checker uses its own absolute engine path.
`tests/test_syntax_fallback.py` verifies missing-model diagnostics, selected and
unselected per-text failures, uniform fallback learning, independent grammar
feedback and immutable retry state.
`tests/test_model_cache.py` verifies external configuration and workspace-shared
setting changes, unchanged cache reuse, actual resource replacement/removal,
manifest changes, per-parse version provenance and mixed-version feedback.
`tests/test_model_identity.py` verifies same-tag content replacement, path/label
independence, provider version identity, loading drift, absent identity, mixed
fingerprint feedback, retained legacy rows and no per-sentence rehashing.
`tests/test_annotation_binding.py` covers scoped and repeated/overlapping literal
fragments, supplied anchors/hashes, strict drafts, literal Unicode/CRLF/math,
actual choice replay, retry/conflict/concurrent publication and failure cleanup.
`tests/test_documented_commands.py` executes the marked command blocks from
`SKILL.md` in fresh shells, including `Keep original` and LaTeX text.
`tests/check_fault_detection.py` disables bias weights, sentence similarity,
candidate ranking, syntax extraction, lexical cue guards and contrastive
round feedback, receipt lookup and decision fingerprinting separately in disposable copies; each
must cause assertion failures and a nonzero gate.
The syntax regressions additionally prove author-owned structure feedback,
same-structure/different-word ranking, academic move-order and cue
diagnostics, different-structure rejection, v3 migration, syntax
decay/pruning, atomic rollback and missing local model fallback without network
access. Additional assertions cover all permutations and duplicate candidates,
shared-feature reinforcement, exclusive rejection after decay, technical noun
positive/negative pairs, phantom preference prevention, v2 namespace isolation,
and parser sentence/id boundaries.
`tests/test_choice_events.py` covers receipt replay without reparsing, unchanged
timestamps/history, reordered candidates, conflicting content, invalid ids,
independent identical decisions, no-id compatibility, receipt write rollback,
synchronized racing callers, concurrent fresh CLI processes, expiry/pruning,
and v3 migration with a simulated table-creation failure. The documented
`choice-file` command also replays in a new shell and rejects changed choices.
`tests/test_structure_analysis.py` covers unseen wording/order transfer with
equal words/labels, reversal, context isolation, source diagnostics, text/hash
binding, Unicode offsets, unknown/missing evidence, conflicting annotations,
off/lexical-source behavior, atomic rollback, decay, and legacy receipt identity.
Its annotation fixtures test mechanisms, not a model's semantic accuracy.
`tests/test_grammar.py` checks unfamiliar-word transfer for nominalization,
voice, condition order and relative links, reversal, shared-expression/link
scoring, exact slot coverage and containment, missing/unknown feedback guards,
receipt identity, rollback, off behavior and the executable grammar example.
`tests/test_adjunct_position.py` covers actual independently produced conditional
frames, new-word transfer/reversal/context isolation, missing/conflicting matrix
evidence, cause placement, unknown-position feedback protection, and unchanged
old scores/receipts. It does not establish linguistic annotation accuracy.

## 7. Wrong versus correct

Wrong: `check && echo OK` followed by unconditional `echo PASS`, even with
`set -e`. The failed condition of an AND list is exempt from errexit.

Correct: run the assertion-bearing test process as a standalone command under
`set -e`; only print success after it exits successfully. Verify that this
actually catches a deliberately broken implementation.

Wrong: apply a delta to the original stored weight and reset its timestamp.

Correct: apply the delta to the current decayed weight and persist that value
with the same calculation timestamp, under a write lock.

Wrong: deduplicate by sentence text, regenerate an id on each retry, or mark a
receipt committed separately from its learning writes.

Correct: generate one id per author decision, persist it before acting, recheck
its receipt under the write lock, and commit receipt and learning together.


## Multi-part rhetorical units (analysis input v2)

- New analysis v2 replaces spans with units; each unit has move/polarity/certainty
  and non-empty exact parts. At most 16 total parts, globally disjoint, cover
  substantive content. Empty units abstains. Canonical parts/units sort by source
  start. Bound parts have exactly start/end/text; no arbitrary IDs are accepted.
- Binder infers 2 only from units; spans drafts retain 1 and old bytes. Validation,
  hash, conflict and atomic choice contracts apply unchanged. No migration/backfill.
- Source-ordered unit occurrences feed existing conservative coarse features.
  Independent analysis-topology-v1 pair keys preserve identity (AB, ABA, ABAB)
  with move-only and fully known signatures. They express surface organization,
  never causality or entailment. Same-unit adjacent subdivision is immaterial;
  declaring separate units is a distinct interpretation.
- Complete topology capacity is unique key count; partial capacity is n*(n-1).
  A separate topology mean is added within SYNTAX_SCALE; base denominator and
  numerator exclude topology. Untrained topology cannot dilute v1 preferences.
  Total analysis_feature_capacity includes both independently scored families.
- Positive selected features learn once. Topology rejection requires the old
  complete-analysis gate plus version 2 for every candidate; legacy absent keys
  are not rejection. off/opt-out/receipt replay remain unchanged. Full chosen
  sentences remain exemplars, never fragments or synthetic joined assertions.
- Runtime requires input versions {1,2} and analysis_topology feature v1. New
  ordinary receipt/rank diagnostics include input version and topology capacity/
  version; ranks add score/matches, receipts add count and negative-feedback gate.
  Older receipts are returned verbatim. Tests: test_argument_units.py and actual
  SDK bind/rank/choice/restart exercise the transport as well as engine behavior.


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

`tests/test_phrase_literals.py` exercises number/scientific-notation invariance,
nested notes, math comments, formatting/escaped/malformed controls, stale numeric
rows, exact author text/replay and normal-prose transfer. The same extractor is
used by rank and fresh feedback; no additional database schema or feature-key
namespace is introduced, and shared-positive feedback remains unchanged.

## Shared edit scope

`choice_edit_scope_guard` validates optional root edit_scope before DB access or
binding publication. See [edit-scope.md](edit-scope.md) for exact fields and host
editing/recovery responsibilities. Ordered source IDs, literal before_text and
optional exact Keep baseline are frozen into new scoped decision hashes and
normal/opt-out receipts. Old absent hashes/receipts are unchanged. No DB migration,
file editor or semantic-equivalence check is added. All candidates must describe
the full same source span; the host must verify this and prevent duplicated
neighbors on resume. `tests/test_edit_scope.py` covers malformed input, strict
baseline, CLI/MCP parity, binding retries, scope identity, both receipt kinds,
legacy hash preservation and the untouched-neighbor negative-feedback regression.

## Independent analysis scoring, version 2

- engine-info's code default and legacy rank rows include integer scoring_version=2.
  The current checker requires it and independent_analysis_scoring, plus support
  for adaptive version 3. This is a score formula
  version, independent of DB schema and annotation/feature versions.
- base_count excludes grammar and all analysis keys. base_syntax_score is the
  mean of matched base weights over that count. analysis_score is the mean of
  matched surface-analysis weights over analysis_feature_capacity minus
  analysis_topology_capacity, with max(1,capacity) for either denominator.
- syntax_score = base_syntax_score + analysis_score + grammar_score +
  analysis_topology_score. The phrase mean, SYNTAX_SCALE, label-only tie break,
  grammar families and topology mean are unchanged. Identical rhetorical
  feature/capacity inputs cannot be rescaled by unrelated base-key counts.
- With analysis present, old numerical rankings can intentionally change; prior
  compatibility statements about annotation versions apply within one scoring
  version. Without analysis capacity, the old base calculation is unchanged.
  Input validation/feature extraction, feedback deltas, all stored rows, choice
  hashes and old normal/editorial receipts retain their exact behavior.
- No scoring version is injected into a historical choice receipt or payload.
  Rank results are recomputed views, not new author decisions. A pending old
  round can be reranked under its saved context without changing its pool/ID.
  Never relabel or relearn a saved choice because its newly computed order differs.
- test_analysis_scoring.py uses actual synthetic writing payloads to reproduce
  the old same-history first-choice flip, verify component independence,
  unknown/off/legacy behavior and write-free ranking/replay. Fault checks restore
  the coupled denominator and independently disable analysis scoring.

## Independent grammatical relation order

- Capability `grammar_link_order` is required by the current runtime checker.
  Grammar input 1–7, grammar feature namespace v1 and scoring version
  2 remain accepted/unchanged; database upgrades follow the schema contract above. This adds feature keys and an additive family,
  without reinterpreting any existing key or changing an old-weight score.
- For every unordered pair of link frames, derive each bound slot envelope
  and relation/internal-slot-order signature. Disjoint envelopes yield one
  `link_pair_order:<left-signature>||<right-signature>` feature in surface order.
  Do not use padded frame boundaries, array order, rhetorical moves, raw cue
  words or guessed proposition identities to establish the pair order.
- Unknown relations and nested/overlapping envelopes produce a zero-evidence
  contribution, never a sequential key. Score the distinct known pair weights
  over their count plus the number of unresolved pairs; add this mean as
  `grammar_scores_by_family.link_pair_order`. Masking known labels cannot shrink
  the denominator enough to increase the remaining positive evidence.
- Known selected pairs gain normal context-scoped/decaying grammar feedback.
  Pair rejection requires existing grammar completeness, the same grammar input
  version for the whole pool, and no unresolved pair in any candidate. Ordinary
  new receipts expose `link_pair_negative_feedback`. Other feature feedback is
  unchanged, and all old normal/opt-out receipts and payload hashes replay exactly.
- Only new author choices learn pair keys. Ranking/replay never backfills them.
  No input field, annotation relabeling or model installation is required.
  Identical grammatical signatures, omitted links and semantic ambiguity remain
  limits; the numeric score is not a syntactic or semantic correctness measure.
- `test_link_order.py` covers actual cross-writer/new-word candidates, old-store
  replay, correction/decay/context, slot/padding invariance, uncertainty, nesting,
  duplication and incompatible negative feedback. SDK tests exercise the actual
  annotated pools; fault checks disable extraction, the feedback gate and the
  new uncertainty contribution independently.

## Bound relation identity

`grammar_link_identity` introduced relation observation identity for pair-order
features, capacities and rejection. Use the ordered pair of physical slot
ranges as identity, excluding outer frame scope, relation label and role
names. Equivalent scopes produce one observation. Contradictory relation/role
signatures or explicit unknown make that identity unresolved, including a
single unresolved identity with no possible partner: it cannot enable pair
rejection. Incomplete links keep separate frame/available-slot identities;
their relationship to fully bound evidence is not inferred. Two different
slot partitions with the same envelope remain distinct and safe to compare.

The source objects, payload hashes, old weights and immutable receipts are
unchanged. Ordinary nonduplicate inputs retain exact pair scores and feedback.
Duplicate/conflicting inputs can intentionally rank differently after this
fix; ranking does not rewrite prior learning. Tests cover actual padded scopes,
conflicting labels/directions, cumulative unknown masking and identical-envelope
ties. Mutations reintroduce outer-scope identity and ignore signature conflicts.

## Fieldwise grammar observation identity

The runtime additionally requires `grammar_observation_identity`. Derived
observations group expressions by event range and all physical slot ranges,
and links by their two slot ranges. Unbound expression/link evidence uses its
exact scope and available role/range tuples; no absent anchor is inferred.
For each bound group, form/voice/relation must agree to remain known. Role
disagreement or any partial role inventory removes complete shape evidence;
agreed attributes remain positive evidence. These derived conflicts make
grammar status partial, or abstained if no known features remain, and prevent
rejection. The internal link completeness
flag never enters accepted canonical input or a historical payload.

Use the same observations for grammar feature kinds, missing-evidence counts,
status, and link-pair identity. Adjunct evidence uses one relation observation
but all its relevant original expression scopes with consensus form, preserving
placement conflicts. Duplicate equivalent evidence must leave every family
score unchanged; additional masking cannot improve a score. Normal distinct
observations and old receipts retain exact behavior. Tests include partial
expression duplication, conflicting fields/roles, unknown links, unresolved
placement, cumulative masking and raw-payload conflict/replay. The real SDK
checks repeated partial evidence, and mutations split expression identity and
ignore role consensus independently.

## Exemplar surface cache: validation scenarios

### 1. Scope / trigger

Formatting must not make the same visible author sentence a worse match than
an opposite claim. This is a retrieval cache/API upgrade, not new feedback.

### 2. Signatures

`store(db, kind, text, *, text_format=None)` and
`nearest(db, text, n=5, min_sim=..., *, text_format=None)` share the source mode.
Their CLI forms accept trailing `[--text-format markdown]`; MCP
`sonder_nearest(sentence_file, limit=3, text_format=None)` forwards that mode.
DB and runtime versions are specified in **Upgrade** above.

### 3. Contracts

Raw text remains the author record; `_text_feature_views(...)[1]` is derived
matcher input. Scientific literals remain in this view. Cached ngram counts
supply both document frequencies and vectors. Optional matchers receive the
same surface interpretation. No new environment variable or dependency applies.

### 4. Validation and errors

| Input/state | Required behavior |
|---|---|
| Omitted source mode | Legacy path; old Markdown is not guessed |
| Explicit `markdown` | Supported emphasis is transparent |
| Unknown/empty/non-string mode | Reject before author-store changes |
| Empty normalized new import | Reject; an empty query returns no matches |
| Existing normalized-empty row | Preserve author history; zero similarity |
| Stale nonnegative index version | Rebuild derived cache transactionally |
| Future/invalid index version | Reject without downgrading or dropping rows |
| Migration write failure | Roll back columns, cache and schema version |
| Current malformed cache counts | Fail clearly instead of inventing a vector |
| Invalid CLI tail / missing mode value | Nonzero exit, no silent ignored flags |

### 5. Good / base / bad cases

Good: a newly confirmed `pre**condition**` in Markdown matches `precondition`
with sim 1 while returning the stars. Base: an unchanged unformatted sentence
retains its prior matcher score. Bad: guessing Markdown for an old `a*b*c`
entry, or treating a high score as proof that negation/evidence can be copied.

### 6. Required tests

`test_exemplar_format.py` covers exact visible negation ranking, persisted mode,
v0/v4 migration, transactional failures, cache epochs, replays/opt-out and
optional backend inputs. CLI/MCP tests must assert persisted mode and returned
raw text, not merely a successful exit. An actual writing-host trial must keep
the current claim's negation and evidence strength when consulting examples.

### 7. Wrong versus correct

Wrong: compute DF from old raw-text caches and fresh vectors from cleaned text;
rerun old author decisions to update the index. Correct: rebuild only versioned
derived caches, use those counts consistently, and leave old receipts/history
intact. A fresh confirmation updates the exact-text row; a retry does not.

## Original receipt file export

### 1. Scope / trigger

Host logging can fail after feedback commits. Export the original event with the
feedback/replay operation so recovery does not depend on reconstructing JSON in
an ad hoc wrapper. This is not manuscript application, author approval, a rewrite
of the human log, or a transaction spanning SQLite and the filesystem.

### 2. Signatures

- `choice_file_export(db, section, role, path, receipt_file, *, expected_round_id=None)`
- `choice-file DB SECTION ROLE CANDIDATE_JSON [--receipt-file FILE]`
- MCP `sonder_record_choice(section, role, candidate_file, round_id, receipt_file=None)`

Omitting the option preserves the original `choice_file` API and response. Export
requires a saved round ID; no-ID legacy decisions must not be assigned one merely
to replay them. CLI extra tails other than this exact optional pair are errors.
MCP output must remain inside this workspace's `choice-receipts/` directory.
The parent directory must already exist; no external dependency is added.

### 3. Contracts

One validated candidate snapshot supplies preflight, payload identity and feedback.
A common data-level owner retains the original choice body; do not read a changed
candidate file again between destination validation and learning. Scoring, feature
feedback, hashes, schema and original `choice_events.result_json` stay unchanged.

The canonical UTF-8 file is sorted/indented JSON followed by one newline:

```json
{
  "receipt_export_version": 1,
  "round_id": "the-existing-author-decision",
  "payload_hash": "the-existing-payload-hash",
  "created": "the-original-event-time",
  "result": {}
}
```

`result` is the exact parsed original DB result, including absent legacy fields.
It is not the replay projection: a command can report `replayed:true` while the
file retains its original false/absent field. The export call adds absolute
`receipt_file` and boolean `receipt_file_reused` only to its returned response,
not to the DB result. Existing files must be byte-identical to the canonical
export of the current DB row; a merely similar JSON object or different formatting
is not overwritten. Numeric booleans/floats are not silently equated with integers.

Check existing output against a read-only snapshot of this DB's row, hash, creation
time and original result before feedback. Missing DB/event is a refusal, not a
reason to learn from the file. After commit, recheck the row against the accepted
snapshot hash and result before publication. Export never establishes that a
manuscript edit or human-readable log finished.

Publish complete temporary bytes using atomic no-replacement publication, flush
file contents and the parent directory, and clean owned temporaries. An identical
retry verifies and flushes existing content without changing bytes or mtime.
Concurrent calls may independently win DB commit and file publication; only one
event and one canonical final file should result. Filesystem durability follows
the local platform's fsync behavior; this is not a cross-resource atomic commit.

### 4. Validation and error matrix

| State | Required outcome |
|---|---|
| Missing chosen/round ID, invalid context/protocol | Reject before feedback |
| Missing parent, directory/special file, output symlink | Reject before feedback; preserve existing object |
| Candidate/DB alias, SQLite -wal/-shm/-journal, runtime-control sidecar path | Reject even when the reserved file does not yet exist |
| Existing canonical file but this DB has no matching event | Reject before new feedback; do not trust/import file history |
| Existing file differs in original result/hash/time/version/bytes | Reject before feedback; never replace it |
| New/unchanged output after successful feedback | Export original row; normal or replay response reports path/reuse |
| File race, ENOSPC, permissions or fsync failure after commit | Fail clearly: feedback may already be recorded; same saved decision/ID must be retried |
| Retry after commit/publication failure | Reuse the one DB event; finish or reuse file, with no reinforcement |

A conflicting output can be preserved while choosing a fresh receipt path for the
same decision. This cannot bypass a payload conflict under an existing round ID.
There is no automatic rollback of already committed feedback on file failure.

### 5. Good / base / bad cases

Good: stdout is lost, but the original receipt file exists; retry reports replay
and leaves weights/mtime unchanged. Base: an ordinary no-file choice returns its
old result shape. Bad: copy a valid receipt into another empty store and treat it
as proof that its feedback already happened, or retry under a new round ID because
a logging parser failed.

### 6. Required tests

`test_receipt_export.py` covers original normal/opt-out/legacy results, stdout loss,
preflight collisions/reserved paths, missing foreign-store events, post-commit
publication/flush failures, exact retries, concurrent publication, changed-input
snapshot and post-commit row consistency. CLI/MCP and documented-command tests
verify real option forwarding and paths. Retain the default feedback body/old
actual receipts exactly; moved fault injections must still hit that common owner.

### 7. Wrong versus correct

Wrong: serialize the current replay response, overwrite a conflicting file, or
regenerate a decision ID after export failure. Correct: export the authoritative
original DB event, publish without replacement, preserve the conflict and retry
the same approved snapshot/ID. Update the human log using normal file editing and
link the saved receipt instead of rebuilding it in inline script strings.

## Scenario: local opposition policy and shared adaptive history

### 1. Scope / trigger

Repeated contrary choices should correct participating text/structure features
without globally shrinking sparse unrelated preferences. `adaptive-v2` adds the
frozen local trace rule; v1's extraction, score formula and explicit behavior stay
available. This changes future learning, not the original author's saved decision.

### 2. Signatures

- CLI: `learning-policy DB [legacy|adaptive-v1|adaptive-v2|adaptive-v3|ensure-adaptive]`.
- Python: `learning_policy(db, action=None)`; MCP: `sonder_learning_policy(action=None)`.
- Schema 7: `adaptive_opposition(context TEXT, feature TEXT, value REAL, last_at TEXT)`;
  primary key `(context,feature)`, non-null columns, `0 <= value <= 32`.
- Policy/scoring pairs: legacy/2, adaptive-v1/3, adaptive-v2/4, adaptive-v3/5. Runtime capability
  `local_opposition_learning` remains required; v3 also requires the relation
  coverage and relative-learning capabilities described above.

### 3. Contracts

`ensure-adaptive` selects v3 only if no policy was saved. Inspection and repeated
selection do not clear traces. All adaptive policies share native weights and
the fixed legacy snapshot; v3 additionally retains its signed relative state.
An actual change involving v2 or v3 clears only the transient trace in the
policy transaction; interrupted policy writes restore both setting and trace.
Legacy-mode choices keep the existing no-backfill behavior on adaptive resume.
No selection/reselection or migration creates an author event or changes old
receipts. Schema migration and subsequent policy configuration are separate
atomic transactions; a failed policy change does not downgrade the migrated DB.

For each distinct comparable difference, divide its positive/negative magnitude
by `max(chosen_coefficient, alternative_coefficient)` and average across all
compared differences. Clamp each aggregate mean to one: repeated floating-point
addition of nine `1/9` terms must not make a legal trace negative. At the choice commit clock, calendar-decay the prior trace
and set `min(32, old*(1-positive)+negative)`. Apply
`2**(-negative*trace/32)` only to negatively compared existing comparison weights,
then use the unchanged logistic/profile update. Trace and all feedback writes
share the original receipt transaction. Queries do not refresh it. Unknown
comparisons, no differences, explicit opt-out and saved receipt replay cannot
invent trace evidence. Previously stored timestamps and decision hashes survive.

### 4. Validation and error matrix

| Condition | Required behavior |
|---|---|
| Unknown action / invalid type | Reject before opening the store |
| Future schema | Reject, no downgrade or reset |
| Schema table creation fails | Roll back schema and version marker |
| Policy update fails after clearing trace | Restore old setting and trace |
| Original receipt write fails after trace/weights | Roll back the whole choice |
| Export file fails after successful DB commit | Keep committed choice; original-ID retry/export, never imply DB rollback |
| Same ID / changed candidate pool | Reject without feedback; policy changes do not grant a new ID meaning |
| Old code or incomplete installation | Runtime preflight fails before writing the revision workspace |

### 5. Good / base / bad cases

Good: v1 history -> explicit v2, same scores and all original receipts; future
contrary choices update local traces. Base: ensure on saved v1 keeps v1, and an
unset direct API caller still uses legacy. Bad: copy old v1 parameters back after
v2 feedback, silently clear traces on each resume, or replay old receipts as new
training to populate a trace. Switching between policies is not a schema downgrade.

### 6. Required tests

`test_local_opposition.py`: default/respect-saved policy, score-preserving adoption,
shared model across switches, repeat stability, policy/schema/choice rollback,
old replay and opt-out bypass, unknown/no-comparison guards, context/read-only
clock behavior and independent arithmetic. Frozen research-to-production parity
must also compare old, mixed, tiny-difference and repeated-return trajectories.
Actual CLI/MCP restart/concurrency and installed preflight tests cover consumers.

### 7. Wrong vs correct

Wrong: normalize the entire context before activating correction, then keep the
old full learning step; a frequent unrelated family can make a sparse preference
flip after one contrary choice. Correct: retain initial parameters exactly and
accumulate continuous evidence only for features in eligible current comparisons.
Do not call a repeated synthetic template corpus a population calibration.


## Checked preparation

`prepare_file(db, section, role, input_path, output_path, *, summary=False)` and
MCP `sonder_prepare` bind once, validate context, publish with the same byte-exact
no-overwrite helper as bind-file, then rank the bound in-memory candidates.
`checked_candidate_preparation` identifies support; the Skill checker requires it.
The response is `{preparation_version:1, binding:{output,round_id,candidates,reused},
candidates:[ranked rows]}`. Existing canonical/receipt formats and scores do not change.

Invalid draft/context/summary or publication conflict fails before rank/store
access. Rank failure after publication leaves valid canonical bytes for retry;
there is no cross-resource transaction or implied author approval. Preparation
never switches policy, records feedback or overwrites old decisions. Preserve
source format, context, scope, unknowns and complete annotations. CLI/MCP use the
same core, and MCP retains workspace/output restrictions and its operation lock.

## Coextensive grammar frames (input v7)

Version 7 retains v6 fields and permits distinct physical predicates/links to
share their necessary outer evidence. Versions 1–6 keep strict kind/range
uniqueness. For v7, equal kind/range additionally requires nonempty, distinct
anchors: expression event start/end or the link's two sorted slot ranges.
Roles are not part of this anchor identity; changing a role/tag cannot bypass
collision rejection. Missing anchors block coextensive acceptance in either order.

`relation_features.frame_key` locates these frames for graph membership, canonical
sort ties and draft coverage remapping. No reference may alias two different
predicate heads. Original distinct-range input order/bytes and receipt hashes
remain unchanged. Omitted-version inference stays unchanged; new writing drafts
choose explicit v7. Existing physical-observation consensus, feature formulas,
normalization, partial capacity and learning policies are unchanged. This input
extension itself required no database schema change; the later context-key
format update requires schema 9.
The required runtime capability is `coextensive_grammar_frames` alongside grammar
input 7. Old MCP processes require restart after installation. Successful binding
certifies literal consistency, not semantic truth or participant inference.

## Scenario: injective context keys and ambiguous historical rows

`_context(section, role)` alone owns tuple-to-storage mapping. Preserve the legacy
concatenation only when its sole overlapping `::` occurrence is at the actual
field boundary. Otherwise use `@sonder-context-v2:` followed by canonical JSON
of the original string pair, with every colon inside JSON escaped as `\u003a`.
Encoded keys contain no raw `::`; ordinary keys always do. JSON protects quotes,
backslashes and Unicode. Do not split an opaque `reshow` key or reinterpret it as
a saved `learning_context`. The intentional `__GLOBAL__` label namespace stays
separate. Original candidate fields, hashes and receipt bodies do not change.

Schema 9 marks this format without adding tables or rewriting rows. Older engines
must refuse it. For a canonically encoded key, check its old ambiguous alias in
`prefs`, `seen`, `phrase_prefs`, `syntax_prefs`, `adaptive_weights`,
`adaptive_legacy`, `adaptive_legacy_mass`, `adaptive_opposition` and
`adaptive_relative`. Any matching row causes an explicit ambiguity error; never
silently ignore, delete or assign historical mass. Aggregate rows cannot safely
be partitioned without provenance and author intent. No resolution tool is
provided by this change. Ordinary context reads/updates retain exact behavior.

Rank and new choice check before expensive extraction and again within their
actual read snapshot or serialized write transaction. Record/signal check under
their write lock; bias uses one read snapshot. Receipt lookup precedes the guard,
including its locked recheck, so retries return original results without learning.
Explicit opt-out also bypasses weight extraction/guard and stores only its receipt.
Schema migration can precede a failed operation; preserve rows, but do not claim
that a rejected rank leaves an old version marker unchanged. Prepare publication
is separate from ranking, as in the checked-preparation contract.

Policy setup preserves opaque keys; global label preference, paper-wide exemplar
retrieval, raw reshow and existing prune semantics remain separate. Prune uses full
key equality; no key parsing or automatic historical repair. Runtime requires
`injective_context_keys` and integer `context_key_version:2` alongside schema 9;
restart older running MCP servers after a complete install.

Validation: `test_context_keys.py` covers ordinary/encoded namespaces, boundary
colons, all context tables, old replay/export, opt-out, lock/snapshot guards,
concurrency, globals and opaque pruning. The SDK gate exercises real tuple
isolation and optionally a frozen schema-8 engine (`--legacy-engine-root`). Release
checks use actual old stores, fixed-clock full rank/row parity, the original
Chinese object-fronting collision, old-engine refusal and independent-process
commit-order reconstruction. Synthetic compatibility evidence is not a semantic
accuracy result and must never involve an actual author's database.
