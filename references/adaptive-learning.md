# Adaptive preference learning

Schema 8 adds the relative relation layer described in
[relation coverage](relation-learning.md). `adaptive-v3` reports scoring version 5.
It preserves native weights and starts its new signed state empty; old choices are
not replayed. Missing coverage gives no new relation features. An existing saved
policy stays unchanged on `ensure-adaptive`; select v3 explicitly only when the
author requests that workspace upgrade.

For each finite relation/attribute group, v3 retains signed relative evidence and
scores it as `theta - min(theta)` over all permitted categories, including unseen
ones. Its comparison gradient includes the derivative of this minimum when
candidate group masses differ. Tied minima use equal branch weights. Relative
coordinates use the fixed 256 metric from the eight relation groups and two
attributes; it is not an inverse sentence-length multiplier. Profile observations
remain nonnegative, and a single sentence teaches no comparative preference.

V3 uses the existing local opposition trace to reduce only positive excess above
the group's unchanged minimum before the ordinary gradient. It does not shrink a
negative latent value toward zero. Group centering is score-invariant; bounding
scales the whole span. Uniform 30-day decay commutes with this nonnegative readout.
Native comparison/profile/legacy channels stay in their existing tables;
`adaptive_relative` stores only new signed local comparison values. All channels
and the receipt share the original transaction and commit timestamp.

Switching between saved policies retains learned native and relative values;
entering or leaving a correction policy clears only its transient opposition
trace. V1/v2 do not extract relation features or update signed relative state.
The shared profile still undergoes its ordinary decay and mass normalization,
including retained relation observations. Opt-out and replay bypass
new learning; old receipts retain their original bodies and hashes. Schema-7 code
must refuse a schema-8 store, even if the active setting is an older policy.
The current schema 9 additionally makes custom context keys collision-free;
schema-8 engines must refuse it. Ordinary scores and learned values are unchanged.
Ambiguous legacy history is preserved behind an explicit rank/learning guard;
see [updates and historical data](runtime-setup.md#updates-and-historical-data).

Recent synthetic checks found improved single-opposition stability after four
supporting choices, while the same small history after 30 days remained sensitive.
Those are controlled observations, not author-population calibration or a promise
that a particular number of choices must change a ranking. Meaning and approval
remain the author's responsibility.

The current Skill initializes `adaptive-v3` for an unset paper workspace. Any
saved `legacy`, `adaptive-v1`, `adaptive-v2` or `adaptive-v3` policy remains respected. The policy is local to that
paper; it does not write a cross-paper style instruction or approve a sentence.

```bash
python3 -I "<learn-path>" learning-policy "<db-path>" ensure-adaptive
```

The equivalent MCP call is `sonder_learning_policy(action="ensure-adaptive")`.
Omit the action to inspect the policy. `legacy`, `adaptive-v1`, `adaptive-v2` and `adaptive-v3`
explicitly select a policy; repeated selection does not recreate the history snapshot. The returned
`policy`, `configured`, `scoring_version`, `adaptive_initialized` and `changed`
describe configuration, not whether a new author choice occurred.

## What an approved choice teaches

An accepted sentence provides evidence of expressions the author uses. Known
phrase and structural features update a nonnegative expression **profile**.
A single author sentence can update it without alternatives. At each accepted
update, the profile first decays, receives a positive update, and is scaled down
if its total weight exceeds one. Queries only decay; they never renormalize.

When distinct candidate texts provide comparable evidence, their differences
also update **relative preferences**. The update uses the same normalized feature
coefficients as ranking. It is a projected online logistic update with step one,
or 1.5 for author-owned wording. All comparisons in one round use the same prior
state; duplicate texts/differences cannot multiply a round's evidence. Another
ID holding the chosen text is not an opponent.

Comparability is checked separately for phrases, base parser features, rhetorical
analysis, topology and grammatical families. Unknown, partial, absent older-version
roles or differing parser identities cannot establish that a feature was rejected.
Known parts of the accepted text can still inform the profile. Inspect the saved
`adaptive.contrast_feature_counts` before calling an update structural learning;
phrase-only comparison evidence is possible.

Adjunct positions have an independent gate: unknown voice alone does not block
comparison if every candidate's nonempty grammar has resolved forms, role
inventories, relations and placement. Other grammatical comparisons stay guarded
by full completeness. The receipt may therefore report partial grammar and
`grammar_negative_feedback:false` with `adjunct_position_negative_feedback:true`.
No unknown voice is relabeled or treated as rejected; only positions are newly
eligible grammatical coordinates. The joint update still includes independently
eligible phrase/base/analysis differences. Old receipts keep their saved gates.

The compatibility `phrase_prefs`, `syntax_prefs` and label counters keep their
existing update behavior. They are not a second adaptive comparison update.
Both adaptive layers, existing counters, author exemplar and original receipt
commit in one existing choice transaction, at one commit time. Only one approved
choice is recorded; a derived profile is not another author event.

An explicit `learn_from_choice:false` updates none of these layers and performs
no feature extraction. Replaying a saved round returns the original result,
even if the current policy differs, without learning it into a new layer.

## Local correction in adaptive-v2

`adaptive-v2` keeps v1's nonnegative profile, comparison model and score formula.
It adds a per-context, per-feature correction trace for future comparable choices.
For each distinct candidate difference, relative positive/negative evidence is
the corresponding signed coefficient difference divided by the larger of the
chosen and alternative coefficients. Evidence is averaged across the same
distinct differences as the ordinary update. Missing/unknown comparisons provide
no evidence; tiny differences remain tiny rather than becoming a full event.

At the serialized feedback time, decay the old trace with the existing 30-day
half-life, then compute `trace = min(32, old * (1 - positive) + negative)`.
Only negatively compared existing comparison weights receive the additional
factor `2**(-negative * trace / 32)` before the ordinary logistic update. Positive
evidence can reduce or clear the trace. Unrelated features are not refreshed or
forgotten by this rule. A single-candidate observation only updates its profile;
opt-out and receipt replay bypass both model and trace updates.

The trace, profile, comparison, compatibility counters, exemplar and original
receipt share one transaction. Failure before commit rolls all of them back.
Read-only ranking decays its score weights but never refreshes stored traces.
This is a soft correction mechanism, not a ban or a semantic-correctness score.
The horizon 32 is a regularization choice, not a calibrated author population
constant; sustained conflicting feedback can still produce ranking fluctuations.

## Ranking and old history

Adaptive rank rows use `scoring_version:3` for `adaptive-v1` and version 4 for
`adaptive-v2`. Version 4 identifies the changed learning policy; both use:

`score = comparison_score + profile_score + legacy_prior_score`

The old history snapshot is taken once, under the policy-setting write lock,
before prospective adaptive learning. It retains original phrase/syntax weights
and `last_at` values. Each context's reference mass is the sum of its original
raw text-feature weights, excluding label/global preferences. The legacy score
is divided by that fixed mass (at least one). This preserves old ranking order
when it is the only signal, while limiting its influence alongside new evidence.
The snapshot is not a reconstruction of historical comparisons.

`legacy_score` reports the decayed snapshot's unscaled text score;
`legacy_raw_mass` reports its fixed reference mass. Existing phrase, syntax and
match diagnostics describe the compatibility observation tables;
`legacy_observation_score` is their old total. They are not the formula for the
new adaptive `score`. The old label weight only breaks equal final scores.

All policy and weight reads in a ranking share one SQLite snapshot and one time.
No ranking, summary, lookup or mode change constitutes author feedback. The
profile and comparison weights decay with the existing 30-day half-life. The
old integer snapshot retains its integer truncation, so its derived half-life
scores have small rounding differences. An old nonzero comparison has no
permanent priority over newer observations.

V1 and v2 share the adaptive model and history snapshot. Switching v1 to v2 leaves all
existing weights, timestamps, scores, examples and receipts intact; its trace
starts empty. First activation from legacy still uses the bounded reference
described above, so legacy integer scores are not preserved numerically.
Switching between the adaptive policies never restores a stale copy of the model.
Every actual transition into or out of v2 clears only the transient trace in
the policy transaction. Choices made while v2 is suspended cannot establish
continuity of an old opposition trace. Repeating v2 or `ensure-adaptive` does
not clear it. Policy changes do not create author events.

Switching back to `legacy` preserves the adaptive model and history snapshot.
Choices made while `legacy` is selected update only the compatibility layer and
normal exemplars. Switching to either adaptive policy again resumes its shared model after
decay; it does not backfill those intervening choices or refresh the old snapshot.
An unset direct legacy API caller retains scoring version 2 until a policy is
configured; the current Skill's setup/resume command chooses the new policy
unless an explicit setting already exists.

The existing `prune` command cleans compatibility counters and exemplars. It does
not delete `adaptive_weights`, the fixed legacy snapshot or its reference mass.
It also leaves `adaptive_opposition` alone. Adaptive values continue to decay when read or updated; never apply the old
integer `MIN_WEIGHT=1000` threshold to these bounded floating-point weights.

## Limits and recovery

These scores express learned preferences, not semantic correctness or a
probability that a sentence is good. Keep all candidates available and verify
meaning before ranking. Repeatedly using an expression does not identify why the
author likes it. A changed choice can revise preference; it is not a permanent
ban or required template.

The profile's total-mass constraint is a regularization choice. It can couple
different expressions in the same context; it does not prove broad linguistic
generalization. With fixed text and annotation boundaries, nonnegative weights
avoid rewarding known evidence merely being marked unknown. Removing an entire
annotation or changing its source can enable different fallback features and is
a separate case, not covered by that fixed-representation guarantee.
For adjunct position, a supplied unknown expression cannot resolve a competing
scope merely by losing its event slot: substantive overlap with the head remains
unresolved. Partly overlapping event/head anchors also abstain. These cases
neither reinforce a position in the profile nor reject an alternative position
in comparative learning. Unrelated unknowns and separator-only overlap do not
block valid attachment; accepted historical receipts are replayed as saved.

Schema 6 added adaptive state; schema 7 adds `adaptive_opposition` while preserving
all original examples, values and receipts. A schema-6 engine must refuse a
schema-7 store; switching policy is not a database downgrade.
Do not replay historical decisions with new IDs to populate the model. Never
edit an approved old payload to add new mode/analysis fields. The strategy is a
workspace setting, not part of the saved author's decision hash. When feedback
or receipt export is interrupted, retry the original file and ID as usual.

Runtime support requires `adaptive_preference_learning`, `local_opposition_learning`,
supported scoring versions 2, 3, 4 and 5, and policies `legacy`, `adaptive-v1`,
`adaptive-v2` and `adaptive-v3`. A configured syntax
backend `off` stays off; adaptive learning then uses its available phrase
features rather than silently loading a parser. Optional parser models are
never trained or downloaded by this strategy.
