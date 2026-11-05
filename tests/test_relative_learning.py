"""Production v3 feedback, compatibility, masking, and transaction boundaries."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_relation_coverage import learn, covered, FIXTURE

NOW = datetime(2026, 10, 29, 8, tzinfo=timezone.utc)


class RelativeLearningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sonder-relative-production-')
        self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)
        self.db = str(self.root / 'preference.db'); self.counter = 0
        learn.learning_policy(self.db, 'adaptive-v3')

    def choice(self, sid='T001', chosen='p', *, data=None, at=NOW, database=None, owned=False):
        self.counter += 1
        data = deepcopy(data or covered(sid)); data.update(round_id=f'choice-{self.counter}', chosen=chosen, author_owned=owned)
        path = self.root / (data['round_id'] + '.json'); path.write_text(json.dumps(data, ensure_ascii=False))
        with patch.object(learn, '_now_iso', return_value=at.isoformat()):
            return learn.choice_file(database or self.db, 'methods', 'detail', str(path)), path

    def state(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return {name: sorted(conn.execute('SELECT * FROM ' + name).fetchall(), key=repr)
                    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def rank(self, sid='H001', at=NOW):
        data = covered(sid)
        with patch.object(learn, '_now_iso', return_value=at.isoformat()):
            return learn.rank_candidates(self.db, 'methods', 'detail', data['candidates'], text_format=data.get('text_format'))

    def train_cycle(self, count):
        for sid, chosen in [('T001', 'p'), ('T002', 'a'), ('T003', 'p'), ('T004', 'a')] * count:
            self.choice(sid, chosen)

    def test_new_default_saved_policies_and_version(self):
        fresh = str(self.root / 'fresh.db')
        result = learn.learning_policy(fresh, 'ensure-adaptive')
        self.assertEqual((result['policy'], result['scoring_version']), ('adaptive-v3', 5))
        for policy in ['legacy', 'adaptive-v1', 'adaptive-v2', 'adaptive-v3']:
            learn.learning_policy(self.db, policy); before = self.state()
            self.assertEqual(learn.learning_policy(self.db, 'ensure-adaptive')['policy'], policy)
            self.assertEqual(before, self.state())

    def test_recent_sparse_noise_and_sustained_reversal(self):
        self.train_cycle(1)
        self.assertEqual(self.rank()[0]['id'], 'p')
        self.choice('T001', 'a')
        self.assertEqual(self.rank()[0]['id'], 'p')
        for sid, chosen in [('T002', 'p'), ('T003', 'a'), ('T004', 'p')]:
            self.choice(sid, chosen)
        self.assertEqual(self.rank()[0]['id'], 'a')

    def test_new_state_is_signed_but_readout_nonnegative(self):
        self.choice()
        state = self.state(); self.assertTrue(any(row[2] < 0 for row in state['adaptive_relative']))
        self.assertFalse(any(row[2].startswith(learn._relations.PREFIX) for row in state['adaptive_weights'] if row[1] == 'comparison'))
        with closing(sqlite3.connect(self.db)) as conn:
            weights = learn._adaptive.weights_at(conn, 'methods::detail', NOW, learn._decay)
        self.assertTrue(all(value >= 0 for value in learn._adaptive._relative.readout(weights['relative']).values()))

    def test_unseen_category_and_unequal_mass_gradient(self):
        relative = learn._adaptive._relative
        keys = learn._relations.GROUPS['voice/condition']
        self.assertEqual(relative.readout({keys[0]: 5}), {keys[0]: 5, keys[1]: 0, keys[2]: 0})
        local = {keys[0]: -1, keys[1]: 0, keys[2]: 2}
        difference = {keys[0]: 0.3, keys[1]: -0.1, keys[2]: 0.1}
        actual = relative.gradient(local, difference)
        def score(values):
            return sum(value * relative.readout(values).get(key, 0) for key, value in difference.items())
        for key in keys:
            plus, minus = dict(local), dict(local)
            plus[key] += 1e-6; minus[key] -= 1e-6
            self.assertAlmostEqual(actual.get(key, 0), (score(plus) - score(minus)) / 2e-6, places=8)

    def test_background_unknown_retains_each_possible_group_capacity(self):
        data = covered('M004'); candidate = data['candidates'][0]
        before, _ = learn._relations.project(learn._grammar_observations, candidate, scale=learn.SYNTAX_SCALE)
        for expression in candidate['relation_coverage']['expressions']:
            hidden = deepcopy(candidate)
            row = next(item for item in hidden['relation_coverage']['expressions'] if item['frame'] == expression['frame'])
            row['incoming_complete'] = False
            after, _ = learn._relations.project(learn._grammar_observations, hidden, scale=learn.SYNTAX_SCALE)
            self.assertTrue(all(value <= before.get(key, 0) + 1e-14 for key, value in after.items()))

    def test_unknown_does_not_reward_or_reject_relative_alternative(self):
        self.train_cycle(5); original = self.rank('T001')
        data = covered(); chosen = data['candidates'][0]
        chosen['relation_coverage']['expressions'][0]['incoming_complete'] = False
        with patch.object(learn, '_now_iso', return_value=NOW.isoformat()):
            hidden = learn.rank_candidates(self.db, 'methods', 'detail', data['candidates'])
        original_by_id = {row['id']: row for row in original}
        self.assertTrue(all(row['relation_score'] <= original_by_id[row['id']]['relation_score'] + 1e-12 for row in hidden))
        before = self.state()['adaptive_relative']
        self.choice(data=data, chosen='a')
        self.assertEqual(before, self.state()['adaptive_relative'])

    def test_single_author_text_only_observes(self):
        data = covered(); data['candidates'] = [data['candidates'][0]]
        result, _ = self.choice(data=data, chosen='a', owned=True)
        self.assertTrue(result['author_exemplar'])
        self.assertEqual(self.state()['adaptive_relative'], [])
        self.assertTrue(any(row[1] == 'profile' and row[2].startswith(learn._relations.PREFIX) for row in self.state()['adaptive_weights']))

    def test_optout_and_replay_do_not_touch_learning(self):
        self.train_cycle(1); before = self.state(); data = covered(); data['learn_from_choice'] = False
        result, path = self.choice(data=data)
        self.assertFalse(result['feedback_applied'])
        after = self.state()
        for name in before:
            if name != 'choice_events':
                self.assertEqual(before[name], after[name])
        self.assertTrue(learn.choice_file(self.db, 'methods', 'detail', str(path))['replayed'])
        self.assertEqual(after, self.state())

    def test_receipt_failure_rolls_back_relative_and_native_channels(self):
        self.train_cycle(1); before = self.state()
        with patch.object(learn, '_store_choice_receipt', side_effect=RuntimeError('injected receipt failure')):
            with self.assertRaisesRegex(RuntimeError, 'receipt failure'):
                self.choice(chosen='a')
        self.assertEqual(before, self.state())

    def test_policy_is_rechecked_at_commit(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        original = learn._candidate_syntax_features; called = False
        def extracting(*args, **kwargs):
            nonlocal called
            if not called:
                called = True; learn.learning_policy(self.db, 'adaptive-v3')
            return original(*args, **kwargs)
        with patch.object(learn, '_candidate_syntax_features', side_effect=extracting):
            result, _ = self.choice()
        self.assertEqual(result['adaptive']['policy'], 'adaptive-v3')
        self.assertTrue(self.state()['adaptive_relative'])

    def test_syntax_off_prevents_relative_observation_and_comparison(self):
        learn.set_syntax_backend(self.db, 'off')
        self.choice(); self.assertEqual(self.state()['adaptive_relative'], [])
        self.assertFalse(any(row[2].startswith(learn._relations.PREFIX) for row in self.state()['adaptive_weights']))
        self.assertTrue(all(row['relation_coverage_status'] == 'disabled' and row['relation_score'] == 0 for row in self.rank()))

    def test_policy_switch_keeps_relative_state_resets_transient_trace(self):
        self.train_cycle(1); before = self.state()
        learn.learning_policy(self.db, 'adaptive-v2')
        self.assertEqual(before['adaptive_relative'], self.state()['adaptive_relative'])
        self.assertEqual(self.state()['adaptive_opposition'], [])
        learn.learning_policy(self.db, 'adaptive-v3')
        self.assertEqual(before['adaptive_relative'], self.state()['adaptive_relative'])

    def test_v7_upgrade_preserves_native_history_and_old_receipt(self):
        learn.learning_policy(self.db, 'adaptive-v2')
        data = deepcopy(FIXTURE['pools']['T001']['data']); result, path = self.choice(data=data)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP TABLE adaptive_relative'); conn.execute('PRAGMA user_version=7')
        before = self.state()
        with patch.object(learn, '_now_iso', return_value=NOW.isoformat()):
            old_scores = learn.rank_candidates(self.db, 'methods', 'detail', data['candidates'])
        for table in before:
            self.assertEqual(before[table], self.state()[table])
        self.assertEqual(self.state()['adaptive_relative'], [])
        learn.learning_policy(self.db, 'adaptive-v3')
        with patch.object(learn, '_now_iso', return_value=NOW.isoformat()):
            new_scores = learn.rank_candidates(self.db, 'methods', 'detail', data['candidates'])
        self.assertEqual([(row['id'], row['score']) for row in old_scores], [(row['id'], row['score']) for row in new_scores])
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('old replay must not extract')):
            replay = learn.choice_file(self.db, 'methods', 'detail', str(path))
        self.assertEqual(replay, {**result, 'replayed': True})
        with patch.object(learn, 'SCHEMA_VERSION', 7):
            with self.assertRaisesRegex(ValueError, 'newer Sonder'):
                learn._open(self.db)

    def test_rank_decay_read_only_and_other_context_zero(self):
        self.train_cycle(1); before = self.state()
        first = {row['id']: row for row in self.rank()}; later = self.rank(at=NOW + timedelta(days=30))
        for row in later:
            self.assertAlmostEqual(row['score'], first[row['id']]['score'] / 2)
        self.assertEqual(before, self.state())
        with patch.object(learn, '_now_iso', return_value=NOW.isoformat()):
            other = learn.rank_candidates(self.db, 'results', 'support', covered('H001')['candidates'])
        self.assertTrue(all(row['score'] == 0 for row in other))


if __name__ == '__main__':
    unittest.main()
