"""Versioned local correction: migration, switching and atomic author feedback."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('local_policy_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/adaptive-learning.json').read_text())
NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)


class LocalOppositionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-local-policy-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = str(self.root / 'preference.db')
        track = next(t for t in FIXTURE['tracks'] if t['name'] == 'old_tracks/paired')
        self.forward = deepcopy(FIXTURE['decisions'][track['events'][0]['decision']])
        self.reverse = deepcopy(FIXTURE['decisions'][track['events'][4]['decision']])
        learn.learning_policy(self.db, 'adaptive-v1')
        for i in range(4):
            self.choice(self.forward, 'old-' + str(i))

    def choice(self, data, name, at=NOW, db=None):
        path = self.root / (name + '.json')
        path.write_text(json.dumps(dict(data, round_id=name), ensure_ascii=False))
        with patch.object(learn, '_now_iso', return_value=at.isoformat()):
            return learn.choice_file(db or self.db, **data['learning_context'], path=path)

    def rows(self, table, db=None):
        with closing(sqlite3.connect(db or self.db)) as c:
            return sorted(c.execute('SELECT * FROM ' + table).fetchall(), key=repr)

    def state(self, db=None):
        with closing(sqlite3.connect(db or self.db)) as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {t: sorted(c.execute('SELECT * FROM ' + t).fetchall(), key=repr) for t in tables}

    def rank(self, at=NOW):
        with patch.object(learn, '_now_iso', return_value=at.isoformat()):
            return learn.rank_candidates(self.db, **self.forward['learning_context'], candidates=self.forward['candidates'], text_format='markdown')

    def test_default_is_v3_but_ensure_preserves_every_saved_policy(self):
        fresh = str(self.root / 'fresh.db')
        state = learn.learning_policy(fresh, 'ensure-adaptive')
        self.assertEqual((state['policy'], state['scoring_version']), ('adaptive-v3', 5))
        for policy in ['adaptive-v1', 'legacy', 'adaptive-v2', 'adaptive-v3']:
            learn.learning_policy(self.db, policy)
            before = self.state()
            self.assertEqual(learn.learning_policy(self.db, 'ensure-adaptive')['policy'], policy)
            self.assertEqual(self.state(), before)

    def test_enable_preserves_old_parameters_scores_times_and_receipts(self):
        before = self.state()
        scores = [(r['id'], r['score']) for r in self.rank()]
        state = learn.learning_policy(self.db, 'adaptive-v2')
        self.assertEqual(state['scoring_version'], 4)
        for table in before:
            if table != 'adaptive_settings':
                self.assertEqual(self.rows(table), before[table])
        after = self.rank()
        self.assertEqual([(r['id'], r['score']) for r in after], scores)
        self.assertTrue(all(r['learning_policy'] == 'adaptive-v2' and r['scoring_version'] == 4 for r in after))
        self.assertEqual(self.rows('adaptive_opposition'), [])

    def test_switches_preserve_shared_learning_and_reset_only_transient_trace(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        self.choice(self.reverse, 'v2-change')
        self.assertTrue(self.rows('adaptive_opposition'))
        state = self.state()
        self.assertFalse(learn.learning_policy(self.db, 'adaptive-v2')['changed'])
        self.assertEqual(self.state(), state)
        learn.learning_policy(self.db, 'adaptive-v1')
        self.assertEqual(self.rows('adaptive_opposition'), [])
        self.assertEqual(self.rows('adaptive_weights'), state['adaptive_weights'])
        self.choice(self.forward, 'v1-change')
        learned = self.rows('adaptive_weights')
        learn.learning_policy(self.db, 'adaptive-v2')
        self.assertEqual(self.rows('adaptive_weights'), learned)
        self.assertEqual(self.rows('adaptive_opposition'), [])
        self.choice(self.reverse, 'v2-change-again')
        learned = self.rows('adaptive_weights')
        learn.learning_policy(self.db, 'legacy')
        self.assertEqual(self.rows('adaptive_opposition'), [])
        self.choice(self.forward, 'legacy-choice')
        learn.learning_policy(self.db, 'adaptive-v2')
        self.assertEqual(self.rows('adaptive_weights'), learned)
        self.assertEqual(self.rows('adaptive_opposition'), [])

    def test_failed_policy_change_restores_cleared_trace_and_setting(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        self.choice(self.reverse, 'opposition')
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("CREATE TRIGGER fail_policy BEFORE UPDATE ON adaptive_settings WHEN NEW.key='policy' BEGIN SELECT RAISE(ABORT,'policy fault'); END")
        before = self.state()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'policy fault'):
            learn.learning_policy(self.db, 'adaptive-v1')
        self.assertEqual(self.state(), before)

    def test_schema6_upgrade_failure_rolls_back_new_table_and_version(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('DROP TABLE adaptive_opposition')
            c.execute('PRAGMA user_version=6')
        before = self.state()
        create = learn._adaptive.create_schema
        def fail(c):
            create(c)
            raise RuntimeError('schema fault')
        with patch.object(learn._adaptive, 'create_schema', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'schema fault'):
                learn.learning_policy(self.db)
        self.assertEqual(self.state(), before)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 6)
        self.assertEqual(learn.learning_policy(self.db)['policy'], 'adaptive-v1')
        after = self.state()
        self.assertEqual({k: after[k] for k in before}, before)
        self.assertEqual(after['adaptive_opposition'], [])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], learn.SCHEMA_VERSION)

    def test_old_receipt_and_optout_bypass_learning_under_v2(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        self.choice(self.reverse, 'opposition')
        before = self.state()
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('no extraction')), \
             patch.object(learn._adaptive, 'update', side_effect=AssertionError('no update')):
            receipt = self.choice(self.forward, 'old-0')
            self.assertTrue(receipt['replayed'])
            self.assertEqual(receipt['adaptive']['policy'], 'adaptive-v1')
            self.assertEqual(self.state(), before)
            receipt = self.choice(dict(self.forward, learn_from_choice=False), 'excluded')
            self.assertFalse(receipt['feedback_applied'])
        after = self.state()
        self.assertEqual({k: v for k, v in after.items() if k != 'choice_events'}, {k: v for k, v in before.items() if k != 'choice_events'})

    def test_receipt_fault_rolls_back_model_and_trace_before_retry(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        before = self.state()
        observed = []
        def fail(c, *args):
            observed.append(c.execute('SELECT COUNT(*) FROM adaptive_opposition').fetchone()[0])
            raise RuntimeError('receipt fault')
        with patch.object(learn, '_store_choice_receipt', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'receipt fault'):
                self.choice(self.reverse, 'new')
        self.assertGreater(observed[0], 0)
        self.assertEqual(self.state(), before)
        clean = str(self.root / 'clean.db')
        with closing(sqlite3.connect(self.db)) as a, closing(sqlite3.connect(clean)) as b:
            a.backup(b)
        self.assertEqual(self.choice(self.reverse, 'new'), self.choice(self.reverse, 'new', db=clean))
        self.assertEqual(self.state(), self.state(clean))

    def test_unknown_or_single_observation_does_not_add_comparison_trace(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        self.choice(self.reverse, 'opposition')
        grammar = lambda rows: [r for r in rows if r[1].startswith('syntax|' + learn.GRAMMAR_PREFIX)]
        traces = grammar(self.rows('adaptive_opposition'))
        self.assertTrue(traces)
        unknown = deepcopy(self.forward)
        for candidate in unknown['candidates']:
            for frame in candidate['grammar']['frames']:
                if frame['kind'] == 'expression':
                    frame['form'] = 'unknown'
        self.choice(unknown, 'unknown', NOW + timedelta(days=1))
        self.assertEqual(grammar(self.rows('adaptive_opposition')), traces)
        one = deepcopy(self.forward)
        one['candidates'] = [c for c in one['candidates'] if c['id'] == one['chosen']]
        one.pop('edit_scope', None)
        before = self.rows('adaptive_opposition')
        self.choice(one, 'observation', NOW + timedelta(days=2))
        self.assertEqual(self.rows('adaptive_opposition'), before)

    def test_trace_has_no_cross_context_or_read_time_writes(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        self.choice(self.reverse, 'opposition')
        before = self.state()
        old = {r['id']: r for r in self.rank()}
        later = {r['id']: r for r in self.rank(NOW + timedelta(days=30))}
        for cid in old:
            for key in ['comparison_score', 'profile_score']:
                self.assertAlmostEqual(later[cid][key], old[cid][key] / 2, places=12)
        self.assertEqual(self.state(), before)
        other = deepcopy(self.forward)
        other['learning_context'] = {'section': 'results', 'role': 'support'}
        self.choice(other, 'other-context')
        self.assertEqual([r for r in self.rows('adaptive_opposition') if r[0] == 'methods::detail'], before['adaptive_opposition'])

    def test_off_backend_does_not_reject_existing_structure_or_refresh_its_trace(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        self.choice(self.reverse, 'opposition')
        prefix = 'syntax|' + learn.GRAMMAR_PREFIX
        traces = [r for r in self.rows('adaptive_opposition') if r[1].startswith(prefix)]
        weights = {r[2]: r[3] for r in self.rows('adaptive_weights') if r[1] == 'comparison' and r[2].startswith(prefix)}
        self.assertTrue(traces and weights)
        learn.set_syntax_backend(self.db, 'off')
        receipt = self.choice(self.forward, 'off-choice', NOW + timedelta(days=1))
        self.assertEqual(receipt['syntax_backend'], 'off')
        self.assertEqual(receipt['adaptive']['contrast_feature_counts']['grammar'], 0)
        self.assertEqual([r for r in self.rows('adaptive_opposition') if r[1].startswith(prefix)], traces)
        after = {r[2]: r[3] for r in self.rows('adaptive_weights') if r[1] == 'comparison' and r[2].startswith(prefix)}
        self.assertEqual(after.keys(), weights.keys())
        for key, value in weights.items():
            self.assertAlmostEqual(after[key], value * 2 ** (-1 / 30), places=13)

    def test_direct_operator_matches_independent_formula_and_clears_positive_trace(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        key = 'phrase|operator-test'
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO adaptive_weights VALUES(?,?,?,?,?)', ('operator::detail', 'comparison', key, 2.0, NOW.isoformat()))
            for step in range(1, 6):
                old = c.execute('SELECT weight FROM adaptive_weights WHERE context=? AND feature=?', ('operator::detail', key)).fetchone()[0]
                trace = float(step)
                weakened = old * math.exp(-math.log(2) * trace / 32)
                expected = max(0, weakened - 0.5 / (1 + math.exp(-weakened * 0.5)))
                learn._adaptive.update(c, 'operator::detail', {}, [{key: -0.5}], False, NOW, learn._decay, learn._adaptive_feature_family, learn.CEILING)
                actual = c.execute('SELECT weight FROM adaptive_weights WHERE context=? AND feature=?', ('operator::detail', key)).fetchone()[0]
                self.assertAlmostEqual(actual, expected, places=13)
                self.assertEqual(c.execute('SELECT value FROM adaptive_opposition WHERE context=? AND feature=?', ('operator::detail', key)).fetchone()[0], trace)
            learn._adaptive.update(c, 'operator::detail', {key: 0.5}, [{key: 0.5}], False, NOW, learn._decay, learn._adaptive_feature_family, learn.CEILING)
            self.assertEqual(c.execute('SELECT value FROM adaptive_opposition WHERE context=? AND feature=?', ('operator::detail', key)).fetchone()[0], 0)

    def test_v1_future_updates_do_not_create_opposition(self):
        self.choice(self.reverse, 'legacy-adaptive-choice')
        self.assertEqual(self.rows('adaptive_opposition'), [])
        self.assertTrue(all(r['scoring_version'] == 3 for r in self.rank()))

    def test_policy_is_read_at_the_serialized_feedback_not_before_extraction(self):
        original = learn._candidate_syntax_features
        for initial, target in [('adaptive-v1', 'adaptive-v2'), ('adaptive-v2', 'adaptive-v1')]:
            with self.subTest(initial=initial, target=target):
                learn.learning_policy(self.db, initial)
                switched = []
                def interposed(*args, **kwargs):
                    if not switched:
                        switched.append(learn.learning_policy(self.db, target))
                    return original(*args, **kwargs)
                with patch.object(learn, '_candidate_syntax_features', side_effect=interposed):
                    receipt = self.choice(self.reverse, 'commit-under-' + target)
                self.assertEqual(receipt['adaptive']['policy'], target)
                self.assertEqual(bool(self.rows('adaptive_opposition')), target == 'adaptive-v2')

    def test_many_candidate_positive_evidence_cannot_round_above_one(self):
        # Repeated addition of 1/9, 1/11, ... can exceed one in binary floats.
        # Exercise a real saved round with distinct texts, not fabricated keys.
        for count in [9, 11, 18]:
            with self.subTest(opponents=count):
                db = str(self.root / f'many-{count}.db')
                learn.learning_policy(db, 'adaptive-v2')
                selected = dict(id='chosen', label='Equal', text='retainword')
                opponents = [dict(id=f'opponent-{i}', label='Equal', text='contrastword opponent' + chr(97 + i)) for i in range(count)]
                initial = dict(learning_context=dict(section='methods', role='detail'),
                    candidates=[selected, opponents[0]], chosen=opponents[0]['id'])
                self.choice(initial, f'many-{count}-old', db=db)
                old = [r for r in self.rows('adaptive_opposition', db) if 'retainword' in r[1]]
                self.assertEqual(len(old), 1)
                self.assertEqual(old[0][2], 1.0)
                future = dict(initial, candidates=[selected, *opponents], chosen='chosen')
                self.choice(future, f'many-{count}-new', db=db)
                current = [r for r in self.rows('adaptive_opposition', db) if 'retainword' in r[1]]
                self.assertEqual(current[0][2], 0.0)
                negative = [r for r in self.rows('adaptive_opposition', db) if 'contrastword' in r[1]]
                self.assertEqual(negative[0][2], 1.0)
                self.assertEqual(len(self.rows('choice_events', db)), 2)

    def test_tiny_relative_difference_is_not_a_whole_opposition_event(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        stamp = (NOW - timedelta(days=1)).isoformat()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO adaptive_opposition VALUES(?,?,?,?)', ('probe::detail', 'phrase|common', 2.0, NOW.isoformat()))
            c.execute('INSERT INTO adaptive_opposition VALUES(?,?,?,?)', ('probe::detail', 'phrase|untouched', 4.0, stamp))
            weights = {'phrase|common': 8.0, 'phrase|untouched': 3.0}
            learn._adaptive._opposition_feedback(c, 'probe::detail', weights, {'phrase|common': 0.999}, [{'phrase|common': -0.001}], NOW, learn._decay)
            trace = c.execute('SELECT value FROM adaptive_opposition WHERE context=? AND feature=?', ('probe::detail', 'phrase|common')).fetchone()[0]
            self.assertAlmostEqual(trace, 2.001, places=13)
            self.assertAlmostEqual(weights['phrase|common'], 8 * math.exp(-math.log(2) * 0.001 * 2.001 / 32), places=13)
            self.assertEqual(weights['phrase|untouched'], 3.0)
            self.assertEqual(c.execute('SELECT value,last_at FROM adaptive_opposition WHERE context=? AND feature=?', ('probe::detail', 'phrase|untouched')).fetchone(), (4.0, stamp))

    def test_trace_cap_fractional_return_and_calendar_decay(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        key = 'phrase|trace-cap'
        with closing(sqlite3.connect(self.db)) as c, c:
            for _ in range(40):
                learn._adaptive._opposition_feedback(c, 'probe::detail', {}, {}, [{key: -1.0}], NOW, learn._decay)
            read = lambda: c.execute('SELECT value FROM adaptive_opposition WHERE context=? AND feature=?', ('probe::detail', key)).fetchone()[0]
            self.assertEqual(read(), 32)
            learn._adaptive._opposition_feedback(c, 'probe::detail', {}, {key: 1.0}, [{key: 0.001}], NOW, learn._decay)
            self.assertAlmostEqual(read(), 32 * 0.999, places=13)
            learn._adaptive._opposition_feedback(c, 'probe::detail', {}, {}, [{key: -1.0}], NOW + timedelta(days=30), learn._decay)
            self.assertAlmostEqual(read(), 16 * 0.999 + 1, places=13)


if __name__ == '__main__':
    unittest.main()
