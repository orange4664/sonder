# Workspace file templates

Seed these in Phase 1/2. Keep them plain Markdown so the author can read them directly.

## Blueprint — recorded after the Phase 2 brainstorm

The blueprint is the agreed shape of the paper. It is the basis for segmentation and revision, so keep it accurate if the author later changes the structure.

```markdown
# Blueprint — <paper title or filename>

- Paper orientation: <one line — what this paper is arguing>
- Confirmed: <date>
- Segmented after: <blueprint finalization>

## Paragraph threads

| #  | Main thread | Role / change |
|----|-------------|---------------|
| 1  | <what this paragraph is for, in 1-2 sentences> | ok |
| 2  | <...>                                     | merged from old ¶2-3 |
| 3  | <...>                                     | reordered to follow ¶1 |
| 4  | <...>                                     | added — was missing |

Status / change values: ok · merged · split · reordered · added · deleted(moved to ¶N).
```

Keep it tied to the actual prose: if the author restructures during revision, update this file first, then re-serve segmentation from it.

## revision-log.md

```markdown
# Revision log — <paper title or filename>

- Source: <path to original>
- Started: <date>
- Total prose sentences: <N>   (fill in after segmentation)
- Last completed paragraph: <# / id, or "none yet">
- Next paragraph to start: <# / id>

## Sentence map

| ID   | Paragraph | Status   |
|------|-----------|----------|
| S001 | ¶1        | kept    |
| S002 | ¶1        | edited  |
| S003 | ¶2        | pending |

Status values: pending · kept (original unchanged) · edited (rewritten) · merged_into:S001 (consumed by the named lead).
Keep stable original IDs; add a shared round/decision reference for all members.

## Structure notes

_Filled during the Phase 1 read, resolved into the blueprint in Phase 2, updated as you go._

### Paragraph map (pre-blueprint)
| Paragraph | Topic sentence (id / "buried" / "missing") | Flow into next |
|-----------|--------------------------------------------|----------------|
| Intro ¶1  | S004                                        | ok             |
| Intro ¶2  | buried (claim in S011, not S009)            | weak — no link |

### First-use map (catch use-before-define)
| Term / symbol / acronym | Defined at | First used at | Issue              |
|-------------------------|------------|---------------|--------------------|
| SNR                     | S031       | S012          | used before defined |
| $\gamma$                | S020       | S020          | ok                  |

## Decisions

### S002 — edited (Medium)
- Original: It is important to note that the results were significantly improved.
- Final:    The results improved by 23%.
- Fixes:    cut filler opener; replaced vague "significantly" with the number.
- Round id: <persisted UUID for this author decision>
- Candidate file: candidate-inputs/S002-<round-id>.json
- Edit scope: <ordered original IDs and exact before_text; same as candidate file>
- Locator: <working file, paragraph and disambiguating surrounding context>
- Learning context: <exact section and role saved in candidate learning_context; preserve on recovery>
- Context basis: <paragraph purpose and primary sentence job, decided before ranking; comparable earlier cases and any secondary role/uncertainty>
- Learning intent: <ordinary learning, or explicit author exclusion saved as learn_from_choice:false>
- Feedback: <successful choice-file receipt; idempotent=true, replayed=false or true; feedback_applied=true or false>
- Receipt file: <choice-receipts/...json; engine-exported original database receipt, link without reconstructing it>

### S001 — kept
- Original: We study the dynamics of driven quantum dots.
- Final:    (unchanged — already clean and in voice)
- Learning intent: <preserve any explicit author exclusion; keeping text does not itself grant permission to learn>
```

Append a new `###` block per author decision as you go (one block for a shared multi-sentence scope), newest at the top or in order — just stay consistent. The sentence map's Status column is the quick at-a-glance record of what's been reviewed; the Decisions blocks are the detail.

A kept/edited sentence can be complete with `feedback_applied:false`: its
editorial decision was recorded but contributes no preference or exemplar.
Keep the reason scoped to this decision in the log, not as a standing style
rule. A host audit without an engine receipt must be labeled as such; recovery
must retain the author's exclusion rather than silently learning it later.

## style-profile.md

```markdown
# Style profile — <author / paper>

_Initial read (before any choices), then updated as the author selects. Each entry carries a scope; see preference-learning.md._

## Voice
- Sentence length: <medium, ~22 words, low variance>
- Person/voice: <"we", active where the field allows>
- Register: <formal physics letter>
- Field conventions: <passive in Methods is fine; hedge claims>

## Punctuation
- Em-dashes: <uses / avoids>
- Semicolons, parentheticals: <...>

## Vocabulary
- Recurring terms: <field terms, pet phrases — these stay available, but note them>
- Terms the author keeps using/moving away from: <recorded in preference.db, not here>

## Observed wording and structure
- Observed preference: <accepted wording, phrase features and optional structure features>, updated as choices come in — soft, from `learn.py rank-file`.

## Preferences (learned, in preference.db)
The machine-learned preferences live in the workspace `preference.db` (see reference file `learn.py`), keyed by `section::role` and candidate phrase/structure feature. Keep this human-readable note only:
| Context | Observed wording or construction | Evidence and limits |
|---------|----------------------------------|---------------------|
| <section::role> | <actual accepted phrase or sentence pattern> | <sentence IDs / round IDs; tentative, local to this paper> |

## Notes
- <anything the author says about how they want to be edited>
```

Treat the initial read as a hypothesis. Overwrite it with evidence as the author makes real choices — what they actually pick beats what you guessed from the prose. And prefer a scoped entry that's short to a rule that's broad: when in doubt, keep it local to this paper and let the author explicitly confirm any lasting scope.

The table is a blank format, not an observed preference. Describe actual wording
or construction evidence; Light/Medium/Bold names are display labels, not the
learned language. Do not invent weights or copy database scores into prose rules.
Explicit lasting preferences belong in the installation's
`memory/style-profile.md`, with author/language/scope and confirmation provenance.
The legacy `global` CLI does not save that file. Template examples are never
author confirmations; an excluded choice belongs only in the revision log.

## candidate-inputs/<sentence-id>-<round-id>.json

Generate and persist one unique `round_id` before asking for a decision. Keep
the exact candidate texts, then persist the author's choice before applying the
text and calling `choice-file`. Retries reuse this file/id; a new confirmed
decision uses a new id/file, preserving previous evidence:

For new writing rounds, first save the semantic draft with the same round ID and
both annotation objects, then run `bind-file` to publish this canonical file.
See [annotation-binding.md](annotation-binding.md). The abbreviated envelope
below omits annotations and is not a complete binding draft.

In semantic drafts, comparison framing uses grammar v4, temporal uses v3,
and ordinary omitted-version drafts retain v2. The binder fills the appropriate
missing version; never change a committed annotation version to add features.

```json
{
  "round_id": "<persisted-decision-uuid>",
  "learning_context": {"section": "results", "role": "support"},
  "edit_scope": {"sentence_ids": ["S001"], "before_text": "...", "keep_original_id": "keep"},
  "candidates": [
    {"id": "light", "label": "Light", "text": "..."},
    {"id": "medium", "label": "Medium", "text": "..."},
    {"id": "bold", "label": "Bold", "text": "..."},
    {"id": "keep", "label": "Keep original", "text": "..."}
  ],
  "chosen": "medium",
  "author_owned": false
}
```

`author_owned` is true for Keep original or a complete sentence supplied by
the author in Other. Candidate ids are stable within this file; labels may be
renamed without changing phrase or structure memory.

For new rounds, persist `learning_context` before the first binding/ranking and
use those exact values in every CLI/MCP rank or choice call. Keep this context
when adding Other or recovering an approved decision, even after a blueprint
change. An existing round is not reclassified by editing its context or ID.
Older files omit the field; add it only when original context is independently
known. New receipts report the effective context; old receipts replay unchanged.

The abbreviated example above omits optional `analysis`. In the writing loop,
attach an analysis object to every real candidate using the executable example
in [structure-analysis.md](structure-analysis.md). For new rounds use v2 units/parts; preserve legacy spans. Persist exact evidence and
whole-text hash before ranking. Reanalyze changed Other text. For an exact
match to a saved candidate in this round, reuse that candidate's annotations;
if adding an Other id/label, copy both analysis and grammar unchanged. Keep
committed analysis unchanged on resume/retry.
Attach `grammar` using [grammar-frames.md](grammar-frames.md) as well, to retain
event form/voice, participant order and known subordinate relations. Follow
the same exact-match rule for grammar; keep committed grammar immutable.

For new candidate envelopes include root `edit_scope` as described in
[edit-scope.md](edit-scope.md), also for one-sentence rounds. After merging S001
and S002, log one shared receipt, keep S001 edited, mark S002 merged_into:S001
and leave original S003 pending. A preserved two-sentence alternative records
each member kept/edited but still has only one shared learning event.

For new rounds, record the context basis before binding/ranking, using the
[role definitions](preference-learning.md#the-context-is-the-sentences-job-not-its-topic).
This is host documentation, not a new root protocol field. Keep a historical
approved context unchanged; an absent old rationale is not permission to invent
a new context or move learned rows.
