# Learning grammatical constructions

Use the writing model already reading the paper to analyze **how** a thought
is expressed, in addition to the rhetorical `analysis` of what it does. Attach
optional `grammar` to every candidate before ranking, including Keep original.
Analyze new Other text afresh. If it exactly matches a saved candidate in this
round, reuse that text's saved annotations, including unknown/absent evidence.
Keep all annotations internal;
authors choose ordinary prose, not linguistic labels. Do not alter a sentence
to make its annotation easier. Missing evidence can be unknown or abstained.

This is a small, grounded construction representation, not a full dependency
tree or a grammar checker. A label is a model judgment. The core verifies text
binding and learns preferences; it cannot verify that the judgment is right.
Do not infer a preferred form from a high score and then relabel candidates to
match it. Generate and analyze the real options before consulting their rank.

Explicit `grammar`, including unknown frames or whole-sentence abstention,
replaces the fallback's raw character-based `heuristic_template`. For example,
the 并 inside 归并 must not compete as a guessed conjunction with a grounded
verbal event. Clause length and punctuation remain separate style cues; actual
Stanza POS/dependency features and rhetorical analysis also remain available.
This does not make the model annotation infallible. It avoids filling an
explicit abstention with a less grounded character-template guess.

Rank and new feedback receipts report `heuristic_template_active`. It is false
for annotated candidates, the off backend, and an actual parser; true for
unannotated heuristic/fallback candidates. Old inputs without grammar still use
their surface templates. Stored template weights are preserved, and selecting
an annotated candidate cannot penalize a legacy alternative merely because that
source was suppressed. Old receipts replay unchanged, without adding this field.

## Expression frames

An expression frame describes a content event or state and its participants.
Its span usually covers the clause containing that predication. Use:

- `form`: `verbal`, `nominalized`, `adjectival`, `copular`, or `unknown`. “团队分析误差” expresses the
  event verbally; “团队完成了对误差的分析” packages it as a noun with a support
  verb. Do not classify every technical noun as a nominalized event.
  `adjectival` covers a direct property predicate, such as “样本稳定”. `copular`
  covers an explicit linking/equative predication, such as “样本是稳定的” or
  “厚度为 86 nm”. A state described by a content verb, such as “信号保持稳定”,
  can still be verbal; do not relabel every state as adjectival. Quoted names
  and isolated words do not determine a form. Genuine ambiguity stays unknown.
- `voice`: `active`, `passive`, `nonagentive`, or `unknown`. Judge participants
  and predicate function, not whether a word such as 被/by occurs. A quotation
  or variable name containing such a word is not a passive construction.
  `nonagentive` covers a confidently identified expression of a state/change
  without an acting agent; use unknown when these distinctions do not apply
  reliably. Preserve hedging/negation in the separate rhetorical analysis.
- `slots`: exact non-overlapping text for any explicitly present `agent`,
  `event`, `patient`, `complement`, or `recipient` (input v5). `event` is the content predicate/head: 分析 in both examples,
  rather than the support verb 完成. It is not necessarily the root of a UD
  dependency tree. `patient` is the affected/evaluated entity or content; it
  can be a phrase containing a relative clause. Do not invent an omitted agent.
  For a property predicate, event is its predicate core (稳定); for a copular
  predicate, event is the linking expression (是/为) and complement is the
  predicative description, identity or value (稳定的/86 nm). The evaluated
  entity belongs in patient, not an invented agent. Complement may also mark
  an explicit relational/predicative complement when needed, with an exact
  disjoint span. Do not extract an event merely because a quotation contains
  是, 被 or a property word.

New writing rounds use v6, with `slots_complete` required on every expression.
This boolean describes whether the supported role assignments, boundaries and
inventory for **that frame** are resolved. True does not mean that every possible
semantic relation is represented. Implicit/unstated arguments stay absent, and
ordinary modifiers need not be invented as arguments. False means some role,
its boundary or the inventory remains uncertain; retain the known slots and
form/voice. Do not infer true merely from valid spans or full text coverage.
Link frames cannot carry this field. It is a writing-model judgment, not a
validator's semantic completeness certificate.

For “操作者确认安全”, the form may be verbal and voice active while 安全 is
uncertain as a nominal content or selected state. Mark that outer expression
`slots_complete:false` and retain known agent/event. If the uncertainty is
whether 安全 contains a predication at all, do not also invent an unknown inner
expression and selection link for that possibility. The full outer text and
false inventory retain the unresolved material. Do not teach agent>event as a full argument shape simply
because the unresolved content was omitted. In “仪器启动了”, a confidently
identified nonagentive change with patient 仪器/event 启动 can be complete;
an unstated agent does not by itself require false. These are examples of
judgment, not cue-word rules; keep genuine uncertainty.

A false frame contributes known form/voice features, but neither
`expression_slots` nor combined `expression`. Each affected kind retains one
zero-evidence contribution, also when another field is unknown; it does not
shrink the divisor or invent a shorter matching template. Other resolved frames
still contribute. Overall grammar status is partial, following the existing
conservative rule that any partial grammar disables ordinary grammar negative
feedback for that choice. Known positive evidence still learns. The independent
placement gate described below still blocks an unresolved role inventory; its
only exception to full grammar certainty is voice. Old weights/receipts are not
reinterpreted; silently missing roles in an old or falsely declared complete
annotation cannot be recovered automatically.

Known forms require an event slot; copular additionally requires complement.
A frame with unknown form/voice and empty
slots can cover a clause you cannot analyze. Analyze separable content events
as their own frames, without duplicating the same event to amplify its score.

### Relational references and affected objects

For a clearly understood relational predication, `patient` is the entity being
described or evaluated; use the existing `complement` for an explicit reference,
limit, or whole selected by that predicate. For example, “温差超过允许范围”
has patient 温差, event 超过, and complement 允许范围. In “偏差趋近零值”,
零值 is the reference complement. Inside “负载占总容量的比例”, the inner
event 占 selects 总容量 as its whole/reference; the relative link separately
connects the predication to 比例. These roles describe this compact protocol;
they need not match the object/complement terminology of a full dependency
parser. A recognized reference does not become unknown merely because its
syntactic object label would differ in another annotation system.

Judge the actual relationship, not the noun or verb in isolation. In
“研究者调整基准”, 基准 is the affected patient, not a reference complement.
A comparison modifier such as “相对于初始状态” follows the comparison-link
contract below; a selected reference inside a content predicate does not by
itself add that link. Quoted labels do not instantiate their quoted relations.
Keep inequality direction, denominators, negation and scope in the exact prose;
these role names do not validate those meanings.

Bind only participants explicitly present inside the frame, in their actual
positions. In the reduced relative “偏离均值的读数”, an inner frame 偏离均值
can bind event 偏离 and complement 均值 without inventing a patient from the
external head 读数; represent the relative link separately. An omitted entity
alone does not make the explicit reference uncertain. If the relationship,
attachment or supported role inventory is genuinely unresolved, retain known
slots and `slots_complete:false` as before. Do not guess a reference to make a
frame complete, force every relational expression into one shape, or revise
old annotations/receipts using this clarification.

### Predications inside other clauses

Use a consistent unit of analysis: an independently predicating event or state,
including one embedded inside a complement, condition, or relative. Before
ranking, review each link's dependent and each expression's complement for
such a predication. If present, represent its predicate and explicit
participants in an expression frame as well. The link records how clauses
relate; it does not replace the expression inside the dependent. An enclosing
frame covering all characters is not evidence that every predicate was analyzed.

An expression frame describes grammatical form, not a claim that its content
is true. A predicate still has a form when it is being denied, hypothesized,
questioned, or discussed as an unproven possibility. Keep the outer qualifier,
negation and relation in the text and rhetorical analysis; do not turn the
embedded content into an independently asserted result. If the inner form or
voice is uncertain, mark that local frame unknown rather than silently omitting
the predication or forcing a category.

Distinguish **uncertain analysis of an evidenced predication** from **uncertain
existence of another predication**. In “团队尚不能证实信号稳定”, 信号稳定 is an
identifiable predication even though the author denies having established it;
keep the inner expression and its relation. In “团队决定继续采样”, the selected
sampling event is identifiable without an explicit inner subject; omitted
participants do not justify dropping it or inventing a subject. If a known
predication's form/voice or an evidenced relation's type is unclear, retain the
appropriate local unknown frame.

By contrast, bare content such as 安全 in 确认安全 may leave unresolved whether
the complement is nominal content or an elided state. When that is the actual
uncertainty, retain the outer expression, known roles and slots_complete:false;
do not add a speculative child expression/link merely to enumerate a possible
parse. A noun-like word alone does not establish another predicate. If context
resolves the interpretation, annotate that evidence normally. This is a compact
representation convention, not a requirement to classify every bare content
word the same way or a claim that one linguistic analysis is uniquely correct.

The convention does not permit dropping unsupported whole clauses: if no
established outer expression covers an unanalyzable clause, keep its unknown
coverage frame or abstain. Do not delete existing unknown frames to raise a
score, and never rewrite historical annotations under a committed round ID.

Do not expand every dependent into an event. A quoted name, numerical value,
ordinary noun phrase or parameter label may contain no predication at all.
Keep those as slots, without inventing an event from a suggestive word. Do not
duplicate a single predicate as both a parent and child merely to add evidence.
This review checks annotation coverage; it is not a complete parse or a truth
checker, and ambiguous structures may remain unresolved.

### Phase of one activity

Use a stable level of detail for this compact representation. When an expression
only locates the onset, continuation or cessation of the same activity, without
asserting a separate participant relation, keep one expression for that activity.
In “团队开始分析误差”, event is 分析, agent 团队 and patient 误差; 开始 stays
in the expression's exact text. The same projection applies to continuing or
stopping the team's own analysis. Do not add a selecting event and complement
link solely because a phase expression accompanies the activity predicate.
Keep phase, negation and their scope in the full text and rhetorical analysis:
“尚未开始分析” must never become an assertion that analysis occurred.

This does not collapse independently asserted decisions, requests or external
interventions. “团队要求操作者继续记录位移” retains the request and the operator's
selected recording activity; 继续 modifies the latter. If a controller stops
another module's transmission, keep the stopping action separate from that
module's activity. Never transfer the outer actor to the inner activity merely
to obtain a shorter frame. Conversely, in “试验停止了” or “试验仍在继续”,
停止/继续 is itself the expressed predicate; do not erase it or invent another
activity. Judge the usage and context, not a list of phase words. Preserve genuine
uncertainty using the existing inventory/unknown rules. This convention applies
to new annotations; approved historical payloads remain unchanged.

### Selecting another predication

Use a consistent constituent boundary when a predicate selects another event
or state. Put the **whole selected predication, including its explicit
participants**, in the parent's `complement` slot. Represent that predication
as its own expression and add a `complement` link from the selecting predicate
to the same full dependent. This applies to fronted complements as well as
ones following the selecting predicate.

An identified selected activity or state can also be nominalized. For example,
when “操作者的采样” denotes the activity being stopped in “负责人叫停了操作者的采样”,
use the complete activity as the outer complement and retain its inner event
and explicit participant. A concrete object such as “操作者的记录文件” remains
a patient when deleted; its name does not establish an internal recording event.
Do not choose patient versus complement solely from noun-like surface form.

For example, in “团队要求操作者记录位移”, the outer expression has agent 团队,
event 要求 and complement 操作者记录位移. The inner expression has agent 操作者,
event 记录 and patient 位移. The complement link has head 要求 and dependent
操作者记录位移. The inner expression records the form of the requested action;
it does not assert that anyone performed it.

Use this projection for a pivotal/control construction when the participant
after the outer predicate also participates in the selected predication. Keep
that participant's role in the inner expression. Do not split this same selected
constituent into a parent patient plus a bare-predicate complement: doing so
teaches a different slot order and can silently omit its complement link.
This is a convention for this compact representation, not a claim that other
linguistic analyses are wrong. It does not prescribe the author's wording.

An ordinary nominal object does not become a predication. A separate parent
patient is still valid when it is a distinct argument outside the selected
predication. Quoted labels and technical nouns do not create an inner event
just because their characters resemble a selecting verb. When a construction
or its boundaries are genuinely unclear, preserve local unknowns instead of
inventing a dependent. Keep all outer negation, questions, conditions and
modality intact; grammatical selection does not establish the selected
proposition's truth, causation or execution.

### Recipients of information or objects

Input v5 adds `recipient` for a distinct, explicitly stated receiver/addressee
outside a selected predication. In “We told the operator the result”, We is
agent, told is event, the result is patient, and the operator is recipient.
“We told the result to the operator” has the same participants in a different
order. In “研究者向操作员交付了校准结果”, 操作员 is recipient and 校准结果 is patient;
“研究者将校准结果交付给操作员” places the patient before event and recipient.
Use exact participant spans, excluding the surrounding 向/给/to markers.
The engine derives order from the evidence offsets, never from candidate labels.

Do not duplicate an inner participant as a parent recipient in “We asked the
operator to record displacement”: keep the full selected predication as the
parent complement and operator as the inner agent, as described above. An
ordinary object, destination/location, technical name such as 接收器增益, or
quoted operation label is not a recipient merely because it contains a cue.
Do not infer an unstated receiver. Unknown interpretation may remain unknown.
This compact role is not a complete semantic-role inventory.

Use current v6 with explicit slot certainty for new rounds; v5 remains accepted. For mixed
historical/modern rounds containing a recipient, absent old-protocol roles are
not comparable rejection evidence: suppress negative feedback for both full
argument-shape kinds (`expression_slots` and combined `expression`) in either
selection direction. Selected features still reinforce; form/voice/link and
other independent feedback gates remain. New ordinary receipts report
`argument_shape_negative_feedback`; old receipts replay exactly. All-v5 rounds
with complete grammar can learn both preference and rejection as usual.

## Link frames

A link frame records a grammatical relation and the order of its two parts:

- `relation`: `condition`, `relative`, `complement`, `cause`, `concession`,
  `temporal` (input v3), `comparison` (input v4), or `unknown`.
- `slots`: `head` and `dependent`. For a condition/cause/concession/temporal, head
  anchors the qualified main predicate and dependent is the qualifying clause.
  For a comparison modifier, head anchors the main claim's predicate and dependent is the comparative framing
  phrase, including its stated reference. For a relative,
  head is the modified noun phrase and dependent is the modifying clause.
  For a complement, head is the selecting expression and dependent its
  complement. Use the exact material that supports the relation.

An identified relation requires both slots. They must be disjoint and inside
the frame; select a compact head when the full matrix clause would contain the
dependent. Different frames may overlap (for example, an expression containing
a relative). The core derives head/dependent order from offsets, not from a
label the model can choose to favor a preferred order. Coordinated sentences
are not automatically a subordinate link. If a relation cannot be determined,
keep it unknown rather than inventing a relative or causal construction.

For condition/cause/concession/temporal/comparison links, include the main predicate in `head`.
When the adjunct interrupts the main clause, use its compact predicate phrase,
not the subject alone. Also ground the main clause's expression frame and its
explicit participants. For example, “振幅仅在温度低于阈值时保持稳定” has a main
expression whose patient 振幅 precedes the dependent 仅在温度低于阈值时 and whose
event 保持 follows it. The dependent precedes the compact head, just as it does
in a fronted condition; the main expression establishes that it is embedded.
Include markers that belong to the dependent clause in its evidence. Do not
invent omitted participants or enlarge a frame to force a placement score.

The engine derives `adjunct_position:<relation>:before|embedded|after` only
when a known expression's event lies in the link's head. A main expression
entirely after/before the adjunct establishes an edge position; an enclosing
expression with explicit slots on both sides establishes embedding. Enclosing
edge cases require no substantive text on the other side. Missing or conflicting
matrix evidence produces no position feature. These are grounded relative
positions, not keyword rules or a complete account of Chinese conditional logic.
An expression with an event inside the head but unknown form remains unresolved
matrix evidence. Do not discard that competing scope and infer a confident
position from another expression alone.
The same applies when an unknown expression has no event slot but its supplied
scope overlaps substantive content in the head, or an event only partly overlaps
the head. Such evidence leaves attachment unresolved. Scopes/events wholly
outside the head, or overlapping only the existing ignorable separators and
whitespace, do not block a known placement. Unknown placement supplies neither
a positive position key nor negative evidence against another position.
Runtime capability `unanchored_grammar_guard` identifies this behavior. Existing
approved receipts replay unchanged; their annotations are not reinterpreted.

### Predicate targets in nested or coordinated clauses

Keep a link's attachment target separate from the full clause's meaning. When a
predicate's arguments contain other predications, bind `head` to its content
event (or an unambiguous compact predicate phrase). Preserve the full expression,
its arguments, all nested expression/relative/complement frames, and the link's
supporting scope. A nested event is not automatically another target of the
outer qualifier merely because its text lies inside a broad predicate phrase.

For “采集程序在校正完成后读取已标记的通道，并保留此前记录的异常值”, the temporal
qualifier applies to the coordinated actions 读取 and 保留. Represent these as
two temporal links sharing the dependent 在校正完成后, with the respective event
as each head. Each link's scope must contain its two pieces; the existing
expression frames retain their own explicit subjects and boundaries. Do not
invent a repeated subject for the second action. Keep 标记 and 记录 as the nested
predications they are; this sentence does not place those earlier actions after
the correction. Do not drop 保留 or other modified actions to obtain a simpler
position key. Separate targets can legitimately yield both embedded and before
positions in one sentence.

Apply this only where the qualifying scope and targets are supported. Do not
split every conjunction into links, promote an embedded/quoted predicate to a
matrix target, or narrow an uncertain head solely to make a score available.
Retain unknown relation/scope evidence when attachment remains ambiguous. This
is guidance for new annotations using existing fields, not an automatic repair
of saved candidates. Same-text choices within a saved round reuse its original
annotations, and historical receipts remain unchanged.
Resolved targets do not by themselves make the whole grammar comparable.
Placement comparison may proceed with unknown voice when every candidate has
nonempty grammar, resolved forms and role inventories, known relations and
complete placement evidence. Voice does not enter placement extraction. The
grammar status remains partial, and ordinary grammar, argument-shape and link-pair
comparison gates remain closed. Unknown form, inventory, relation or competing
target scope still blocks position comparison; absent/abstained grammar and an
off backend also block it. Keep uncertain voice unknown instead of guessing it
to obtain feedback. Runtime capability `independent_adjunct_feedback` identifies
this shared compatibility/adaptive position gate; original receipts are unchanged.

### Temporal clauses

Input version 3 adds `temporal` for a clause that anchors the main event to the
circumstance or time of another event/state. “退火结束后，探头记录电压” and
“探头在退火结束后记录电压” provide temporal framing in different positions.
Keep the temporal clause as dependent, the main predicate as head, and ground
both predications as expression frames when identifiable. The same evidence
rules derive before/embedded/after placement, independently of cue spelling.

Time ordering and a prerequisite can coexist. For this compact single-relation
representation, use `condition` when the sentence explicitly restricts whether
the main action is permitted or can occur, even if that requirement includes
an earlier event. For example, “核实电流归零后，采集器才开始读取” and
“采集器必须先核实电流归零，才能开始读取” retain a prerequisite as well as
chronology. “只有当炉温恒定时，探头才记录电压” likewise states a necessary
condition. A report that an action happened after another, with no such
restriction, uses `temporal`. A condition label does not erase the before/after
meaning or turn a necessary condition into a sufficient trigger.

Judge the whole sentence and its paragraph context, not the presence of
“后”, “时”, “才”, or “when”. For example, the time of a measured threshold
crossing can locate an observation, whereas the same circumstance can be a
trigger in an operating rule. When both temporal and conditional meaning are
present, use the convention above rather than duplicating the same bound
slots under both labels; those conflicting observations do not provide two
independent preferences. If the context does not resolve the intended relation,
keep it unknown. Apply this convention to new annotations before ranking;
do not relabel an approved historical decision or infer meaning from its score.
An ordinary time phrase such as “三小时后” need not be invented as a clause,
and “先记录电压，再测量电流” is not automatically a subordinate link.

The temporal relation and its placement describe how a sentence is organized.
They do not assert that the events happened or replace the before/after/during
meaning in the exact text. Preserve that meaning in every rewrite; a preferred
placement never authorizes changing the chronology.

### Comparative framing

Input version 4 adds `comparison` for a modifier that supplies a comparison
reference for a separate main claim. “与基线相比，误差降低了 8%” and
“误差与基线相比降低了 8%” differ in where that comparative frame appears.
Bind the complete framing phrase, such as 与基线相比 or 相较于初始设置,
as dependent, and the main predicate-containing claim as head. This modifier
may be a phrase rather than a full subordinate clause; do not invent an
independent comparison event merely to give it an expression frame.

Ground the main expression and its explicit participants. In the embedded
example, patient 误差 precedes the dependent while event 降低 follows it;
the compact head can be 降低了 8%. The existing evidence rules derive
before/embedded/after placement. If scope, relation or matrix evidence is
uncertain, preserve that uncertainty; do not infer placement from a cue word.

Distinguish comparative framing from a content predicate such as “本文比较了
两种设置的噪声水平” and a quoted name such as “图中‘相比’是标签名”. Neither
automatically creates a comparison link. “只有与基线相当时，误差才被判定为
可接受” still has a necessary-condition link; the reference inside a state
predicate is not automatically a separate comparative modifier. A rhetorical
comparison move likewise does not by itself establish a grammar comparison link.

This representation records framing and placement, not the full semantics of
greater-than/equality, percentage denominators or necessary/sufficient logic.
Keep those meanings, numbers and uncertainty in the exact sentence. An
embedded comparison can remain hard to express in the separate non-overlapping
rhetorical analysis; a known grammar link does not require inventing a confident
rhetorical label or splitting a proposition into misleading fragments.

### Version compatibility

Version 2 adds state forms and complement slots, version 3 adds temporal links,
version 4 adds comparison framing, version 5 adds expression recipients, and
version 6 adds required expression slots_complete booleans. Each explicit older version keeps its
original enums. Old unknown relations, keys and saved decisions are not
reinterpreted or backfilled. For an omitted version, slots_complete selects v6, otherwise the binder chooses
v5 for recipient slots, v4 for
comparison frames, otherwise v3 for temporal frames, otherwise v2. This keeps
old draft retries byte-exact. The runtime checker requires versions 1 through 6. Explicit v1–v5 reject slots_complete; v6 expressions require it.
New relation and position keys share the grammar-v1 feature namespace without
changing old key meanings. Only new explicit author choices teach them.

## Input and executable example

For ordinary writing rounds, use the declarative drafts in
[annotation-binding.md](annotation-binding.md) and `bind-file`. The schema below
describes its canonical output. The low-level Python examples illustrate that
protocol for direct integrations; there is no need to copy a new binder into
each revision round.

A legacy temporal `grammar` may use `version:3`, `source:writing-model`, `text_sha256` of the
whole exact UTF-8 candidate, and `frames` (at most 64). `frames:[]` means whole-
sentence abstention. Each frame has `kind`, `start`, `end`, `text`, `slots`, plus
`form`/`voice` for kind `expression` or `relation` for kind `link`. Input v6
additionally requires a boolean `slots_complete` on each expression; older input
versions and link frames reject that field. New drafts should explicitly use v7,
which retains the v6 fields and supports shared evidence as described below.

The bounded 64-frame capacity also applies to earlier input versions; their
fields and meanings are unchanged. A long sentence or common-span rewrite can
need more than 16 frames because an embedded predication and its link are
separate evidence. Never discard identified content or rewrite the author's
meaning to meet a tool limit. Above the supported capacity, preserve the draft
and report the limitation; explicit whole-grammar abstention remains available.
The runtime reports `annotation_limits` and requires `grammar_binding_capacity`;
analysis remains limited to 16 total parts. Capacity is not parse completeness.

Every slot has exactly `role`, `start`, `end`, `text`. Offsets are Python Unicode
codepoints in the **candidate**, not relative to the frame. Half-open slices
must match exactly, including math, citations and non-ASCII text. Slot roles
are unique within a frame; slots cannot overlap or leave their frame. Versions
1–6 reject duplicate frame kind/range. Version 7 permits distinct grounded
anchors at the same range, as described below. Nonempty frames together must cover all content,
with only sentence separators/whitespace left in gaps. Add an unknown frame
for unsupported clauses, or abstain. Frames and slots are normalized by offset.

### Shared evidence for distinct predicates (v7)

New drafts explicitly set grammar `version:7`; fields remain those of v6.
In “研究者清洗并干燥样品”, 清洗 and 干燥 are distinct actions with the same
literal agent 研究者 and patient 样品. Their two expression frames can use the
same enclosing text, each with its own event slot and the shared participants.
Three coordinated actions can likewise retain all three predicates. This does
not infer that the actions are simultaneous, ordered or successful.

A coextensive expression pair must have distinct event ranges. A coextensive
link pair must have distinct two-slot range pairs; exchanging head/dependent
roles does not create a new physical anchor. Identical anchors or missing
anchors at the same kind/range are still rejected. Unknown form/voice/relation
with a grounded anchor remains possible; unknown does not grant permission to
repeat the same evidence. All slot, coverage and literal checks still apply.

Use the smallest scope that faithfully contains a frame's evidence. Shared
arguments must be supported by the wording; do not invent an omitted actor.
Conversely, do not delete an explicitly shared argument, mark a known predicate
unknown, or pad punctuation to obtain different outer ranges. Overlap across
frames is permitted; slots within one frame remain disjoint. Distinct actions
retain their own attachment targets and incoming relation inventories.

The binder orders ties by physical anchor and remaps relation_coverage indexes.
Repeated observations with different outer scopes retain existing consensus and
deduplication; additional wrappers do not add learning evidence. Runtime support
requires `coextensive_grammar_frames` and input version 7. An omitted draft version
still follows historical inference (including v6 for slots_complete), so old
retries stay byte-exact. Never relabel an approved decision as v7; old inputs and
stored receipts keep their original versions and identities.

Choose semantic roles first; use code to bind that interpretation to text.
This construction example is executable, not an automatic parser:

<!-- sonder-grammar-example -->
```python
import hashlib
import json

item = {"id": "own", "label": "Other", "text": "团队完成了对误差的分析。"}
text = item["text"]

def evidence(fragment):
    start = text.index(fragment)
    return {"start": start, "end": start + len(fragment), "text": fragment}

frame = {**evidence(text), "kind": "expression", "form": "nominalized",
         "voice": "active", "slots": [
             {**evidence("团队"), "role": "agent"},
             {**evidence("分析"), "role": "event"},
             {**evidence("误差"), "role": "patient"}]}
item["grammar"] = {"version": 3, "source": "writing-model",
                   "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "frames": [frame]}
print(json.dumps(item, ensure_ascii=False))
```

A quantitative copular statement uses the same binding procedure. The value
and citation remain literal manuscript content:

<!-- sonder-state-grammar-example -->
```python
import hashlib
import json

item = {"id": "own", "label": "Other", "text": "薄膜厚度为 86 nm[1]。"}
text = item["text"]

def evidence(fragment):
    start = text.index(fragment)
    return {"start": start, "end": start + len(fragment), "text": fragment}

frame = {**evidence(text), "kind": "expression", "form": "copular",
         "voice": "nonagentive", "slots": [
             {**evidence("薄膜厚度"), "role": "patient"},
             {**evidence("为"), "role": "event"},
             {**evidence("86 nm"), "role": "complement"}]}
item["grammar"] = {"version": 3, "source": "writing-model",
                   "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "frames": [frame]}
print(json.dumps(item, ensure_ascii=False))
```

Add the generated candidate to the saved candidate array, with the round's
persisted ID. Keep the existing rhetorical analysis beside it. When a fragment
occurs more than once, choose the intended occurrence programmatically; never
guess offsets. Save text via a file tool, not shell interpolation. A stale hash,
wrong slice, invalid/unknown field, missing required slot or incomplete coverage
fails before the DB opens, even with syntax off. Correct the evidence binding
or explicitly abstain; do not silently drop a failing object to bypass checks.

## Feedback, scoring and limits

Features live in `syntax_prefs` with prefix `syntax:grammar-v1:writing-model:`.
The input version and feature version are different: version 2 adds property/
copular forms and complement slots; old version 1 retains its original enums
and slots and stays accepted. Existing expression/link frames produce identical
feature keys under either input version, preserving prior weights. New forms
add new keys; no database reset, namespace replacement or feedback backfill is
needed. Do not change a committed file's version: its original ID still replays
the original receipt, while changing the payload under that ID is a conflict.
They include form, voice, participant order, their combined signature, link
type and the head/dependent order. Surface/parser features and rhetorical
analysis-v2 remain separate sources. `syntax-backend off` disables all of them.

Selected features receive context-scoped, decaying positive feedback. Ordinary
grammar negative feedback is enabled only when all candidates have nonempty grammar,
all frame categories are known, and v6 expression inventories are declared
complete. The independent position-only gate above allows voice uncertainty.
Missing/unknown evidence for the feature being compared is not rejection. `complete` means those
input declarations are known, not a certified exhaustive parse. Legacy inputs
retain their earlier category-based status, without inferring new certainty.
Grammar is part of the decision fingerprint. New annotations must not be added
to already committed files; old IDs replay their original receipt unchanged.
Old candidates without grammar retain their old fingerprints and scores.

Scoring averages weights within each feature kind, then averages the four
expression kinds and the two link kinds separately and adds those family
scores. Adjunct placement has its own additive family, so adding it does not
dilute existing expression/link weights. With no learned position weights,
old scores stay unchanged. New keys retain the grammar-v1 namespace; old keys
keep their meaning. Only new decisions learn the extra keys, with no backfill
or rewriting of saved receipts. Runtime capability `adjunct_position` identifies
engines supporting this addition.

Independent relations also retain their surface ordering. The engine pairs
bound head/dependent slot envelopes that do not overlap, using each relation
type and its internal slot order. Thus two procedural units can share the
same rhetorical label while preferring temporal-before-condition over the
reverse. Frame padding, words, candidate labels and frame array order do not
define these keys. Nested/overlapping links or an unknown relation abstain;
this does not infer a logical dependency or execution order.

These `link_pair_order:` keys have their own additive score family. No old
score changes until a new choice learns pair weights; historical receipts and
weights are not backfilled. Repeated known patterns count once, while each
unresolved pair reserves zero evidence in the denominator. Inspect
`grammar_scores_by_family.link_pair_order` and matched keys. Capability
`grammar_link_order` identifies this support. A new ordinary receipt exposes
`link_pair_negative_feedback`: rejection requires complete grammar, one shared
input version across the pool, and resolved ordering for every link pair.
Otherwise known selected pairs can still gain weight without penalizing an
alternative whose absence may reflect an incomparable representation.

Pair extraction identifies a relation by the physical ranges of its two
bound slots. An extra frame with the same slots, relation and direction is
the same evidence, even if its outer scope includes a comma or period. It
cannot add a preference or dilute the pair score. If the same slot ranges
have different relation types, opposite role assignments or an unknown
reading, their shared identity abstains for order learning. Incomplete slot
evidence remains unresolved; do not guess that it matches a known relation.
Runtime capability `grammar_link_identity` marks this protection. The original
annotations, hashes and receipts are retained, and ordinary nonduplicate pair
keys and scores are unchanged. This does not certify or rewrite the underlying
linguistic analysis.

`grammar_observation_identity` extends observation identity to other grammar
families. An expression is identified by its bound event range and the set of
physical slot ranges; relation identity continues to use its two slot ranges.
Padded outer scopes do not add observations. When there is no event anchor or
insufficient link slots, preserve separate scope evidence instead of guessing
equivalence. Consensus is fieldwise: conflicting form/voice/relation becomes
unknown, and conflicting roles or partial inventories suppress complete shape
keys while independently agreed attributes remain usable. Such conflicts make
grammar partial (or abstained when no known features remain) and disable
grammar rejection for that round. The derived
link inventory flag is internal; no new input field is accepted.

Unknown denominators count observations, so copying the same partial evidence
does not dilute another matched value. Every original expression scope remains
available to adjunct-placement checks: choosing a representative wrapper must
not erase a conflict between embedded and edge placement. No source annotation,
old hash or immutable receipt is normalized in place. Ordinary nonduplicate
inputs retain their feature keys, scores and writes.

Identical relation/slot signatures remain indistinguishable when reversed;
this is structure memory, not memory of arbitrary proposition identities.
Omitted links remain a producer limitation. Do not add or remove annotations
to obtain a preferred ranking, or reinterpret old decisions after an upgrade.

Missing/unknown kinds contribute zero. Within each kind, divide by the number
of distinct known values plus the number of unresolved observation contributions.
An unknown form reserves zero evidence for form, slot signature and combined
expression; unknown voice reserves it for voice and the combined expression.
Unknown links reserve it for relation/order and possible adjunct placement;
known links with unresolved placement also retain a zero position contribution.
Count each unresolved observation, including when another is already unknown.
Changing a known category to unknown while retaining its evidence must not
raise the grammar score by removing an unmatched value from an average.

These are scoring contributions, not negative feedback or new stored keys.
Complete evidence with resolved placements retains its prior scoring; missing
or uncertain evidence cannot improve the remaining evidence's average. This
keeps an extra learned relative/condition
preference visible beside an expression shared by both candidates, without
rewarding duplicated frames. `grammar_score` joins `base_syntax_score`,
`analysis_score` and `analysis_topology_score` to form `syntax_score`, which receives
the existing soft syntax scale in the total candidate score. Inspect
`grammar_scores_by_family`, `matched_grammar_features`, `grammar_source`,
`grammar_input_version`, `grammar_feature_version`, and `grammar_status` rather than interpreting the
total as a grammar-correctness probability. New choice receipts also expose
`grammar_features` and `grammar_negative_feedback`.
Receipts also report `adjunct_position_negative_feedback`: position rejection
requires every relevant placement to be grounded and all grammar uncertainty
except voice to be resolved, as specified above. Thus it may be true while
`grammar_negative_feedback` is false. Unresolved placement is not evidence against a known alternative,
even when the frame enums themselves are complete.

These frames cover some useful academic constructions. They cannot express
all syntax, verify role assignments, resolve arbitrary ambiguity, or guarantee
that a rewrite preserves meaning. The uncertainty guard handles explicitly
unknown evidence, not an entire nested frame the writing model omitted.
Independent writing trials and review of
negation, scope, references and claim strength remain necessary.
