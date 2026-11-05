# Runtime setup and diagnosis

The current runtime also requires `relation_coverage` and
`relative_preference_learning`, coverage input v1, relation features v1, and
`adaptive-v3` / scoring v5 support. Install both `relation_features.py` and
`relative_learning.py` with the rest of the Skill; a partial module copy is not
a supported update. Schema 9 upgrades preserve native history and do not replay
old decisions. Keep a backup before a real workspace upgrade; an older engine
must not open the newer schema.

Read this when configuring an interpreter/model/MCP server, diagnosing a runtime
mismatch or fallback, or updating the installation. Ordinary writing can use the
existing configuration after the main Skill's installation check.

## Interpreter and installation

Run this installation's `scripts/check_runtime.py` before creating/resuming a
paper workspace. It invokes its own engine in an isolated process without
opening the author database or downloading/loading a model. Require a compatible
result; use its absolute engine path. Do not mix files from unrelated installs.

Optional packages belong to a particular Python. If using a virtual environment,
use its absolute `bin/python` path in every checker and engine call. Do not depend
on activation surviving a new shell or resolve the symlink to the base Python.

The checker validates supported input/feature versions and current capabilities,
including schema 9, context-key version 2, `injective_context_keys`,
supported scoring versions 2/3/4/5, `adaptive_preference_learning`,
`local_opposition_learning`,
exemplar index 1, `choice_receipt_export` and integer
`receipt_export_version:1`. It does not certify model availability
or semantic labels. The executable protocol and error cases live in
[the maintenance contract](learning-engine-contract.md).

## Existing backend and actual fallback

Keep the paper's saved configuration, including an explicit `off`. In new rank
output inspect `syntax_requested_backend`, `syntax_backend` and
`syntax_fallback_reason`. Success may still mean `heuristic-fallback`; report
that accurately instead of claiming the requested parser ran.

CLI backend queries return plain text. Capture exit status and raw output before
parsing; do not assume every successful command returns JSON.

With structure enabled, new annotated rounds use analysis input/feature v2,
analysis topology v1, grammar input v6 and grammar feature v1. Legacy saved
inputs retain their own accepted version. With explicit grammar, including
unknown or empty frames, `heuristic_template_active:false` indicates that raw
heuristic templates are suppressed. Missing expected diagnostics may indicate a
stale runtime, not an absence of learned preference. Do not drop annotations to
bypass the mismatch.

New receipts record per-candidate fallback reasons in `syntax_fallbacks`. Mixed
effective base backends disable `base_syntax_negative_feedback`, so a parser
failure is not learned as rejection. Known selected features and other learning
layers retain their own rules; uniform heuristic fallback can still learn.
A replay returns the original receipt and diagnostics without testing the parser
again. For score components, uncertainty capacity and feature namespaces, consult
the maintenance contract only when investigating those behaviors.

## Optional local structure parser

Syntax configuration is independent of exemplar similarity:

```text
syntax-backend DB [heuristic|stanza|off] [MODEL_DIR] [MODEL_VERSION]
```

`heuristic` is the zero-dependency default/fallback; it supplies conservative
surface cues and a small academic adapter. `off` disables structure learning.
The writing model's evidence-bound annotations remain fallible; installing
Stanza is not required for the normal annotated workflow.

`stanza` uses an explicitly provisioned local `zh-hans` `default_fast` package.
For example, with the selected Python and absolute engine/database paths:

```text
syntax-backend DB stanza /path/to/stanza-models zh-hans-default_fast
```

The model directory needs tokenizer, POS, lemma and dependency resources plus
their declared pretrain dependencies. Dependency parsing needs lemma annotations
even though learning features do not save lemma values. Use the package resource
manifest when provisioning; NER and sentiment models are unnecessary. The adapter
never downloads during revision. Missing package/resources or a failed parse
falls back to heuristic with a reason, preserving phrase/example learning.

`syntax_model_fingerprint` identifies model contents and the Stanza version;
`syntax_model_version` is the human-readable label captured at loading. Moving or
retagging identical artifacts preserves model memory. Changed contents use a new
parser namespace even under the same label. New receipts record candidate model
identities; mixed identities disable base rejection. Unchanged cache hits avoid
rereading files; drift during loading causes an explained fallback. Maintain
models between calls. Legacy tag-only weights are preserved but not silently
assigned to a verified model; old receipts are never backfilled.

The academic adapter is not evidence that a general UD parser was trained for
this paper's domain. Inspect actual output when evaluating a model; a compatible
installation alone is insufficient.

## Optional exemplar matcher

```text
backend DB [ngram|sklearn|embed]
```

`ngram` is the default zero-dependency character 3-gram TF-IDF matcher. `sklearn`
uses optional scikit-learn TF-IDF; `embed` uses a pretrained sentence-transformer.
Only enable an optional matcher when the author wants it and its requirements
can be installed. Missing libraries fail with actionable instructions instead
of silently selecting another matcher. The separate embedding loader retains
its own optional model-download behavior; do not describe it as Stanza's local-only
adapter. None of these similarity scores verifies meaning.

## MCP transport

Use [the MCP guide](mcp.md) for supported tools and launch configuration. Before
using an exposed server, verify `sonder_status` reports this paper's canonical
workspace/database, the expected engine path and compatible capabilities.
Restart an old running server after installation changes; its cached module can
otherwise outlive an update on disk.

The same complete candidate files and author approval apply to CLI and MCP.
`sonder_record_choice` also receives the saved round ID explicitly. On a timeout,
reconnect or transport switch, inspect the result and retry the same decision/ID.
A new `instance_id` identifies a server restart, not another author confirmation.
MCP keeps optional model caches; it does not edit the manuscript or approve text.

## Updates and historical data

Use the memory-preserving installer described in README and keep a backup before
updating. Existing databases migrate on first open; schema 5 adds exemplar source
format and an independently versioned derived index; schema 6 added adaptive
profile/comparison state and a once-only legacy reference snapshot on activation.
Schema 7 adds local correction traces without changing old weights or receipts.
Schema 8 adds initially empty signed relative state; existing native rows remain.
Schema 9 marks the collision-free context-key format; it adds no tables and does
not rewrite old rows. Ordinary keys remain byte-identical. Restart existing MCP
processes after installing the complete update: schema-8 engines refuse a
schema-9 database, even if the saved learning policy is unchanged.
The main Skill uses `learning-policy DB ensure-adaptive` on setup/resume: v3 for
an unset workspace, otherwise its saved policy. Direct API callers stay legacy until configured.
Versioned upgrades preserve
author text, IDs, dates, rewards, weights and receipts. Old unknown Markdown mode
remains legacy/NULL. Only unversioned legacy stores require duplicate merging.

Do not recreate the database, replay old choices to backfill features, or guess
old modes from markup. Fresh confirmations can update the existing exact-text
example's mode/cache; replay and opt-out cannot. Old engines are not database
downgrade tools. For rollback, migration failure and future-version handling,
use the maintenance contract and a verified backup.

An `ambiguous legacy context` error means an old concatenated key could refer to
more than one section/role pair, and at least one context table still has rows
under that key. For example, `methods::detail` / `support` and `methods` /
`detail::support` used to alias; boundary colons such as `a:` / `b` also matter.
The engine preserves this history and refuses new ranking/learning for the
affected pairs. Other unambiguous contexts remain usable. Historical receipt
replay/export and an explicitly saved `learn_from_choice:false` choice remain
available; the latter records only an editorial receipt and must not be added
automatically to bypass an error. A failed prepare may already have published
its valid canonical file, but it has not produced a successful rank.

Keep the backup, exact pair and original candidate/receipt files. Do not clear
weights, rename roles, re-submit old choices or assign the whole ambiguous key
to one pair without evidence and explicit author intent. Mixed aggregate rows
cannot be safely split from their keys alone; there is no automatic migration
or resolution command for that history. `reshow` displays opaque storage keys,
whereas `learning_context` in candidate files and receipts keeps the original
pair. This guard changes storage safety, not score formulas or writing quality.

Rank results are recomputable views. If scoring changed or concurrent feedback
arrived, rerank a pending pool unchanged under its saved context; do not relearn
its choice. Approved payloads and original receipts remain immutable even when
new rankings differ.
