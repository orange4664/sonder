"""Versioned relation inventories and local feature capacity; no store or model IO."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json

PREFIX = 'syntax|syntax:relations-v1:'
VERSION = 1
RELATIONS = ('none', 'condition', 'relative', 'complement', 'cause', 'concession', 'temporal', 'comparison')
VALUES = dict(form=('verbal', 'nominalized', 'adjectival', 'copular'), voice=('active', 'passive', 'nonagentive'))
GROUPS = {field + '/' + relation: tuple(PREFIX + field + '/' + relation + '/' + value for value in values)
          for field, values in VALUES.items() for relation in RELATIONS}
KEY_GROUP = {key: group for group, keys in GROUPS.items() for key in keys}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def frame_key(frame):
    """Locate exact outer evidence without aliasing distinct coextensive events.

    Slot roles are deliberately absent from the anchor: exchanging roles does
    not create a second physical observation. Validation rejects unresolved
    coextensive evidence before this key is used for coverage references.
    """
    slots = frame['slots']
    if frame['kind'] == 'expression':
        event = next((slot for slot in slots if slot['role'] == 'event'), None)
        anchors = ((event['start'], event['end']),) if event else ()
    else:
        anchors = tuple(sorted((slot['start'], slot['end']) for slot in slots)) if len(slots) == 2 else ()
    return frame['kind'], frame['start'], frame['end'], anchors


def _index(value, frames, kind):
    if type(value) is not int or not 0 <= value < len(frames) or frames[value]['kind'] != kind:
        raise ValueError(f'expected canonical {kind} frame index, got {value!r}')
    return value


def _graph(observe, candidate):
    grammar = candidate.get('grammar')
    frames = grammar['frames'] if grammar else []
    by_evidence = {frame_key(f): i for i,f in enumerate(frames)}
    events, event_indices, links, link_indices = {}, {}, {}, {}
    if grammar is None:
        return frames, events, event_indices, links, link_indices
    for projected, members in observe(grammar):
        indices = [by_evidence[frame_key(m)] for m in members]
        if projected['kind'] == 'expression':
            event = next((s for s in projected['slots'] if s['role']=='event'), None)
            anchor = (event['start'],event['end']) if event else None
            identity = ('event',*anchor) if anchor else ('unanchored',min(indices))
            node = events.setdefault(identity, dict(anchor=anchor, frames=[], readings=[]))
            node['frames'].extend(indices); node['readings'].append(projected)
            event_indices.update({i:identity for i in indices})
        else:
            identity = min(indices)
            links[identity] = dict(frame=projected, frames=indices)
            link_indices.update({i:identity for i in indices})
    for node in events.values():
        for field in ['form','voice']:
            values = {f[field] for f in node['readings']}
            node[field] = next(iter(values)) if len(values)==1 else 'unknown'
        roles = {tuple((s['role'],s['start'],s['end']) for s in f['slots']) for f in node['readings']}
        node['inventory_known'] = len(roles)==1 and all(f.get('slots_complete',True) for f in node['readings'])
    return frames, events, event_indices, links, link_indices


def _declarations(observe, candidate, value):
    if not isinstance(value,dict) or set(value)!={'expressions','links'}:
        raise ValueError('coverage candidate requires exactly expressions and links')
    frames, events, event_indices, links, link_indices = _graph(observe,candidate)
    for field in ['expressions','links']:
        if not isinstance(value[field],list) or len(value[field])>len(frames):
            raise ValueError('coverage declarations must be bounded arrays')
    expr, target, used = {}, {}, set()
    for declaration_index, row in enumerate(value['links']):
        if not isinstance(row,dict):
            raise ValueError('link declaration must be an object')
        kind = row.get('head_kind')
        if not isinstance(kind, str) or kind not in {'event','non_event','unknown'}:
            raise ValueError(f'links[{declaration_index}].head_kind must be event, non_event or unknown')
        expected = {'frame','head_kind'} | ({'head_expression'} if kind=='event' else set())
        if set(row)!=expected:
            raise ValueError('invalid link head declaration')
        index = _index(row['frame'],frames,'link')
        if index in used:
            raise ValueError('duplicate link frame declaration')
        used.add(index)
        identity = link_indices[index]
        link = links[identity]['frame']
        head = next((s for s in link['slots'] if s['role']=='head'),None)
        if kind=='event':
            index2 = _index(row['head_expression'],frames,'expression')
            event_id = event_indices[index2]
            anchor = events[event_id]['anchor']
            if (not link.get('slots_complete',True) or head is None or anchor is None
                    or anchor!=(head['start'],head['end'])):
                raise ValueError('event target must match one exact bound predicate head')
            if any(other!=event_id and node['anchor'] is not None
                   and max(anchor[0],node['anchor'][0])<min(anchor[1],node['anchor'][1])
                   for other,node in events.items()):
                raise ValueError('event head overlaps competing predicate evidence')
            if any(node['anchor'] is None and any(
                max(head['start'],f['start'])<min(head['end'],f['end']) for f in node['readings'])
                for node in events.values()):
                raise ValueError('event head overlaps unanchored unknown expression evidence')
            normalized = (kind,event_id)
        elif kind=='non_event':
            if head is None or not link.get('slots_complete',True) or any(
                node['anchor'] is not None and max(head['start'],node['anchor'][0])<min(head['end'],node['anchor'][1])
                for node in events.values()):
                raise ValueError('non_event head must be explicit and disjoint from predicate evidence')
            if any(node['anchor'] is None and any(
                max(head['start'],f['start'])<min(head['end'],f['end']) for f in node['readings'])
                for node in events.values()):
                raise ValueError('non_event head overlaps unanchored unknown expression evidence')
            normalized = (kind,None)
        else:
            normalized = (kind,None)
        if identity in target and target[identity]!=normalized:
            raise ValueError('duplicate physical link has contradictory target declarations')
        target[identity]=normalized
    used=set()
    for row in value['expressions']:
        if (not isinstance(row,dict) or set(row)!={'frame','incoming_complete','incoming_links'}
                or type(row['incoming_complete']) is not bool or not isinstance(row['incoming_links'],list)
                or len(row['incoming_links'])>len(frames)):
            raise ValueError('invalid expression incoming inventory declaration')
        index=_index(row['frame'],frames,'expression')
        if index in used:
            raise ValueError('duplicate expression frame declaration')
        used.add(index)
        incoming=[_index(i,frames,'link') for i in row['incoming_links']]
        if len(set(incoming))!=len(incoming):
            raise ValueError('duplicate incoming link reference')
        identity=event_indices[index]
        normalized=(row['incoming_complete'],frozenset(link_indices[i] for i in incoming))
        if identity in expr and expr[identity]!=normalized:
            raise ValueError('duplicate physical predicate has contradictory coverage declarations')
        expr[identity]=normalized
    # A reference is evidence, not permission to assign a different target.
    for event_id,(complete,incoming) in expr.items():
        for link_id in incoming:
            kind,node=target.get(link_id,('unknown',None))
            if kind!='unknown' and (kind!='event' or node!=event_id):
                raise ValueError('incoming reference points to another or non-event head')
        expected={i for i,(kind,node) in target.items() if kind=='event' and node==event_id}
        if complete and not expected<=incoming:
            raise ValueError('complete incoming inventory omits a declared targeted link')
    return frames,events,links,expr,target


def bind(observe, candidate, declaration):
    _,events,_,expressions,targets=_declarations(observe,candidate,declaration)
    canonical=dict(expressions=[],links=[])
    for event_id,(complete,refs) in sorted(expressions.items(),key=lambda x:min(events[x[0]]['frames'])):
        canonical['expressions'].append(dict(frame=min(events[event_id]['frames']),
            incoming_complete=complete,incoming_links=sorted(refs)))
    for link_id,(kind,target) in sorted(targets.items()):
        item=dict(frame=link_id,head_kind=kind)
        if kind=='event':item['head_expression']=min(events[target]['frames'])
        canonical['links'].append(item)
    return dict(version=1,text_sha256=hashlib.sha256(candidate['text'].encode()).hexdigest(),
                grammar_sha256=digest(candidate.get('grammar')),**canonical)


def state(observe, candidate, bound=None):
    if bound is None:
        _,events,_,_,_=_graph(observe,candidate)
        return dict(keys=[],capacity=len(events),fully_known=False,events=[],reason='missing_coverage')
    if (not isinstance(bound,dict) or set(bound)!={'version','text_sha256','grammar_sha256','expressions','links'}
            or type(bound['version']) is not int or bound['version']!=1):
        raise ValueError('invalid bound relation coverage')
    if (bound['text_sha256']!=hashlib.sha256(candidate['text'].encode()).hexdigest()
            or bound['grammar_sha256']!=digest(candidate.get('grammar'))):
        raise ValueError('coverage fingerprint differs from exact candidate grammar or text')
    _,events,links,declarations,targets=_declarations(observe,candidate,{k:bound[k] for k in ['expressions','links']})
    incoming={i:set() for i in events};blocked=set()
    for link_id,link_node in links.items():
        kind,target=targets.get(link_id,('unknown',None));link=link_node['frame']
        if kind=='event':
            incoming[target].add(link_id)
        elif kind=='unknown':
            head=next((s for s in link['slots'] if s['role']=='head'),None)
            affected={i for i,node in events.items() if head is not None and node['anchor'] is not None
                      and max(head['start'],node['anchor'][0])<min(head['end'],node['anchor'][1])}
            # No anchored target does not prove unrelatedness.
            blocked.update(affected or events)
    keys=set();unknown=0;rows=[]
    for event_id,node in events.items():
        declared_complete,refs=declarations.get(event_id,(False,frozenset()))
        reasons=[]
        if not declared_complete:reasons.append('incoming_not_complete')
        if event_id in blocked:reasons.append('unresolved_target')
        if any(targets.get(i,('unknown',None))[0]=='unknown' for i in refs):reasons.append('unresolved_reference')
        if node['anchor'] is None or node['form']=='unknown' or node['voice']=='unknown' or not node['inventory_known']:
            reasons.append('unresolved_expression')
        relations={links[i]['frame']['relation'] for i in incoming[event_id]}
        if 'unknown' in relations:reasons.append('unresolved_relation')
        known=not reasons
        key=None
        if known:
            key=PREFIX+('+'.join(sorted(relations)) or 'none')+'/'+node['form']+'/'+node['voice']
            keys.add(key)
        else:
            unknown+=1
        rows.append(dict(frames=sorted(node['frames']),anchor=node['anchor'],known=known,
                         incoming_frames=sorted(i for k in incoming[event_id] for i in links[k]['frames']),
                         relations=sorted(relations),key=key,reasons=reasons))
    return dict(keys=sorted(keys),capacity=len(keys)+unknown,fully_known=bool(events) and not unknown,events=rows)


def validate(observe, candidate, value):
    """Called only after the owning candidate validator canonicalizes grammar."""
    if value is None:
        raise ValueError('relation_coverage must be a versioned object when present')
    if candidate.get('grammar') is None:
        raise ValueError('relation_coverage requires explicit grammar')
    state(observe, candidate, value)
    return bind(observe, candidate, {key: value[key] for key in ('expressions', 'links')})


def bind_draft(observe, candidate, original_frames, value):
    """Remap draft references after grammar sorting, then bind exact fingerprints."""
    if (not isinstance(value, dict) or set(value) != {'version', 'expressions', 'links'}
            or type(value['version']) is not int or value['version'] != VERSION
            or not isinstance(value['expressions'], list) or not isinstance(value['links'], list)):
        raise ValueError('relation coverage draft requires version 1, expressions and links')
    frames = candidate['grammar']['frames']
    canonical = {frame_key(frame): index for index, frame in enumerate(frames)}

    def remap(index, kind):
        frame = original_frames[_index(index, original_frames, kind)]
        return canonical[frame_key(frame)]

    declaration = deepcopy({key: value[key] for key in ('expressions', 'links')})
    for row in declaration['expressions']:
        if not isinstance(row, dict) or not isinstance(row.get('incoming_links'), list):
            raise ValueError('invalid expression incoming inventory declaration')
        row['frame'] = remap(row.get('frame'), 'expression')
        row['incoming_links'] = [remap(index, 'link') for index in row['incoming_links']]
    for row in declaration['links']:
        if not isinstance(row, dict):
            raise ValueError('link declaration must be an object')
        row['frame'] = remap(row.get('frame'), 'link')
        if 'head_expression' in row:
            row['head_expression'] = remap(row['head_expression'], 'expression')
    return bind(observe, candidate, declaration)


def project(observe, candidate, *, scale, backend='heuristic'):
    """Fixed relation capacities reserve every possible group for unknown events."""
    result = state(observe, candidate, candidate.get('relation_coverage'))
    unknown = (result['capacity'] if result.get('reason') == 'missing_coverage'
               else sum(not row['known'] for row in result['events']))
    members = {group: [] for group in RELATIONS}
    for row in result['events']:
        if row['known']:
            for relation in row['relations'] or ['none']:
                members[relation].append(row)
    capacities = {group: len(rows) + unknown for group, rows in members.items()}
    vector = defaultdict(float)
    if backend != 'off':
        for relation, rows in members.items():
            for row in rows:
                amount = scale / len(RELATIONS) / capacities[relation] / 2
                frame = candidate['grammar']['frames'][row['frames'][0]]
                for field in VALUES:
                    vector[PREFIX + field + '/' + relation + '/' + frame[field]] += amount
    return dict(sorted(vector.items())), {**result, 'capacities': capacities, 'unknown': unknown}
