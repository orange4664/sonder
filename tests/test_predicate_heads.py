"""Explicit link targets preserve nested meaning and learn mixed placement."""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('predicate_heads_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/predicate-heads.json').read_text())
PREFIX = learn.GRAMMAR_PREFIX + 'adjunct_position:temporal:'
NOW = '2026-10-28T08:00:00+00:00'


class PredicateHeadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-predicate-heads-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.counter = 0
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def pool(self, key='targeted'):
        return learn._validated_candidates(deepcopy(FIXTURE[key][:2]))

    def choose(self, db, pool, chosen, identity):
        path = self.root / (identity + '.json')
        path.write_text(json.dumps(dict(round_id=identity, candidates=pool, chosen=chosen,
            author_owned=True, learning_context=dict(section='methods', role='detail')), ensure_ascii=False))
        return learn.choice_file(str(db), 'methods', 'detail', path), path

    def rows(self, db, sql, params=()):
        with closing(sqlite3.connect(db)) as conn:
            return conn.execute(sql, params).fetchall()

    def dump(self, db):
        with closing(sqlite3.connect(db)) as conn:
            return list(conn.iterdump())

    def position_only_rank(self, db, pool, section='methods'):
        self.counter += 1
        copy = self.root / ('ablation-' + str(self.counter) + '.db')
        with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(copy)) as target:
            source.backup(target)
            with target:
                target.execute('DELETE FROM prefs')
                target.execute('DELETE FROM phrase_prefs')
                target.execute('DELETE FROM syntax_prefs WHERE feature NOT LIKE ?', (PREFIX + '%',))
                target.execute('DELETE FROM adaptive_weights WHERE feature NOT LIKE ?', ('syntax|' + PREFIX + '%',))
                target.execute('DELETE FROM adaptive_legacy WHERE feature NOT LIKE ?', ('syntax|' + PREFIX + '%',))
        return learn.rank_candidates(str(copy), section, 'detail', pool)

    def test_targeted_links_keep_all_other_evidence_and_both_modified_actions(self):
        for old, new in zip(self.pool('original'), self.pool()):
            self.assertEqual(new['text'], old['text'])
            self.assertEqual(new['analysis'], old['analysis'])
            self.assertEqual(new['grammar']['text_sha256'], old['grammar']['text_sha256'])
            others = lambda item: [f for f in item['grammar']['frames']
                                  if not (f['kind'] == 'link' and f['relation'] == 'temporal')]
            self.assertEqual(others(new), others(old))
            links = [f for f in new['grammar']['frames'] if f['kind'] == 'link' and f['relation'] == 'temporal']
            self.assertEqual([s['text'] for f in links for s in f['slots'] if s['role'] == 'head'], ['读取', '保留'])
            dependents = [s for f in links for s in f['slots'] if s['role'] == 'dependent']
            self.assertEqual(dependents[0], dependents[1])

    def test_separate_targets_resolve_mixed_positions_without_changing_old_broad_head(self):
        front, embedded = self.pool()
        self.assertEqual(learn._grammar_adjunct_positions(front['grammar'], require_complete=True),
                         ['adjunct_position:temporal:before'])
        self.assertEqual(learn._grammar_adjunct_positions(embedded['grammar'], require_complete=True),
                         ['adjunct_position:temporal:before', 'adjunct_position:temporal:embedded'])
        old = self.pool('original')[1]
        self.assertEqual(learn._grammar_adjunct_positions(old['grammar']), [])
        self.assertIsNone(learn._grammar_adjunct_positions(old['grammar'], require_complete=True))

    def test_shared_before_key_learns_once_per_choice_in_both_policies(self):
        for policy in ['legacy', 'adaptive-v1']:
            with self.subTest(policy=policy):
                db = self.root / (policy + '.db')
                learn.learning_policy(str(db), policy)
                receipt, _ = self.choose(db, self.pool(), 'fronted', policy + '-front')
                self.assertTrue(receipt['adjunct_position_negative_feedback'])
                self.assertEqual(self.rows(db, 'SELECT picks FROM syntax_prefs WHERE feature=?', (PREFIX + 'before',)), [(1,)])
                self.choose(db, self.pool(), 'embedded', policy + '-embedded')
                self.assertEqual(self.rows(db, 'SELECT feature,picks FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature', (PREFIX + '%',)),
                                 [(PREFIX + 'before', 2), (PREFIX + 'embedded', 1)])
                self.assertEqual(self.rows(db, 'SELECT COUNT(*) FROM choice_events'), [(2,)])

    def test_adaptive_mixed_position_preference_corrects_and_stays_context_local(self):
        db = self.root / 'adaptive.db'
        learn.learning_policy(str(db), 'adaptive-v1')
        pool = self.pool()
        for index in range(3):
            self.choose(db, pool, 'embedded', 'mixed-' + str(index))
        state = self.dump(db)
        ranked = self.position_only_rank(db, pool)
        self.assertEqual(ranked[0]['id'], 'embedded')
        self.assertGreater(ranked[0]['score'], ranked[1]['score'])
        self.assertTrue(all(row['phrase_score'] == row['label_weight'] == 0 for row in ranked))
        self.assertTrue(all(row['score'] == 0 for row in self.position_only_rank(db, pool, 'results')))
        self.assertEqual(self.dump(db), state)
        for index in range(4):
            self.choose(db, pool, 'fronted', 'front-' + str(index))
        corrected = self.position_only_rank(db, pool[::-1])
        self.assertEqual(corrected[0]['id'], 'fronted')
        self.assertGreater(corrected[0]['score'], corrected[1]['score'])

    def test_uncertain_same_target_still_abstains_and_does_not_reject_history(self):
        for policy in ['legacy', 'adaptive-v1']:
            db = self.root / (policy + '-uncertain.db')
            learn.learning_policy(str(db), policy)
            pool = self.pool()
            self.choose(db, pool, 'embedded', policy + '-seed')
            candidate = pool[1]
            event = next(s for f in candidate['grammar']['frames'] if f['kind'] == 'expression'
                         for s in f['slots'] if s['role'] == 'event' and s['text'] == '读取')
            candidate['grammar']['frames'].append(dict(kind='expression', start=event['start'], end=event['end'],
                text=event['text'], form='unknown', voice='unknown', slots_complete=False, slots=[]))
            pool = learn._validated_candidates(pool)
            self.assertIsNone(learn._grammar_adjunct_positions(pool[1]['grammar'], require_complete=True))
            self.assertEqual(learn._grammar_adjunct_positions(pool[1]['grammar']), ['adjunct_position:temporal:before'])
            before = self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature=?', (PREFIX + 'embedded',))
            comparison = self.rows(db, "SELECT * FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ? ORDER BY feature", ('syntax|' + PREFIX + '%',))
            receipt, _ = self.choose(db, pool, 'embedded', policy + '-unknown')
            self.assertFalse(receipt['adjunct_position_negative_feedback'])
            self.assertEqual(self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature=?', (PREFIX + 'embedded',)), before)
            self.assertEqual(self.rows(db, "SELECT * FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ? ORDER BY feature", ('syntax|' + PREFIX + '%',)), comparison)

    def test_old_approved_annotation_replays_and_cannot_be_replaced_under_same_id(self):
        db = self.root / 'history.db'
        learn.learning_policy(str(db), 'adaptive-v1')
        first, path = self.choose(db, self.pool('original'), 'embedded', 'old-round')
        state = self.dump(db)
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('old receipt must not reparse')):
            replay = learn.choice_file(str(db), 'methods', 'detail', path)
        self.assertEqual(replay, {**first, 'replayed': True})
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.choose(db, self.pool(), 'embedded', 'old-round')
        self.assertEqual(self.dump(db), state)

    def test_literal_binder_disambiguates_the_action_from_quoted_retention_label(self):
        draft = dict(round_id='bind-predicate-targets', candidates=self.pool())
        for candidate in draft['candidates']:
            candidate['grammar'].pop('text_sha256')
            candidate['analysis'].pop('text_sha256')
            for unit in candidate['analysis']['units']:
                for part in unit['parts']:
                    part.pop('start'); part.pop('end')
            for frame in candidate['grammar']['frames']:
                frame.pop('start'); frame.pop('end')
                for slot in frame['slots']:
                    slot.pop('start'); slot.pop('end')
        with self.assertRaisesRegex(ValueError, 'ambiguous|occurrence|multiple'):
            learn.bind_candidate_data(draft)
        for candidate in draft['candidates']:
            for frame in candidate['grammar']['frames']:
                for slot in frame['slots']:
                    if frame['kind'] == 'link' and frame['relation'] == 'temporal' and slot['role'] == 'head' and slot['text'] == '保留':
                        slot['occurrence'] = 2
        bound = learn.bind_candidate_data(draft)
        self.assertEqual(bound['candidates'], self.pool())


if __name__ == '__main__':
    unittest.main()
