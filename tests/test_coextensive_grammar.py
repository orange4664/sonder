"""Shared evidence must preserve distinct actions and their coverage targets."""
from copy import deepcopy
import importlib.util
import itertools
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('coextensive_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec); spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/coextensive-grammar.json').read_text())
NOW = '2026-10-29T12:00:00+00:00'


class CoextensiveGrammarTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-coextensive-')
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.db = str(self.root / 'preference.db')
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)

    def bind(self, key='training'):
        return learn.bind_candidate_data(deepcopy(FIXTURE[key]))

    def choose(self, data, chosen='active', event='training', db=None):
        payload = {**data, 'chosen': chosen, 'round_id': event, 'author_owned': True}
        path = self.root / (event + '.json'); path.write_text(json.dumps(payload, ensure_ascii=False))
        return learn.choice_file(db or self.db, 'methods', 'detail', path)

    def dump(self):
        with sqlite3.connect(self.db) as conn: return list(conn.iterdump())

    def test_shared_agent_patient_and_distinct_events_remain_complete(self):
        data = self.bind(); item = data['candidates'][0]
        frames = item['grammar']['frames']
        self.assertEqual(len({(f['start'], f['end']) for f in frames}), 1)
        self.assertEqual([next(s['text'] for s in f['slots'] if s['role'] == 'event') for f in frames], ['清洗', '干燥'])
        for f in frames:
            self.assertEqual({s['text'] for s in f['slots'] if s['role'] != 'event'}, {'研究者', '样品'})
            self.assertTrue(f['slots_complete'])
        state = learn._relations.state(learn._grammar_observations, item, item['relation_coverage'])
        self.assertEqual(len(state['events']), 2)
        self.assertTrue(state['fully_known'])
        self.assertEqual(learn._validated_candidates(data['candidates']), data['candidates'])

    def test_legacy_versions_and_omitted_version_keep_rejection_contract(self):
        for version in [1, 2, 3, 4, 5, 6, None]:
            data = deepcopy(FIXTURE['training'])
            for c in data['candidates']:
                if version is None: c['grammar'].pop('version')
                else: c['grammar']['version'] = version
                if version is not None and version < 6:
                    for f in c['grammar']['frames']: f.pop('slots_complete')
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'duplicate frame kind/range'):
                learn.bind_candidate_data(data)

    def test_v7_still_requires_slot_certainty_and_complete_grounding(self):
        for field in ['slots_complete', 'text']:
            data = deepcopy(FIXTURE['training'])
            data['candidates'][0]['grammar']['frames'][1].pop(field)
            with self.assertRaises(ValueError): learn.bind_candidate_data(data)
        data = deepcopy(FIXTURE['training'])
        data['candidates'][0]['grammar']['frames'][1]['slots'][1]['text'] = '不存在'
        with self.assertRaises(ValueError): learn.bind_candidate_data(data)

    def test_same_event_anchor_cannot_evade_duplicate_guard_with_new_roles_or_tags(self):
        for change in ['none', 'form', 'voice', 'roles', 'inventory']:
            data = deepcopy(FIXTURE['training']); frames = data['candidates'][0]['grammar']['frames']
            frames[1] = deepcopy(frames[0])
            if change == 'form': frames[1]['form'] = 'nominalized'
            if change == 'voice': frames[1]['voice'] = 'unknown'
            if change == 'roles':
                frames[1]['slots'][0]['role'], frames[1]['slots'][2]['role'] = 'patient', 'agent'
            if change == 'inventory': frames[1]['slots_complete'] = False
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'coextensive frame anchors'):
                learn.bind_candidate_data(data)

    def test_unanchored_coextensive_expression_rejected_in_either_order(self):
        for reverse in [False, True]:
            data = deepcopy(FIXTURE['training']); frames = data['candidates'][0]['grammar']['frames']
            frames[1].update(form='unknown', slots=[])
            if reverse: frames.reverse()
            with self.assertRaisesRegex(ValueError, 'coextensive frame anchors'):
                learn.bind_candidate_data(data)

    def test_same_link_anchors_reversed_roles_or_uncertain_labels_remain_one_collision(self):
        for change in ['roles', 'relation', 'unanchored']:
            data = deepcopy(FIXTURE['temporal']); frames = data['candidates'][0]['grammar']['frames']
            frames[4] = deepcopy(frames[3])
            if change == 'roles':
                for s in frames[4]['slots']: s['role'] = 'head' if s['role'] == 'dependent' else 'dependent'
            if change == 'relation': frames[4]['relation'] = 'condition'
            if change == 'unanchored': frames[4].update(relation='unknown', slots=[])
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'coextensive frame anchors'):
                learn.bind_candidate_data(data)

    def test_all_temporal_frame_permutations_preserve_exact_bound_coverage(self):
        baseline = self.bind('temporal')
        for order in itertools.permutations(range(5)):
            data = deepcopy(FIXTURE['temporal']); c = data['candidates'][0]
            c['grammar']['frames'] = [c['grammar']['frames'][i] for i in order]
            remap = {old: new for new, old in enumerate(order)}
            for row in c['relation_coverage']['expressions']:
                row['frame'] = remap[row['frame']]
                row['incoming_links'] = [remap[i] for i in row['incoming_links']]
            for row in c['relation_coverage']['links']:
                row['frame'] = remap[row['frame']]; row['head_expression'] = remap[row['head_expression']]
            # Slot array order is also irrelevant to identity/remapping.
            for f in c['grammar']['frames']: f['slots'].reverse()
            self.assertEqual(learn.bind_candidate_data(data), baseline, order)

    def test_coextensive_links_keep_separate_predicate_targets(self):
        item = self.bind('temporal')['candidates'][0]
        state = learn._relations.state(learn._grammar_observations, item, item['relation_coverage'])
        self.assertEqual(len(state['events']), 3)
        self.assertTrue(state['fully_known'])
        events = {item['text'][r['anchor'][0]:r['anchor'][1]]: r for r in state['events']}
        self.assertEqual(events['稳定']['relations'], [])
        self.assertEqual(events['加热']['relations'], ['temporal'])
        self.assertEqual(events['压缩']['relations'], ['temporal'])
        self.assertNotEqual(events['加热']['incoming_frames'], events['压缩']['incoming_frames'])
        bad = deepcopy(FIXTURE['temporal'])
        bad['candidates'][0]['relation_coverage']['links'][1]['head_expression'] = 1
        with self.assertRaises(ValueError): learn.bind_candidate_data(bad)

    def test_distinct_predicates_do_not_disappear_when_three_share_one_range(self):
        data = deepcopy(FIXTURE['training']); c = data['candidates'][0]
        c['text'] = '研究者清洗、干燥并称量样品。'
        for f in c['grammar']['frames']: f['text'] = c['text'][:-1]
        third = deepcopy(c['grammar']['frames'][0]); third['slots'][1]['text'] = '称量'
        c['grammar']['frames'].append(third)
        c['relation_coverage']['expressions'].append({'frame': 2, 'incoming_complete': True, 'incoming_links': []})
        bound = learn.bind_candidate_data(data)['candidates'][0]
        state = learn._relations.state(learn._grammar_observations, bound, bound['relation_coverage'])
        self.assertEqual(len(state['events']), 3)
        self.assertTrue(state['fully_known'])

    def test_valid_padded_v6_control_preserves_features_scores_and_learning(self):
        legacy = deepcopy(FIXTURE['training'])
        for c in legacy['candidates']:
            c['grammar']['version'] = 6
            c['grammar']['frames'][1]['text'] += '。'
        old = learn.bind_candidate_data(legacy); modern = self.bind()
        db2 = str(self.root / 'modern.db')
        for db, pool in [(self.db, old), (db2, modern)]:
            learn.learning_policy(db, 'ensure-adaptive')
            self.choose(pool, db=db)
        with sqlite3.connect(self.db) as a, sqlite3.connect(db2) as b:
            for table in ['adaptive_weights', 'adaptive_relative', 'adaptive_opposition', 'syntax_prefs', 'phrase_prefs']:
                self.assertEqual(a.execute('SELECT * FROM '+table+' ORDER BY 1,2').fetchall(),
                                 b.execute('SELECT * FROM '+table+' ORDER BY 1,2').fetchall(), table)
        for left, right in zip(old['candidates'], modern['candidates']):
            self.assertEqual(learn._grammar_features(left['grammar']), learn._grammar_features(right['grammar']))
        query = self.bind('query')['candidates']
        self.assertEqual(learn.rank_candidates(self.db, 'methods', 'detail', query),
                         learn.rank_candidates(db2, 'methods', 'detail', query))

    def test_soft_preference_transfers_to_new_words_and_reverses_with_feedback(self):
        learn.learning_policy(self.db, 'ensure-adaptive')
        training = self.bind(); query = self.bind('query')['candidates']
        self.choose(training, event='initial')
        initial = learn.rank_candidates(self.db, 'methods', 'detail', query)
        self.assertEqual(initial[0]['id'], 'active')
        self.assertGreater(initial[0]['score'], initial[1]['score'])
        for i in range(8): self.choose(training, chosen='passive', event='reverse-'+str(i))
        changed = learn.rank_candidates(self.db, 'methods', 'detail', query)
        self.assertEqual(changed[0]['id'], 'passive')
        self.assertGreater(changed[0]['score'], changed[1]['score'])

    def test_unknown_fields_cannot_improve_learned_score_and_off_abstains(self):
        learn.learning_policy(self.db, 'ensure-adaptive')
        self.choose(self.bind())
        baseline = self.bind('query')['candidates'][0]
        full = learn.rank_candidates(self.db, 'methods', 'detail', [baseline])[0]
        for field, value in [('form', 'unknown'), ('voice', 'unknown'), ('slots_complete', False)]:
            data = deepcopy(FIXTURE['query'])
            data['candidates'][0]['grammar']['frames'][0][field] = value
            partial = learn.bind_candidate_data(data)['candidates'][0]
            row = learn.rank_candidates(self.db, 'methods', 'detail', [partial])[0]
            self.assertLessEqual(row['score'], full['score'])
            self.assertEqual(row['relation_coverage_status'], 'partial')
        learn.set_syntax_backend(self.db, 'off')
        row = learn.rank_candidates(self.db, 'methods', 'detail', [baseline])[0]
        self.assertEqual(row['grammar_score'], 0)
        self.assertEqual(row['relation_score'], 0)

    def test_duplicate_outer_padding_does_not_multiply_v7_observations(self):
        learn.learning_policy(self.db, 'ensure-adaptive')
        self.choose(self.bind())
        data = deepcopy(FIXTURE['query'])
        original = learn.bind_candidate_data(data)['candidates'][0]
        duplicate = deepcopy(data['candidates'][0]['grammar']['frames'][0])
        duplicate['text'] += '。'
        data['candidates'][0]['grammar']['frames'].append(duplicate)
        repeated = learn.bind_candidate_data(data)['candidates'][0]
        self.assertEqual(len(learn._grammar_observations(repeated['grammar'])), 2)
        a, b = [learn.rank_candidates(self.db, 'methods', 'detail', [item])[0] for item in [original, repeated]]
        for key in ['score', 'grammar_score', 'relation_score', 'profile_score', 'comparison_score']:
            self.assertEqual(a[key], b[key], key)

    def test_receipt_replay_keeps_original_version_and_rejects_relabeling(self):
        old = deepcopy(FIXTURE['training'])
        for c in old['candidates']:
            c['grammar']['version'] = 6; c['grammar']['frames'][1]['text'] += '。'
        old = learn.bind_candidate_data(old)
        receipt = self.choose(old, event='historical')
        state = self.dump()
        newer = deepcopy(old)
        for c in newer['candidates']:
            c['grammar']['version'] = 7
            c['relation_coverage']['grammar_sha256'] = learn._relations.digest(c['grammar'])
        with self.assertRaises(ValueError): self.choose(newer, event='historical')
        self.assertEqual(self.dump(), state)
        self.assertEqual(self.choose(old, event='historical'), {**receipt, 'replayed': True})
        self.assertEqual(self.dump(), state)

    def test_cli_prepares_and_ranks_v7_and_duplicate_failure_has_no_store(self):
        draft = self.root / 'draft.json'; out = self.root / 'bound.json'
        draft.write_text(json.dumps(FIXTURE['temporal'], ensure_ascii=False))
        argv = [sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'), 'prepare-file', self.db,
                'methods', 'detail', str(draft), str(out), '--summary']
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)['candidates'][0]['grammar_input_version'], 7)
        data = deepcopy(FIXTURE['temporal'])
        data['candidates'][0]['grammar']['frames'][2] = deepcopy(data['candidates'][0]['grammar']['frames'][1])
        draft.write_text(json.dumps(data, ensure_ascii=False))
        absent = self.root / 'absent.db'; argv[5] = str(absent)
        before = out.read_bytes()
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(absent.exists()); self.assertEqual(out.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
