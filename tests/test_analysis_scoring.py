"""Rhetorical evidence must not be diluted by unrelated base syntax features."""
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
spec = importlib.util.spec_from_file_location('analysis_score_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/analysis-scoring.json').read_text())


class AnalysisScoringTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-analysis-score-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        clock = patch.object(learn, '_now_iso', return_value='2026-10-27T12:00:00+00:00')
        clock.start()
        self.addCleanup(clock.stop)
        self.pool = deepcopy(FIXTURE['probe']['candidates'])

    def choice(self, db, source, chosen, round_id, author_owned=True):
        data = deepcopy(source)
        data.update(chosen=chosen, round_id=round_id, author_owned=author_owned)
        path = self.root / (round_id + '.json')
        path.write_text(json.dumps(data, ensure_ascii=False))
        return learn.choice_file(str(db), 'methods', 'detail', path)

    def ranks(self, db, pool=None):
        return learn.rank_candidates(str(db), 'methods', 'detail', self.pool if pool is None else pool)

    def test_equal_analysis_signal_is_not_diluted_by_base_feature_count(self):
        results = {}
        for arm in 'ab':
            db = self.root / (arm + '.db')
            self.choice(db, FIXTURE['source_' + arm], 'keep', 'source-' + arm)
            results[arm] = {r['id']: r for r in self.ranks(db)}
        # These real candidates carry the same rhetorical structure; the only
        # varied training input is method versus limitation on identical text.
        deltas = {k: results['a'][k]['score'] - results['b'][k]['score'] for k in results['a']}
        self.assertGreater(min(deltas.values()), 0)
        self.assertAlmostEqual(max(deltas.values()), min(deltas.values()), places=5,
                               msg='untrained base keys must not rescale equal rhetorical evidence')

    def test_identical_author_history_cannot_flip_uniform_analysis_pool(self):
        orders = {}
        for arm in 'ab':
            db = self.root / ('history-' + arm + '.db')
            self.choice(db, FIXTURE['probe'], 'medium', 'medium-' + arm, False)
            for i in range(2):
                self.choice(db, FIXTURE['source_' + arm], 'keep', f'keep-{arm}-{i}')
            rows = self.ranks(db)
            self.assertGreater(rows[0]['score'], rows[1]['score'])
            orders[arm] = [r['id'] for r in rows]
        self.assertEqual(orders['a'], orders['b'], 'identical shared analysis contributes a common offset')

    def test_score_diagnostics_expose_independent_components_and_version(self):
        db = self.root / 'diagnostics.db'
        self.choice(db, FIXTURE['source_a'], 'keep', 'accepted')
        rows = self.ranks(db)
        self.assertEqual(learn.engine_info().get('scoring_version'), 2)
        self.assertIn('independent_analysis_scoring', learn.engine_info()['capabilities'])
        for row in rows:
            self.assertEqual(row.get('scoring_version'), 2)
            self.assertIn('analysis_score', row)
            self.assertAlmostEqual(row['syntax_score'], row['base_syntax_score'] + row['analysis_score']
                                   + row['grammar_score'] + row['analysis_topology_score'], places=5)
            self.assertAlmostEqual(row['score'], row['phrase_score'] + learn.SYNTAX_SCALE * row['syntax_score'], places=5)
        self.assertEqual(len({r['analysis_score'] for r in rows}), 1)

    def test_unknown_analysis_retains_capacity_without_rescaling_base(self):
        db = self.root / 'unknown.db'
        self.choice(db, FIXTURE['source_a'], 'keep', 'known')
        known = deepcopy(self.pool[0])
        masked = deepcopy(known)
        masked['analysis']['units'][0]['move'] = 'unknown'
        full = self.ranks(db, [known])[0]
        partial = self.ranks(db, [masked])[0]
        self.assertEqual(partial['analysis_feature_capacity'], full['analysis_feature_capacity'])
        self.assertLessEqual(partial['score'], full['score'])
        self.assertEqual(partial['base_syntax_score'], full['base_syntax_score'])
        self.assertEqual(partial.get('analysis_score'), 0)

    def test_off_and_legacy_unannotated_scoring(self):
        db = self.root / 'legacy.db'
        text = 'We measured a stable response.'
        data = {'candidates': [{'id': 'keep', 'label': 'Keep original', 'text': text}]}
        self.choice(db, data, 'keep', 'legacy', False)
        row = self.ranks(db, data['candidates'])[0]
        # All retained phrase and base features were selected once with the
        # same reward. No analysis exists to change the original mean formula.
        self.assertEqual(row['phrase_score'], 1000)
        self.assertEqual(row['base_syntax_score'], 1000)
        self.assertEqual(row['score'], 1250)
        self.assertEqual(row.get('analysis_score'), 0)
        learn.set_syntax_backend(str(db), 'off')
        row = self.ranks(db)[0]
        self.assertEqual(row['syntax_backend'], 'off')
        self.assertEqual(row['syntax_score'], 0)
        self.assertEqual(row.get('analysis_score'), 0)

    def test_ranking_does_not_change_weights_or_saved_receipts(self):
        db = self.root / 'readonly.db'
        receipt = self.choice(db, FIXTURE['source_a'], 'keep', 'saved')
        def dump():
            with closing(sqlite3.connect(db)) as conn:
                return list(conn.iterdump())
        before = dump()
        self.ranks(db)
        self.assertEqual(dump(), before)
        replay = self.choice(db, FIXTURE['source_a'], 'keep', 'saved')
        self.assertEqual(replay, {**receipt, 'replayed': True})
        self.assertEqual(dump(), before)
        self.assertNotIn('scoring_version', receipt, 'a saved decision is not a current rank computation')


if __name__ == '__main__':
    unittest.main()
