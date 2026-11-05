# Grounded structure analysis

Use the writing model already revising the manuscript to identify what each
clause does. No additional service or downloaded model is involved. These are
model interpretations, not verified grammar or probabilities. Author approval
of the sentence does not certify its labels. The core validates evidence and
learns soft preferences; it cannot establish that a semantic label is correct.

## When to analyze

After generating the real candidates, analyze **all** of them, including Keep
original. Read the surrounding paragraph, but ground every span in the exact
candidate text. Do this before ranking or asking the author. After an author
supplies new Other wording, analyze that exact new sentence afresh. When its
text exactly matches a saved candidate in the current round, reuse that
candidate's saved analysis and grammar instead of generating a conflicting
second interpretation. Changed text must never inherit another sentence's
annotations. Do not revise approved prose just to fit a label.

The author sees ordinary Chinese/English proposals, not this internal schema.
Persist analysis inside the candidate JSON. Do not regenerate analysis on a
retry or while reconciling an already committed decision. Existing completed
decisions without analysis remain unchanged.

## Meaning of labels

Choose the function of a clause from its meaning, not from a substring match.
For example, “可见光” is a technical noun, not evidence of a conclusion, and
“未发现差异” is still an empirical result, with negative polarity. A method
phrase can be implicit, such as “逐点扫描记录响应”; it needs no fixed cue word.

| move | Meaning |
|---|---|
| background | Established context, prior knowledge, definitions or stipulated assumptions |
| aim | Research question, objective or intended investigation |
| method | How the work is organized: procedure, measurement, derivation, analysis or operational choice, whether planned, prescribed or performed |
| result | Reported empirical/computational observation or estimate |
| interpretation | Explanation or inference about what observations mean |
| comparison | Explicit comparison of alternatives or conditions |
| contrast | Opposing propositions or a reversal in the argument |
| concession | Acknowledged counterpoint before the main proposition |
| condition | A prerequisite or conditional circumstance |
| limitation | Boundary on applicability, evidence or what is established |
| conclusion | A synthesized takeaway from the argument |
| unknown | Ambiguous, inseparable, or unsupported classification |

Classify the **argument function**, not execution status. An aim states the
question or objective pursued; a method describes how work is to be or was
carried out. In a paragraph explicitly giving an acquisition schedule,
“团队决定继续采样” can record a procedural choice even when the additional
sampling has not begun. Its method label does not assert that sampling occurred.
In a paragraph asking whether longer sampling reduces bias, the investigation's
stated question is aim. Future tense or 决定 alone does not decide either label.

Read the complete local paragraph and its established role before making this
judgment. Section/role identifies the learning context, not a gold label for
every unit: a methods paragraph can contain a result, condition, aim or
limitation. When a provided fragment leaves its function unresolved, use unknown
rather than reconstruct missing context or force a plan into aim merely because
it is not yet performed. A mixed or ambiguous function can still remain unknown.

Keep the proposition's scope intact. “团队已决定继续采样” asserts a decision,
not completed sampling; “控制器允许执行器启动” states permission, not occurrence.
An asserted plan/decision/rule may have asserted certainty **as that plan or
rule**, without asserting its embedded action as a realized result. Keep modality,
negation and time in the relevant unit rather than splitting the embedded action
into a standalone result. The same method label on two sentences does not license
copying an exemplar's completed-action wording into a prospective paragraph.

A number by itself is not evidence of a result. Distinguish **stipulated**
definitions/values from **obtained** observations or estimates using the
surrounding manuscript. A declared symbol or assumption belongs to background;
setting a parameter as a procedural step can be method; reporting a measured
or computed outcome is result. If the text does not establish the provenance,
use unknown rather than infer an experiment from “value is ...”. Likewise,
quoted variable names or labels do not carry assertion strength by themselves.

Identical wording in different paragraphs or new rounds can legitimately have
different functions: “响应时间为 5 ms” may be a stipulated definition or a
measured outcome; “We confirmed stability” may report a result or a verification
step in an operational sequence. Establish that distinction from the actual
paragraph, not from the verb or number. Within a saved round, keep identical-text
annotations identical and preserve the original annotations on replay; do not
reinterpret a committed decision using newly supplied context.

`polarity` is `affirmative`, `negative`, or `unknown`. It describes the
proposition, not whether the author liked it. `certainty` is `asserted`,
`hedged`, or `unknown`: “可能源于” is hedged; a reported estimate can be asserted
as an estimate without becoming a definitive physical value. Preserve source
qualifiers, negation, comparisons, units, math and citations in every proposal.

Use the smallest non-overlapping units that carry a meaningful
argument function, often clauses or a method adjunct followed by a result.
Keep the predicate and its modifiers with the proposition they qualify. A
concession or condition can precede a separately labeled main clause. Do not
split isolated keyword fragments just to obtain desired labels. If functions
cannot be separated reliably, label the unit `unknown` rather than force one.


### Sources and settings versus procedural steps

For this compact rhetorical protocol, distinguish the proposition being made
from a modifier locating its evidence. A study source, setting, or design
description stays with the result or other proposition it qualifies; do not
create a separate method unit merely because that modifier names an experiment
or measurement. For example, “在独立复现实验中，振荡幅度减小” is one result
with its study scope. In “在经校核的光谱中，出现了两个峰”, the qualified source
also stays with the result; the grammar still represents the embedded checking
predication and its relative relation where identifiable. Keeping rhetorical
scope together is not permission to drop those grammatical observations.

Separate a method unit when the text itself describes an operation, means,
analysis, or procedural decision used to carry out the work. It need not be an
independent finite clause or have an explicit actor: in “通过逐点扫描，研究者
测得共振频率”, the scan is an expressed method and the measured frequency a
result. Likewise retain actual ordered operations and their participants. Judge
what is asserted in context, not a list of prepositions, technical nouns, or
section names.

This convention does not absorb an actual condition, concession, negation or
evidential limit into an unqualified result. Preserve those scopes and every
source modifier in the exact text. When a modifier's function or separability
is genuinely unresolved, keep that material and use the applicable unknown
judgment rather than inventing an independent procedure. It does not change
grammar form/voice labels, require rewriting prose, or authorize reanalysis of
any approved old decision.

## Conserved propositions across a round

Before binding or ranking, compare how the alternatives represent propositions
whose meaning was preserved. A sentence break, colon or new conditional wrapper
does not by itself change a proposition's polarity or certainty. Check the
boundaries and labels together: preserving the words in the candidate is not
enough if its analysis hides a negative or hedged action inside an unqualified
affirmative unit while other candidates retain that action separately.

For example, both of these can describe the same handling rule:

- “操作员将不满足条件的时段标记为缺失，而不直接删除对应记录。”
- “若某时段不满足条件，操作员将该时段标记为缺失，而不直接删除对应记录。”

In both, retain the positive marking step and the negative direct-deletion
constraint in the analysis. They may be separate method units when their shared
restriction and referents remain clear in context; do not reinterpret the second
unit as an unconditional claim about every record. If their shared or nested scope
cannot be separated faithfully with the available parts, retain the scoped unit
and use unknown for its mixed polarity. Do not silently make the whole rule
affirmative solely because it starts with “若”. The same check applies to an
unchanged possibility or evidential limit that becomes embedded in a recast clause.

This is semantic alignment, not a request for identical unit counts or labels.
Different wording may genuinely change rhetorical organization, antecedent scope,
or the order of the same propositions; represent those differences. A negated
condition does not make its entire conditional rule negative, and a noun or quoted
label containing a negative word is not a negative proposition. Do not split by
keywords, copy annotations onto changed text, or use the resulting rank to decide
which interpretation is preferable.

Perform this check while preparing a new round. A discovered inconsistency in
an already approved pool is diagnostic evidence; preserve its input, decision
identity and receipt rather than relabeling it or replaying it as new training.

## Exact input

New rounds use **analysis input v2** for every alternative, including continuous
units. Read [annotation-binding.md](annotation-binding.md) and write literal
fragments; `bind-file` supplies exact coordinates and hashes, not semantics.
Each `analysis` has exactly `version:2`, `source:writing-model`, `text_sha256`
(SHA-256 of the entire UTF-8 candidate) and `units`.

Each unit has exactly `move`, `polarity`, `certainty`, `parts`. Parts are
non-empty, each with exactly `start`, `end`, `text` in bound input. Offsets are
Python Unicode codepoints, half-open [start,end). All parts across all units
must be disjoint, match exact source slices and cover substantive content;
only whitespace and separator punctuation may be gaps. At most 16 total parts
and 16 units are allowed. Empty `units` is explicit abstention. Unknown labels
retain uncertain content. Include formulas, citations and parentheses in the
relevant evidence. Parts are canonicalized by start, units by first part;
no arbitrary unit IDs are supplied. Identical text needs identical annotations.

Use one part for a continuous unit, multiple parts only when the same
proposition is interrupted. In “研究者通过逐点扫描测得平均响应。” the result
unit has parts “研究者” and “测得平均响应”; “通过逐点扫描” is a method unit.
This is one result interrupted by a method, not two independent results.
Keep necessary conditions, negation and uncertain nested scope together when
splitting would imply an unsupported standalone assertion. Never concatenate
parts into new facts or store parts as separate author exemplars. A unit's
first part does not establish logical or causal priority.

Legacy input v1 instead has `spans`, each with `start`, `end`, `text`, `move`,
`polarity`, `certainty`. Preserve those saved files and decisions. The binder
infers v2 only from `units`; old `spans` drafts still infer v1 byte-exactly.
Do not regenerate labels for retries, or silently drop invalid annotations.

This executable canonical-input example illustrates binding, not classification;
ordinary writing uses the declarative binder instead:

<!-- sonder-analysis-example -->
```python
import hashlib
import json

item = {"id": "a", "label": "Light",
        "text": "逐点扫描记录响应，中位数为 7 μm[2]。"}
parts = [("逐点扫描记录响应", "method", "affirmative", "asserted"),
         ("中位数为 7 μm[2]", "result", "affirmative", "asserted")]
units = []
cursor = 0
for fragment, move, polarity, certainty in parts:
    start = item["text"].index(fragment, cursor)
    cursor = start + len(fragment)
    units.append({"parts": [{"start": start, "end": cursor, "text": fragment}],
                  "move": move, "polarity": polarity, "certainty": certainty})
item["analysis"] = {"version": 2, "source": "writing-model",
                    "text_sha256": hashlib.sha256(item["text"].encode("utf-8")).hexdigest(),
                    "units": units}
print(json.dumps(item, ensure_ascii=False))
```

Keep the round's saved UUID and normal author choice/receipt handling. Save
manuscript strings with a file tool; never interpolate them into a shell command.

## Learning and inspection

Analysis features use `syntax:analysis-v2:writing-model:` in `syntax_prefs`,
independent of the heuristic/parser `syntax:v3` keys. Features include action
position among the normalized action units, action/polarity/certainty signatures, adjacent known
actions, and the full action order when no action is unknown. Unknown spans
remain barriers; they are not removed to join actions on either side.

Uncertain attributes also make some normalized boundaries uncertain. The
engine retains only features that survive every compatible way those runs
could merge. For example, hiding certainty in one of two equal method spans
must not invent a new middle method or a repeated method sequence. Known
actions whose positions remain certain still contribute; uncertainty does not
disable the whole sentence's analysis.

For partial inputs, `analysis_feature_capacity` reserves zero contributions
for potentially missing features instead of shrinking the mean denominator.
With fixed text, spans, grammar, other sources, weights and time, replacing an
attribute with unknown cannot increase the candidate score. Complete inputs
retain their feature sets; numerical comparisons use the same scoring version. This guarantee does
not cover deleting spans, choosing an entirely different segmentation or
changing the underlying sentence.

For feature extraction only, consecutive spans with the same fully known
move/polarity/certainty are one action unit. Splitting a sequence of method
steps into more evidence spans therefore does not weaken the same method-to-
result preference. A change in polarity or assertion strength, or any unknown
attribute, keeps the boundary. The raw spans and candidate text are never
rewritten; their exact evidence still participates in the immutable decision
hash. This coarse layer deliberately does not learn the number of adjacent
same-kind steps; base syntax/punctuation features retain other distinctions.

Both input versions reuse coarse `analysis-v2` features. Old
analysis-v1 rows are preserved but not interpreted as v2 weights. New confirmed
decisions teach v2. Never replay old decisions under new IDs to backfill it;
existing IDs return their original receipt without extraction or new learning.

Providing analysis replaces the keyword-based `academic` source for that
candidate, including when explicitly abstaining. Other surface/POS/dependency
features and phrase memory remain available. `syntax-backend off` disables all
structure signals, including analysis. Leaving analysis absent preserves the
old heuristic/local-parser behavior and old decision fingerprints.

Accepted annotated features receive the existing context-scoped, decaying
reward. Negative analysis feedback is enabled only when **every candidate** has
non-empty analysis and no unknown move/polarity/certainty. Missing information
is not an author's rejection. An explicitly annotated choice also does not
penalize the legacy academic features it deliberately did not extract.

`rank-file` exposes `analysis_source`, `analysis_feature_version`, `analysis_feature_capacity`, `analysis_status` (`absent`, `abstained`,
`partial`, `complete`, `disabled`) and `matched_analysis_features`; these also
appear inside the combined syntax diagnostics. `choice-file` additionally
reports `analysis_features` and `analysis_negative_feedback`. Old replayed
receipts retain their original fields. These diagnostics
describe input coverage and feedback, **not semantic accuracy**. Full analysis
is part of the immutable decision hash. Changing it under a committed round ID
is a conflict, even if the candidate prose is unchanged.

This first version represents rhetorical order and assertion shape. It is not
a complete syntactic tree or a trained academic parser. Evaluate its meaning
with independent writing trials and unfamiliar phrasing, separately from the
engine's validation and ranking tests.

## Unit identity and surface topology

V2 traces unit occurrences in source order, coalescing adjacent parts of the
same unit. This surface trace feeds the coarse features above. The separate
`syntax:analysis-topology-v1:writing-model:` namespace retains unit identity:
`pair:result>method:ABA` means a result starts, a method intervenes, and the same
result resumes. Two independent result units have different pair traces.
For each pair, letters are assigned by first appearance; repeated letters
collapse after other units are filtered out. A fully known pair also supplies
its move/polarity/certainty signature. These are surface organization features,
not a semantic dependency graph or proof of causal order.

Topology has a separate additive mean within the syntax scale. Untrained keys
do not dilute older coarse preferences. Complete capacity equals the unique
feature count; partial labels reserve two possible keys per unordered unit
pair. Unknown move suppresses that pair's keys; unknown polarity/certainty
suppresses the full signature. On fixed units/parts, masking cannot invent
keys or improve scores. Reordering arrays or harmless subdivision within the
same unit preserves features/scores. Changing unit identity is a different
interpretation and need not be equivalent.

Topology rejection additionally requires every candidate to have complete v2
analysis. Missing topology in v1 cannot become negative evidence. Known chosen
features can still receive positive feedback. Whole sentences remain exemplars;
fragments do not become independent author statements.

New ranks report `analysis_input_version`, `analysis_topology_score`,
`analysis_topology_capacity`, `analysis_topology_feature_version` and
`matched_analysis_topology_features`. New ordinary receipts report the input
version, topology feature count/capacity/version and
`analysis_topology_negative_feedback`. Total `analysis_feature_capacity`
includes surface and topology capacity, though their means are independent.
V1 has zero topology. Backend off disables both. Old receipts remain exact.

## Independent scoring (version 2)

The runtime code default and legacy rank rows report scoring_version:2. Adaptive
rank rows report version 3 and the separate profile/comparison score described in
[adaptive learning](adaptive-learning.md); their compatibility analysis diagnostics
remain available. In the version-2 observation formula, surface analysis
has its own mean: its matched weights divided by surface capacity (total
analysis_feature_capacity minus analysis_topology_capacity). It is exposed as
analysis_score. Base parser/fallback keys have a separate base_syntax_score;
grammar and topology retain their existing independent means. The four components
sum to syntax_score before the unchanged syntax scale is applied.

This removes cross-family dilution: two candidates with identical analysis
features/capacity must get the same analysis contribution even if one has more
untrained raw syntax keys. Masking unknown attributes still retains analysis
capacity and cannot improve that component. off still disables all structure.
No analysis keys, input versions, stored weights, feedback or old receipts are
rewritten. Older snapshots used a combined base/analysis mean and may have
different numbers/order; absence of scoring_version identifies the old output.
This fixes score normalization, not the unresolved meaning of overlapping moves.
