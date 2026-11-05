# One source span per decision

A candidate round compares alternatives for the **same complete contiguous source
span**. If any option merges S001 with S002, every option includes both sentences'
content. A Light edit to S001 must append unchanged S002; Keep original is the
entire unchanged span. Otherwise choosing S001 alone falsely rejects features
from the untouched neighbor. Preserve citations, facts, scope and literal spacing.

Every new writing round declares a root object, including ordinary single-sentence
rounds (one ID). Use a saved round_id and the exact current source text:

```json
"edit_scope": {
  "sentence_ids": ["S001", "S002"],
  "before_text": "研究者测量了电压[1]。研究者记录了电流[2]。",
  "keep_original_id": "keep"
}
```

Required keys are sentence_ids and before_text; keep_original_id is optional at
the protocol boundary. IDs form a non-empty ordered list of unique non-empty
strings without surrounding whitespace. before_text is non-empty literal text,
including between-sentence whitespace. When present, keep_original_id names an
existing candidate whose text equals before_text exactly, regardless of label.
Unknown keys and per-candidate edit_scope are errors. Do not infer scope from
Light/Medium/Bold labels. The validator checks this declaration and baseline;
it cannot certify semantic completeness, ID adjacency or a filesystem location.

For example, a comparable pool is:

- Light: 研究者已测量电压[1]。研究者记录了电流[2]。
- Medium: 研究者对电压进行了测量[1]。研究者记录了电流[2]。
- Bold: 研究者测量了电压[1]，记录了电流[2]。
- Keep original: 研究者测量了电压[1]。研究者记录了电流[2]。

Explain the affected sentences to the author before asking. Bind analysis and
grammar against each **whole candidate**, including unchanged neighbors. Reuse
saved annotations for exact-text duplicates. Keep the shared learning_context
fixed. Do not expand across unrelated paragraphs just to improve ranking. If
new steering changes the span, prepare and present a new round ID/file. For
Other within the same span, retain every original alternative and its annotations;
incorporate the exact author wording into the full span before approval. A
one-candidate temporary Other binding draft can omit keep_original_id when that
candidate is absent; the final pool retains its original scope and Keep baseline.

## Apply, log and resume

The host edits files; the engine and MCP only bind, rank and record decisions.
Before author approval, save the file/paragraph locator and enough unchanged
surrounding context to distinguish repeated spans in revision-log or metadata.
Metadata is host guidance, not a validated source locator or part of the receipt
identity. Never infer a location merely because the chosen text occurs somewhere.

1. Persist the approved chosen/author_owned and any learning exclusion first.
2. At the saved location, compare the **whole scope and surrounding boundaries**.
   If it still equals before_text, replace that complete span with chosen.text.
   If it already equals the complete approved replacement with correct boundaries,
   leave it as applied. Keep also requires verifying the unchanged whole span.
   If partial, duplicated, externally changed or ambiguous, reconcile using the
   saved original/decision/context evidence; ask the author only when that cannot
   resolve the conflict. Presence of the merged string alone is insufficient:
   ensure the old S002 was not left behind. Do not globally delete matching text.
3. Call choice-file with the same saved file, round_id and learning_context,
   adding `--receipt-file` with a stable path in `choice-receipts/` (create the
   directory during workspace setup). A first receipt or replay completes feedback
   once, including opt-out choices. The file saves the original database result;
   it neither edits the manuscript nor completes this human-readable log.
4. Log one shared decision with ordered IDs, exact before/final text, locator,
   candidate path, round ID and exported receipt path using normal file edits. If S001+S002 became one sentence,
   S001 remains the lead edited ID; S002 is merged_into:S001 with that same round.
   If the option kept two sentences, keep both IDs with their actual kept/edited
   statuses linked to this one decision. Do not submit feedback per member ID.
5. Advance to the first still-pending original ID (S003 in the merge example).
   Do not renumber old S003 to S002 or re-review a consumed ID. If one original
   sentence is split, its stable ID still owns the complete replacement span;
   record output parts in that decision, not by stealing another source ID.

On recovery, reconcile text, receipt and log independently. A feedback receipt
alone does not prove the file edit/log finished. After a lost feedback response,
retry the original file/id and receipt path; after feedback but before logging,
inspect the saved receipt or replay/export that same decision, then finish the
shared log. A post-commit export error does not mean feedback rolled back. Preserve
any conflicting receipt file and choose a fresh output path if necessary, keeping
the decision ID unchanged. The export is checked against this database, not used
to import feedback into another store. No extra record/signal/store-file calls.

## Compatibility and limits

edit_scope is optional for historical inputs. When present it participates in
the immutable decision hash and both normal/editorial-only receipts; changing
IDs, their order, before_text or Keep reference under a committed ID conflicts.
Absent input hashes and old receipt bodies remain unchanged. This is a protocol
extension, not a database migration or a new syntax feature.

Never retrofit an already approved legacy payload with edit_scope or rebind its
annotations. For an interrupted legacy merge, use reliable saved scope notes,
original text and surrounding context to reconcile the entire span, then retry
the exact original approved file/id. Record the recovered scope as host evidence
in the log, without pretending it was an engine-validated scoped receipt. A
mixed-scope historical choice remains historically mixed; do not silently replay
a normalized pool. For completed legacy decisions without IDs, preserve the
existing evidence and do not create an ID merely to replay unprotected feedback.
