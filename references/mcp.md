# Optional local MCP companion

The existing tools also support v3 relation learning through the same core.
`sonder_bind` remaps draft `relation_coverage` indexes and binds fingerprints;
rank and record-choice validate the canonical field and include it in decision
identity. No separate sidecar memory service is needed. Current support requires
schema 9, context-key version 2, `injective_context_keys`, scoring 5,
`relation_coverage` and `relative_preference_learning`.
On an older saved workspace, ensure preserves its policy; select `adaptive-v3`
explicitly only when the author requests the upgrade.

Sonder can expose its existing learning engine through a local stdio MCP server.
This is useful with the optional Stanza backend: a server keeps the loaded model
in memory across requests. The engine, workspace database, scores and author
approval rules are the same as the CLI. It does not train a neural model or
improve the correctness of writing-model annotations by itself.

## Setup

Keep the ordinary Skill installation. In a separate Python 3.11+ environment,
install the optional pinned SDK (installation needs package access):

```bash
/absolute/venv/bin/python -m pip install -r /absolute/skills/sonder/scripts/requirements-mcp.txt
```

If using Stanza, use the environment where its dependencies are installed and
provision the model separately. Preserve the absolute `venv/bin/python` path;
resolving the executable symlink to base Python can lose that environment.

Create the normal revision workspace with the Skill first, including
`candidate-inputs/`. Launch one server per existing revision directory:

```bash
/absolute/venv/bin/python -B -I /absolute/skills/sonder/scripts/mcp_server.py --workspace /absolute/paper-revision
```

This process speaks MCP on stdin/stdout; a host normally launches it. A generic
host configuration looks like this (adapt the host's own configuration format):

```json
{
  "mcpServers": {
    "sonder-paper": {
      "command": "/absolute/venv/bin/python",
      "args": ["-B", "-I", "/absolute/skills/sonder/scripts/mcp_server.py", "--workspace", "/absolute/paper-revision"]
    }
  }
}
```

Setup does not change any host application settings automatically. There is no
HTTP listener, remote service or tool to switch workspaces/backends. Configure
optional backends using the normal CLI for this workspace. Default ngram and
heuristic paths require no models. Stanza inference uses preinstalled local
models without downloads; the separately selected legacy `embed` retrieval
backend retains its existing optional model-loading/download behavior.

## Calls and author decisions

First run the installation's `scripts/check_runtime.py` as instructed by the
Skill. Then call `sonder_status`. Match `workspace`, root `database` and
`engine_path` to this paper/install and require current capabilities, including
`choice_id_guard`, `grounded_grammar_precedence`, `text_first_ranking`,
`choice_learning_control`, `choice_context_guard` and `choice_edit_scope_guard`.
A server still reporting old capabilities after an update needs restarting.
The current installation also requires `grammar_binding_capacity` and reports
`annotation_limits` of 64 grammar frames, 16 analysis parts and five modern
expression slots. Check the running server's status, not only the engine file
on disk; an older process can retain the earlier binding limits.
The current checker also requires grammar input versions 1–6, including
comparative framing in v4, recipient slots in v5 and expression slot certainty in v6; old saved drafts retain their original versions.
Paths are canonical (macOS may report `/private/var` for
`/var`). Status does not open/create the database or load optional models. Its
`instance_id` distinguishes a restart, not a new author choice.

| Tool | Arguments | Existing engine operation |
|---|---|---|
| `sonder_status` | none | capabilities and backend configuration |
| `sonder_bind` | `draft_file`, `output_file` | `bind-file` |
| `sonder_prepare` | `section`, `role`, `draft_file`, `output_file`, optional `summary=false` | checked `prepare-file` |
| `sonder_nearest` | `sentence_file`, `limit` (1–500, default 3), optional `text_format:"markdown"` | `nearest-file` |
| `sonder_rank` | `section`, `role`, `candidate_file`, optional `summary=false` | `rank-file` (`--summary` when true) |
| `sonder_record_choice` | `section`, `role`, `candidate_file`, `round_id`, optional `receipt_file` | `choice-file` with expected-ID guard |
| `sonder_prune` | none | default `prune` at paragraph checkpoint |
| `sonder_learning_policy` | optional `action` | inspect policy, or `ensure-adaptive`, `legacy`, `adaptive-v1`, `adaptive-v2`, `adaptive-v3` through the shared core |

After verifying `sonder_status`, call
`sonder_learning_policy(action="ensure-adaptive")` on new or resumed workspaces.
It enables `adaptive-v3` only when unset, respects every saved policy, and
takes the old-weight reference snapshot only once. Unlike status, this tool can
initialize/upgrade the DB. It never approves a sentence or replays old feedback.
Query with no action; explicit `legacy`/`adaptive-v1`/`adaptive-v2`/`adaptive-v3` switches preserve saved
models and receipts. An actual transition into or out of v2 clears only the
transient opposition trace; repeating the setting does not reset it. Status
requires `local_opposition_learning` and supported scoring versions 2/3/4/5.
See [adaptive learning](adaptive-learning.md).

All paths may be relative to the bound workspace or absolute within it. File
resolution rejects escapes, including symlinks. `preference.db` stays at the
workspace root; sidecar configuration files must also stay in the workspace.
The configured model-directory *value* may point outside it. Binding output
must be a file below `candidate-inputs/`, with its parent already present.
Binding never overwrites different content. The workspace is trusted local
state, not an operating-system sandbox against concurrent local file tampering.

Use `sonder_prepare` for the normal draft-to-ranked-candidates step. Require
`checked_candidate_preparation` in the running server's capabilities; restart an
older process after updating. It returns `preparation_version:1`, the publication
receipt under `binding`, and the ranked rows under `candidates`. Bind failure,
context mismatch or conflicting output returns an error before ranking or DB
access, even when an old valid file exists at that path. The core ranks the same
bound in-memory candidates it published, not an unchecked filename read.

If ranking fails after publication, the complete canonical remains for an
identical retry; this is checked sequencing, not a file/DB/model transaction.
Preparation never selects wording, changes a policy or learns. Keep the canonical
file as the later choice input, and use `sonder_rank` for a later refresh without
rebinding. Manual bind/rank calls remain available, but a failed bind must stop
that manual sequence; old-file existence is not success.

Rank and nearest may initialize/migrate a database, so they are not advertised
as strictly read-only. Rank returns `{ "candidates": [...] }`; nearest returns
`{ "exemplars": [...] }`. These wrap the same CLI result arrays. Bind and choice
return their normal receipts. All successful tools return structured objects.

`consistent_rank_snapshot` applies to `sonder_rank` and the CLI alike: each
response scores every candidate from one committed preference snapshot, even
when another process writes feedback. Refresh ranking to observe later feedback;
do not replay a choice as a refresh. Model extraction happens before the SQLite
read transaction. This is a database-read guarantee, not a transaction over
the candidate file or model sidecars. A server's operation lock serializes only
its own calls; the engine snapshot also covers feedback from external processes.

Markdown candidate files save root `text_format:"markdown"` before binding.
`sonder_bind`, `sonder_rank` and `sonder_record_choice` share this saved mode;
there is no separate per-tool toggle. Verify `markdown_text_format` in status.
Absent format retains legacy behavior. Keep the complete envelope when making
a decision; ranking rows do not replace its source format/context/scope. New
normal and opt-out receipts retain an explicit format, and retries must not
add or remove it from an approved payload.

`sonder_rank` accepts `summary:true` for routine writing calls. This projects
the same computed ranking after validation and scoring: candidate text, order,
all scores, version fields and fallback diagnostics remain intact. It omits
the saved `analysis`/`grammar` payloads and the five matched-feature arrays,
adding `summary_version:1`, `omitted_fields` and `matched_feature_counts`.
Default false retains the complete historical response. The original saved
candidate file, never this summary, remains the input for a decision. Use the
full response when inspecting individual feature matches. Runtime capability
`compact_ranking` identifies support; no model work or feedback is skipped.
The required `rank_summary_input_guard` rejects any candidate row carrying
`summary_version`, `omitted_fields` or `matched_feature_counts` before store
access in rank and record-choice. A summary is never valid decision input,
including an opt-out or retry. Recover the complete saved candidate file;
do not strip markers, synthesize omitted evidence or rewrite old receipts.
Invalid requests return tool errors; legacy optional-matcher dependency failures
do not terminate the server. Prune advertises its deletion behavior.

Use the normal draft/binding workflow. Save one stable `round_id`, show the
candidates, and wait for the author's choice. Only then save `chosen` and
`author_owned`, apply the exact text in `working/`, and call, for example:

```json
{
  "section": "results",
  "role": "support",
  "candidate_file": "candidate-inputs/S001.json",
  "round_id": "the-id-already-saved-in-S001.json"
}
```

The explicit ID must equal the ID from the single validated file read. A
missing ID, mismatch or pending decision fails before DB access. The tool cannot
prove that a human approved the text; that remains the writing host's duty.

If the author explicitly excludes this decision from learning, persist
`learn_from_choice:false` in the same saved candidate file before the call.
It is a candidate-file field, not an additional MCP argument. Keep authorship
accurate. The usual record-choice call then returns `feedback_applied:false`
and `author_exemplar:false`, records only the editorial receipt, and does not
extract model/phrase features or update preference/exemplar rows. Ordinary
new calls report feedback_applied true; historical receipts may lack the field.
Missing choice/ID still fails, and a changed intent under an existing ID
conflicts. Preserve this flag across CLI switches and restarts. It is not an
undo of previously recorded learning or deletion of the manuscript.

New rounds also persist root `learning_context:{"section":"results","role":"support"}`
before binding/ranking. Pass these exact section/role values to both rank and
record-choice. Mismatch fails before DB access, including editorial-only choices.
Use the original saved context after a blueprint change; new receipts expose the
effective context for recovery. This adds no MCP argument and does not change
old files or backfill old receipts. Missing legacy context requires original
command/log evidence; do not guess it or use a new ID to bypass a conflict.

On timeout, cancellation, reconnect, or a switch to/from CLI, keep the same
saved file and ID. The first application returns `idempotent:true,replayed:false`;
a retry returns the original receipt with `replayed:true`. It does not increase
weights, rewards or timestamps. A changed decision under an existing ID fails;
do not assign another ID to bypass the conflict. A genuinely new author decision
gets its own ID. Keep receipts in the normal revision log. No MCP tool writes
manuscript files or global author preferences.

## Model cache and verification

Operations are serialized around the shared engine/model state. Current model
content-identity and invalidation checks still apply. Repeated requests can
reuse a loaded pipeline; restart loads it again on demand. Different models
cannot silently share parser preference weights. Do model maintenance between
revision requests, as for the CLI.

`tests/check_mcp_sdk.py` is the explicit optional integration gate. It connects
real SDK clients to real subprocesses with client caching disabled, tests
protocol negotiation, all tools, concurrent delivery, restart/CLI replay and
errors. `--model-dir` requires actual Stanza parsing and records latency;
`--server-root /absolute/installed/sonder` tests the delivered installation.
All test text/stores are synthetic. `--evidence` retains requests, receipts,
workspaces and timing in a `report.json`; a failed assertion exits nonzero.


The same six tools accept analysis input v2 units/parts. Verify status supports
analysis [1,2] and analysis_topology v1. New drafts bind units to exact slices;
rank exposes topology score/capacity and choice records its feedback gate. Mixed
v1/v2 candidates cannot reject topology missing from a legacy representation.
No extra tool, global memory or semantic inference is introduced.


Status must include `phrase_literal_guard`; the same lexical filter applies to
CLI and all MCP ranking/feedback. It needs no additional field or tool. The server
preserves exact scientific text in inputs and whole-sentence exemplars.

New writing rounds save a shared `edit_scope` for all candidates; merging two
sentences requires complete two-sentence alternatives, including Keep. The
existing bind/rank/record-choice commands validate the scope and preserve it in
new receipts. File editing and stable-ID recovery remain host responsibilities.
See [edit scope and recovery](edit-scope.md); no additional MCP tool is needed.

MCP status exposes code-default scoring_version:2 and independent_analysis_scoring;
it also advertises supported scoring versions 2/3 and adaptive policy support.
`sonder_rank` reports the actual policy's version; `analysis_score` remains an
observation diagnostic. In legacy scoring, base syntax, surface
analysis, grammar and topology have independent means, so the same rhetorical
evidence is not diluted by unrelated parser keys. New annotated ranks can differ
from saved v1 output, while old choices and receipts replay exactly. Refresh a
stale server after updating; no new tool or input protocol is introduced.


The runtime also requires `feedback_commit_time`. Core feedback captures one
instant after the writer lock and receipt recheck, then uses it for all learned
rows and an author exemplar. This also protects overlapping CLI callers outside
this server's serialization. Replays keep historical timestamps; parsing and
model work remain outside the write transaction.

## Exemplar format and derived index

Current status requires `exemplar_format`, `database_schema:9` and integer
`exemplar_index_version:1`. Restart old servers after updating. For segmented
Markdown sentence files, pass `text_format:"markdown"` to `sonder_nearest`;
omission keeps legacy interpretation. Choice uses its saved envelope format.
Matching removes supported formatting from the derived comparison view while
returning exact author text, including its original formatting.

A first query may migrate old exemplar caches. Old rows keep unknown format
as NULL/legacy; migration preserves original text, IDs, dates, rewards, weights
and receipts. A new confirmation can refresh mode/cache, but old replay and
opt-out cannot. Queries after migration do not reinforce memory.

## Durable choice receipt files

`sonder_record_choice` optionally takes `receipt_file`, a path inside this
workspace's `choice-receipts/` directory; the parent must already exist. Require
`choice_receipt_export` and `receipt_export_version:1` in the running status.
The tool finalizes/replays the saved choice and atomically exports the original
DB event. Its usual response gains `receipt_file` and `receipt_file_reused`;
omission preserves the previous response and does not write a receipt file.

The canonical file contains version, round ID, payload hash, creation time and
`result` exactly as originally stored. A replay response may say `replayed:true`
while the file retains the original result. The file must match this DB; it cannot
be used to infer or import missing feedback. Conflicting files are preserved.
Publication/flush can fail after feedback committed. Keep the original saved
decision and ID for retries; a new empty receipt path can resolve an output-file
conflict but cannot bypass a decision conflict. This operation does not edit the
manuscript or human-readable revision log.

## Shared evidence input support

Check `coextensive_grammar_frames` and grammar input 7 in `sonder_status` before
preparing new v7 drafts. These allow distinct event/link anchors to share the
necessary enclosing evidence while retaining shared arguments and separate
coverage targets. `sonder_prepare`, bind, rank and record-choice all use the same
core validation. No transport schema or extra tool is needed. Version 1–6 inputs
and receipts preserve their prior identity; never rewrite old decisions to v7.
Restart an older running server after installation to acquire this capability.
