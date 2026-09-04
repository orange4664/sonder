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
| S001 | ¶1        | done    |
| S002 | ¶1        | edited  |
| S003 | ¶2        | pending |

Status values: pending · kept (original unchanged) · edited (rewritten).

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

### S001 — kept
- Original: We study the dynamics of driven quantum dots.
- Final:    (unchanged — already clean and in voice)
```

Append a new `###` block per sentence as you go, newest at the top or in order — just stay consistent. The sentence map's Status column is the quick at-a-glance record of what's been reviewed; the Decisions blocks are the detail.

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

## Edit aggressiveness
- Observed preference: <Light / Medium / Bold>, updated as choices come in — soft, from `learn.py bias`.

## Preferences (learned, in preference.db)
The machine-learned preferences live in the workspace `preference.db` (see reference file `learn.py`), keyed by `section::role` and `version`. Keep this human-readable note only:
| Context | Is the author leaning toward | Notes |
|---------|------------------------------|-------|
| abstract::topic | Medium (weight 4) | |
| intro::support | Light (weight 3) | |

## Notes
- <anything the author says about how they want to be edited>
```

Treat the initial read as a hypothesis. Overwrite it with evidence as the author makes real choices — what they actually pick beats what you guessed from the prose. And prefer a scoped entry that's short to a rule that's broad: when in doubt, scope it `local` and let the author promote it to `global` explicitly.
