"""Voice-only uncertainty must not suppress independently grounded placement."""
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
spec = importlib.util.spec_from_file_location('position_feedback_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/voice-uncertain-positions.json').read_text())
CONDITIONS = json.loads((ROOT / 'tests/fixtures/grammar-adjunct-placement.json').read_text())
PREFIX = learn.GRAMMAR_PREFIX + 'adjunct_position:'
NOW = '2026-10-28T08:00:00+00:00'


class IndependentPositionFeedbackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-position-feedback-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sequence = 0
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)

    def pool(self, resolved=False):
        pool = deepcopy(FIXTURE['data']['candidates'][:2])
        if resolved:
            for candidate in pool:
                for frame in candidate['grammar']['frames']:
                    if frame.get('voice') == 'unknown':
                        frame['voice'] = 'passive'  # Controlled annotation variant, not a gold label.
        return learn._validated_candidates(pool)

    def db(self, policy='adaptive-v1'):
        self.sequence += 1
        folder = self.root / str(self.sequence); folder.mkdir()
        db = folder / 'preference.db'
        learn.learning_policy(str(db), policy)
        return db

    def choose(self, db, candidates, chosen='embedded', **extra):
        self.sequence += 1
        data = dict(round_id='trial-' + str(self.sequence), candidates=candidates, chosen=chosen,
                    author_owned=True, learning_context=dict(section='methods', role='detail'))
        data.update(extra)
        path = db.parent / (data['round_id'] + '.json')
        path.write_text(json.dumps(data, ensure_ascii=False))
        return learn.choice_file(str(db), 'methods', 'detail', path), path

    def rows(self, db, sql, params=()):
        with closing(sqlite3.connect(db)) as conn:
            return conn.execute(sql, params).fetchall()

    def dump(self, db):
        with closing(sqlite3.connect(db)) as conn:
            return list(conn.iterdump())

    def position_rank(self, db, pool, section='methods'):
        self.sequence += 1
        copy = self.root / ('positions-' + str(self.sequence) + '.db')
        with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(copy)) as target:
            source.backup(target)
            with target:
                target.execute('DELETE FROM prefs')
                target.execute('DELETE FROM phrase_prefs')
                target.execute('DELETE FROM syntax_prefs WHERE feature NOT LIKE ?', (PREFIX + '%',))
                target.execute('DELETE FROM adaptive_weights WHERE feature NOT LIKE ?', ('syntax|' + PREFIX + '%',))
                target.execute('DELETE FROM adaptive_legacy WHERE feature NOT LIKE ?', ('syntax|' + PREFIX + '%',))
        return learn.rank_candidates(str(copy), section, 'detail', pool)

    def test_real_writer_voice_uncertainty_allows_only_position_comparison(self):
        for policy in ['legacy', 'adaptive-v1']:
            with self.subTest(policy=policy):
                db = self.db(policy)
                receipt, _ = self.choose(db, self.pool())
                self.assertEqual(receipt['grammar_status'], 'partial')
                self.assertFalse(receipt['grammar_negative_feedback'])
                self.assertFalse(receipt['argument_shape_negative_feedback'])
                self.assertFalse(receipt['link_pair_negative_feedback'])
                self.assertTrue(receipt['adjunct_position_negative_feedback'])
                if policy == 'adaptive-v1':
                    keys = self.rows(db, "SELECT feature FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ?", ('syntax|' + learn.GRAMMAR_PREFIX + '%',))
                    self.assertTrue(keys)
                    self.assertTrue(all(key.startswith('syntax|' + PREFIX) for key, in keys))

    def test_actual_mixed_position_learns_reverses_and_stays_in_context(self):
        db = self.db(); pool = self.pool()
        for _ in range(3):
            self.choose(db, pool)
        state = self.dump(db)
        ranked = self.position_rank(db, pool)
        self.assertEqual(ranked[0]['id'], 'embedded')
        self.assertGreater(ranked[0]['score'], ranked[1]['score'])
        self.assertEqual(self.dump(db), state)
        self.assertTrue(all(r['score'] == 0 for r in self.position_rank(db, pool, 'results')))
        for _ in range(4):
            self.choose(db, pool, 'front')
        corrected = self.position_rank(db, pool[::-1])
        self.assertEqual(corrected[0]['id'], 'front')
        self.assertGreater(corrected[0]['score'], corrected[1]['score'])

    def test_voice_location_and_all_accepted_input_versions(self):
        for where in ['nested', 'main', 'all']:
            pool = self.pool(resolved=True)
            for candidate in pool:
                for frame in candidate['grammar']['frames']:
                    if frame['kind'] == 'expression':
                        event = next(s['text'] for s in frame['slots'] if s['role'] == 'event')
                        if where == 'all' or (where == 'main' and event in ['读取', '保存']) or (where == 'nested' and event in ['标记', '计算']):
                            frame['voice'] = 'unknown'
            with self.subTest(where=where):
                self.assertTrue(self.choose(self.db(), pool)[0]['adjunct_position_negative_feedback'])
        for version in range(1, 8):
            pool = deepcopy(CONDITIONS['candidates'][:2])
            for candidate in pool:
                candidate['grammar']['version'] = version
                for frame in candidate['grammar']['frames']:
                    if frame['kind'] == 'expression':
                        frame['voice'] = 'unknown'
                        if version >= 6:
                            frame['slots_complete'] = True
                        if version == 1:
                            frame['slots'] = [s for s in frame['slots'] if s['role'] != 'complement']
            with self.subTest(version=version):
                self.assertTrue(self.choose(self.db(), learn._validated_candidates(pool), pool[0]['id'])[0]['adjunct_position_negative_feedback'])

    def test_nonvoice_uncertainty_absence_and_off_still_block_position_rejection(self):
        for policy in ['legacy', 'adaptive-v1']:
            for change in ['form', 'inventory', 'relation', 'absent', 'empty', 'same-target', 'off']:
                with self.subTest(policy=policy, change=change):
                    db = self.db(policy); self.choose(db, self.pool(resolved=True))
                    pool = self.pool(); candidate = pool[0]
                    nested = next(f for f in candidate['grammar']['frames'] if f.get('voice') == 'unknown')
                    if change == 'form': nested['form'] = 'unknown'
                    elif change == 'inventory': nested['slots_complete'] = False
                    elif change == 'relation': next(f for f in candidate['grammar']['frames'] if f['kind'] == 'link')['relation'] = 'unknown'
                    elif change == 'absent': candidate.pop('grammar')
                    elif change == 'empty': candidate['grammar']['frames'] = []
                    elif change == 'off': learn.set_syntax_backend(str(db), 'off')
                    else:
                        event = next(s for f in candidate['grammar']['frames'] if f['kind'] == 'expression'
                                     for s in f['slots'] if s['role'] == 'event' and s['text'] == '读取')
                        candidate['grammar']['frames'].append(dict(kind='expression', start=event['start'], end=event['end'], text=event['text'],
                            form='unknown', voice='unknown', slots_complete=False, slots=[]))
                    before = self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature=?', (PREFIX + 'temporal:embedded',))
                    comparison = self.rows(db, "SELECT * FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ? ORDER BY feature", ('syntax|' + PREFIX + '%',))
                    receipt, _ = self.choose(db, learn._validated_candidates(pool), 'front')
                    self.assertFalse(receipt['adjunct_position_negative_feedback'])
                    self.assertEqual(self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature=?', (PREFIX + 'temporal:embedded',)), before)
                    self.assertEqual(self.rows(db, "SELECT * FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ? ORDER BY feature", ('syntax|' + PREFIX + '%',)), comparison)

    def test_position_rejection_does_not_reject_unknown_voice_or_other_grammar(self):
        for policy in ['legacy', 'adaptive-v1']:
            db = self.db(policy); self.choose(db, self.pool(resolved=True))
            voices = self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature', (learn.GRAMMAR_PREFIX + 'voice:%',))
            other = self.rows(db, "SELECT * FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ? AND feature NOT LIKE ? ORDER BY feature", ('syntax|' + learn.GRAMMAR_PREFIX + '%', 'syntax|' + PREFIX + '%'))
            old_position = self.rows(db, 'SELECT weight FROM syntax_prefs WHERE feature=?', (PREFIX + 'temporal:embedded',))[0][0]
            pool = self.pool(resolved=True)
            # The unchosen alternative keeps known voices, so an accidentally
            # broadened gate would actually reject those existing voice rows.
            for frame in pool[0]['grammar']['frames']:
                if frame['kind'] == 'expression': frame['voice'] = 'unknown'
            receipt, _ = self.choose(db, pool, 'front')
            self.assertTrue(receipt['adjunct_position_negative_feedback'])
            self.assertFalse(receipt['grammar_negative_feedback'])
            self.assertLess(self.rows(db, 'SELECT weight FROM syntax_prefs WHERE feature=?', (PREFIX + 'temporal:embedded',))[0][0], old_position)
            self.assertEqual(self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature', (learn.GRAMMAR_PREFIX + 'voice:%',)), voices)
            self.assertEqual(self.rows(db, "SELECT * FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ? AND feature NOT LIKE ? ORDER BY feature", ('syntax|' + learn.GRAMMAR_PREFIX + '%', 'syntax|' + PREFIX + '%')), other)

    def test_conflicting_observations_do_not_become_voice_only_uncertainty(self):
        for field in ['form', 'roles']:
            pool = self.pool(); candidate = pool[0]
            nested = next(f for f in candidate['grammar']['frames'] if f.get('voice') == 'unknown' and any(s['role'] == 'complement' for s in f['slots']))
            duplicate = deepcopy(nested); duplicate['end'] += 1; duplicate['text'] = candidate['text'][duplicate['start']:duplicate['end']]
            if field == 'form': duplicate['form'] = 'nominalized' if nested['form'] == 'verbal' else 'verbal'
            else: next(s for s in duplicate['slots'] if s['role'] == 'complement')['role'] = 'patient'
            candidate['grammar']['frames'].append(duplicate)
            pool = learn._validated_candidates(pool)
            receipt, _ = self.choose(self.db(), pool, 'front')
            self.assertFalse(receipt['adjunct_position_negative_feedback'])

    def test_genuine_historical_receipt_replays_without_new_eligibility(self):
        db = self.db(); row = FIXTURE['historical_event']; data = FIXTURE['historical_input']
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('INSERT INTO choice_events(round_id,payload_hash,result_json,created) VALUES(?,?,?,?)',
                         tuple(row[k] for k in ['round_id','payload_hash','result_json','created']))
        path = self.root / 'historical.json'; path.write_text(json.dumps(data, ensure_ascii=False))
        state = self.dump(db)
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('old receipt must not reparse')):
            replay = learn.choice_file(str(db), 'methods', 'detail', path)
        self.assertEqual(replay, {**json.loads(row['result_json']), 'replayed': True})
        self.assertFalse(replay['adjunct_position_negative_feedback'])
        self.assertEqual(self.dump(db), state)

    def test_editorial_optout_does_not_extract_or_update_positions(self):
        db = self.db(); self.choose(db, self.pool(resolved=True))
        positions = self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature', (PREFIX + '%',))
        model = self.rows(db, 'SELECT * FROM adaptive_weights ORDER BY channel,feature')
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('opt-out must not extract')):
            receipt, _ = self.choose(db, self.pool(), learn_from_choice=False)
        self.assertFalse(receipt['feedback_applied'])
        self.assertEqual(self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature', (PREFIX + '%',)), positions)
        self.assertEqual(self.rows(db, 'SELECT * FROM adaptive_weights ORDER BY channel,feature'), model)


if __name__ == '__main__': unittest.main()
