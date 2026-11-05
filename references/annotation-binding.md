# Binding semantic drafts to exact text

Markdown rounds add optional root `"text_format":"markdown"` before binding.
This is the only explicit format value; omit the key for legacy behavior.
Null, other values/types and candidate-level placement are errors. Binding
validates and preserves the root flag, while all evidence offsets/hashes still
refer to the exact original markup-bearing candidate. Do not attach the flag
to an approved old payload or infer it from emphasis-looking characters.

When an old Markdown pool is still genuinely unapproved, add the flag to a
copy of its draft and bind to a new canonical filename, retaining its round ID
and exact candidates/annotations. Keep the old artifact; no-overwrite still
applies. This cannot upgrade an already committed choice or evade a conflict.

For new rounds, write JSON with your chosen rhetorical labels, grammatical
frames and literal evidence fragments, then run `bind-file`. You supply the
meaning; the engine computes coordinates and hashes. It does not parse prose,
correct labels, open a database, load a model or make an author choice.

Use the same root fields as candidate files: a persisted `round_id`, `candidates`,
and optional `chosen`, `author_owned`, `learn_from_choice`, `learning_context`, `edit_scope`, or a `metadata` object. Candidate fields
are `id`, optional `label`, exact `text`, `analysis`, `grammar`, and optional
`relation_coverage` as described in [relation coverage](relation-learning.md). Both annotation
objects are required here: use `{"units":[]}` / `{"frames":[]}` to explicitly
abstain. Omitting a field or misspelling it is not a fallback. Legacy rank/choice
files without annotations remain supported; do not rebind completed decisions.

Replace this example's round ID with a fresh UUID for a new real decision:

<!-- sonder-binding-example -->
```json
{
  "round_id": "binding-example-use-a-fresh-id",
  "edit_scope": {"sentence_ids": ["S001"], "before_text": "信号稳定；信号减弱[1]。", "keep_original_id": "keep"},
  "candidates": [{
    "id": "keep",
    "label": "Keep original",
    "text": "信号稳定；信号减弱[1]。",
    "analysis": {"units": [
      {"parts": [{"text": "信号稳定"}], "move": "result", "polarity": "affirmative", "certainty": "asserted"},
      {"parts": [{"text": "信号减弱[1]"}], "move": "result", "polarity": "affirmative", "certainty": "asserted"}
    ]},
    "grammar": {"frames": [
      {"kind": "expression", "text": "信号稳定", "form": "adjectival", "voice": "nonagentive", "slots_complete": true,
       "slots": [{"role": "patient", "text": "信号"}, {"role": "event", "text": "稳定"}]},
      {"kind": "expression", "text": "信号减弱[1]", "form": "verbal", "voice": "nonagentive", "slots_complete": true,
       "slots": [{"role": "patient", "text": "信号"}, {"role": "event", "text": "减弱"}]}
    ]}
  }]
}
```

Save the draft with a file-writing tool, then call:

```bash
python3 -I "<learn-path>" bind-file "<draft-file>" "<candidate-file>"
```

The output is the normal validated candidate JSON used by rank-file/choice-file.
New rounds also declare root edit_scope; see [edit-scope.md](edit-scope.md).
The binder preserves source text and ordered IDs exactly, checks the optional
Keep baseline, and rejects malformed/misplaced scope before publishing. Every
alternative and its annotations cover the whole common span. Legacy absent
scope remains absent for exact retries; never retrofit an approved decision.
New writing drafts include root `learning_context:{"section":"results","role":"support"}`.
It is preserved exactly and must contain only two non-empty strings without
surrounding whitespace. Later file-based ranking/feedback calls must match both
values before any DB access. The binder does not infer or approve rhetorical
roles; historical drafts without context remain compatible for byte-exact retries.
An optional root `learn_from_choice` must be a JSON boolean and is preserved
exactly. Use false only for an explicit author instruction to exclude this
decision from learning. It does not approve a pending candidate or change its
authorship. Finalizing a false choice requires the persisted round ID.
Only missing protocol fields are filled: analysis version 2 for `units` (legacy
`spans` drafts still infer 1 byte-exactly), grammar version 6
when slots_complete is declared, otherwise version 5 for recipient slots, otherwise version 4 for comparison, version 3 for temporal, otherwise the
prior version 2 (preserving old draft retries),
source `writing-model`, whole-text SHA-256, and evidence start/end. Existing
values are checked, not silently replaced. Candidate text is never normalized.
In v6 every expression must explicitly supply boolean slots_complete; the binder
does not infer it from text coverage or default it to true. See grammar-frames.md
for role uncertainty; link frames reject this field. Old drafts retain their
original defaults and byte-exact retries.
All ordinary coverage, enum, slot, same-text consistency and metadata validation
must pass before a file is published. Keep semantic uncertainty as unknown or
explicit abstention; do not drop an invalid object to bypass validation.

Grammar drafts and canonical files share the same capacity: at most 64 frames,
with expression roles determined by their input version (three in v1, four in
v2–v4, five in v5–v6) and two roles for links. Roles remain unique and bound
slots cannot overlap. The five modern roles are agent, event, patient,
complement and recipient; their availability does not require inventing them.
`engine-info.annotation_limits` reports the current bounds. Above a bound,
retain the rejected draft and explain the limitation or explicitly abstain;
never truncate a known analysis silently. The analysis limit stays 16 parts.

## Repeated fragments

Analysis parts (and legacy spans) and frame spans search the whole candidate. Slots search only
their already-bound frame. Thus the second 信号 in the example is located inside
its second frame, rather than using the first global occurrence.

A single exact match is automatic. For multiple matches, specify `occurrence`
as a **1-based integer inside that search scope**, or supply the intended
absolute Unicode `start` (and optional matching `end`). Do not combine start
with occurrence. Overlapping matches count too. For two identical clauses,
their frame objects can use `"occurrence":1` and `"occurrence":2`; slots are then
resolved locally. The helper removes occurrence from the canonical output.
Missing, ambiguous, out-of-frame or stale evidence fails clearly.

Fragment errors identify their input location, for example
`candidates[2].grammar.frames[4].slots[0]` or
`candidates[1].analysis.units[0].parts[1]`, followed by the original reason.
Annotation validation errors identify the candidate and annotation; duplicate
frame kind/range errors also name both input frame indexes. These are zero-based
JSON array positions, while `occurrence` remains 1-based inside its search scope.
Use the location to inspect the intended evidence; it does not choose a meaning
or an occurrence for you. Rejected drafts still publish no output and never learn.

## Persistence and author feedback

For routine writing, `prepare-file DB SECTION ROLE DRAFT OUTPUT [--summary]`
combines binding and ranking through the same core. It validates the draft and
saved context, publishes the complete canonical, then ranks those same bound
candidates in memory. A conflicting old output or failed binding stops before
ranking or store access; it cannot silently rank the old file at that path.
The response has `preparation_version:1`, the normal receipt under `binding`,
and ranked rows under `candidates`. Summary rows remain output-only.

Publication and ranking are not one transaction: a rank/backend/store failure
can leave a valid canonical file, but the command returns failure, not a ranking.
Retry the same draft and output after resolving the reported problem; identical
bytes are reused. A failed bind with a stale existing file is different: preserve
that file and select a new output name. Do not infer success from file existence.
Neither preparation nor binding approves wording, sets a policy or records
feedback. Ranking may initialize/migrate the store just as `rank-file` does.

Use separate draft and output paths. The output's parent directory must exist.
The complete validated bytes are published without replacing an existing file;
an identical retry returns `reused:true` without changing bytes, mtime or round
ID. A different existing output is an error. Preserve it and choose a new output
path for revised proposals; do not generate a new round ID to bypass a committed
decision. The helper never generates IDs or writes feedback.

After a choice, update chosen/author_owned in the saved canonical file as usual.
Do not rerun the original draft over that now-approved file. For **new Other
text**, bind a separate one-candidate draft with the existing round ID, then add
that bound candidate to the original saved pool and select it; retain all old
alternatives. This can be a normal JSON edit, without a new binding script.
If this temporary draft carries edit_scope, keep its sentence_ids/before_text
but omit keep_original_id when the referenced Keep candidate is not in that
temporary file. Merge only the bound candidate back: the final original pool
retains its full edit_scope, including its Keep reference. Do not replace the
final scope with the temporary draft's scope or submit the one-candidate
temporary file as the author decision.
The merged pool already contains canonical candidates. Save it to a new full
candidate JSON file and use `rank-file` if a refreshed ranking is needed, then
record the approved choice from that full file. Do not rebind the merged pool
with `bind-file` or `prepare-file`: canonical relation_coverage contains bound
fingerprints that are deliberately not draft fields. Do not strip them to
force a second bind. `rank-file` and `choice-file` validate the complete canonical
envelope, preserving every original alternative, annotation and scope. An author
who already supplied a complete final Other sentence need not approve it again.
For an **exact-text Other match**, reuse the existing candidate's annotations
directly. Committed decision files remain immutable, and choice-file handles
retry safety. No tool can certify that the model's language judgment is correct.

## Multi-part units

Each v2 unit has move/polarity/certainty and non-empty parts with literal text.
Each part uses the same occurrence/start/end selectors described above. The
16-part total, global non-overlap, substantive-content coverage and exact-text
rules are validated before publication. Empty units abstains. Continuous units
have one part; use multiple parts only for one interrupted proposition, keeping
necessary conditions and negation scopes whole when uncertain. See
[structure-analysis.md](structure-analysis.md). Array order does not establish
argument priority. Do not supply arbitrary unit IDs.

## Distinct frames sharing evidence

Explicit grammar `version:7` permits two different predicates to retain the same
necessary enclosing range and shared literal participants. Each expression needs
its own event range; each link needs its own two-slot anchor pair. Duplicate or
unresolved anchors at that range still fail. See the grammar guide's shared
evidence examples. Keep all original slots and relations when they are supported;
do not delete a participant or add punctuation padding merely to avoid a clash.

Draft relation_coverage indexes still refer to the unsorted draft. The binder
sorts by position and anchor, remaps these references and binds fingerprints.
Different input orders produce the same canonical output. Omitted versions keep
historical inference, and v1–v6 keep their prior range collision rule. Do not
upgrade an approved payload in place or use a new version under its old receipt.
