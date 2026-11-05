# Relation coverage in new writing rounds

Grammar frames describe what a predicate or relationship is. A relation inventory
also states whether all incoming relationships of a predicate have been reviewed.
Without that statement, missing links mean unknown rather than “no condition”.
This lets a learned preference depend on the actual target's context, such as
conditional versus unconditioned wording, instead of attaching one flag to a paragraph.

## Write and bind

Add `relation_coverage` to each new candidate alongside its grammar. Draft indexes
are **zero-based positions in that draft's grammar.frames**. The binder handles
canonical reordering and exact text/grammar hashes. Do not calculate those hashes
or rewrite the output references yourself.

```json
{
  "version": 1,
  "expressions": [
    {"frame": 0, "incoming_complete": true, "incoming_links": []},
    {"frame": 2, "incoming_complete": true, "incoming_links": [1]}
  ],
  "links": [
    {"frame": 1, "head_kind": "event", "head_expression": 2}
  ]
}
```

This fragment describes a grammar with expression 0, link 1 and expression 2.
Use the actual indexes of your draft, not these example numbers. It is not a full
candidate and does not choose or approve text.

- Review every identifiable predicate, including conditions, embedded events,
  negation and state/copular expressions. `incoming_links` lists relationships
  whose head really is this predicate. Mark `incoming_complete:true` only after
  checking that list; a known empty list means no incoming link in this protocol.
- Use `incoming_complete:false` or omit an expression declaration when its
  inventory is unresolved. Keep known links. `slots_complete` describes argument
  inventory and is not proof that incoming relationships are complete.
- An event target needs `head_kind:"event"` and `head_expression` pointing to a
  single exact event anchor. A shared condition may need two links with the same
  dependent and different event heads; keep both physical targets. Duplicate
  kind/range frames still fail normal grammar validation.
- A link to an explicit noun entity uses `head_kind:"non_event"` without
  `head_expression`. Its head must not overlap predicate evidence. If attachment
  is uncertain, use `head_kind:"unknown"`, keep possible predicates and do not
  claim their inventories are closed. Unknown form, voice or relation remains
  unknown even if the inventory declaration is present.
- Inbound relationships are those actually analyzed by the grammar protocol,
  not every semantic prerequisite, neighboring sentence or parameter. Do not
  infer an extra condition from a topic, vocabulary or method role.

The canonical result additionally contains `text_sha256` and `grammar_sha256`.
These bind the inventory to the complete canonical grammar and literal text.
Canonical indexes cannot be copied back as draft indexes after rearranging frames.
Keep the draft and canonical file separate. Preserve failed drafts and make a new
pending file when correcting evidence; never retrofit approved decisions.

## Feedback and uncertainty

The new layer is active with `adaptive-v3`, subject to the syntax backend. Explicit
`off` disables it. A supplied inventory is validated under every policy, and is
part of the decision identity under every policy. Changing it under an already
used round ID conflicts, including editorial-only decisions. No inventory on an
old input means no additional layer; it does not manufacture a `none` category.

Each predicate contributes to its confirmed incoming relation groups. Missing or
unresolved predicates reserve capacity in all possible groups and contribute
nothing themselves. Existing known local observations can still teach usage;
comparative feedback for this layer requires every distinct compared text to have
complete known inventories. A single author sentence provides usage evidence,
not an invented preference over alternatives. Identical text must have identical
grammar and coverage even when presented under another label or ID.

Internally the learner retains support and opposition as relative values, then
uses nonnegative group scores. Under fixed evidence boundaries, hiding known
fields as unknown cannot increase this layer's score. This does not validate
meaning: omitting a whole proposition or falsely claiming complete coverage can
still mislead the model. Preserve meaning and flag ambiguity before ranking.

These are soft contextual preferences. They do not ban a voice, require a template,
identify semantic correctness or promote ordinary choices across papers. A single
exception is not a standing instruction. Existing `learn_from_choice:false`
records an explicitly excluded decision without any learning.

Full rank rows retain canonical coverage; `--summary` omits it like grammar and
analysis, while reporting `relation_score`, `relation_feature_count` and
`relation_coverage_status`. Summary rows remain output-only. Actual technical
annotations and scores stay internal during ordinary author interaction.
