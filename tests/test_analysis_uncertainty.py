"""Removing rhetorical evidence cannot invent preference or inflate its mean."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('analysis_uncertainty', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
FIELDS = ('move', 'polarity', 'certainty')


def candidate(signatures):
    clauses = ['扫描样本', '记录响应', '计算误差', '比较结果'][:len(signatures)]
    text = '，'.join(clauses) + '。'
    draft = {'round_id': 'draft', 'candidates': [{'id': 'selected', 'label': 'Neutral', 'text': text,
        'analysis': {'spans': [{'text': clause, **dict(zip(FIELDS, signature))} for clause, signature in zip(clauses, signatures)]},
        'grammar': {'frames': []}}]}
    return learn.bind_candidate_data(draft)['candidates'][0]


class AnalysisUncertaintyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='sonder-analysis-mask-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = str(self.root / 'preference.db')
        clock = patch.object(learn, '_now_iso', return_value='2026-10-27T01:00:00+00:00')
        clock.start(); self.addCleanup(clock.stop)

    def rank(self, item):
        return learn.rank_candidates(self.db, 'results', 'support', [item])[0]

    def test_masked_single_unit_cannot_gain_from_weak_feature_removal(self):
        item = candidate([('result', 'affirmative', 'asserted')])
        def weight(conn, context, feature, now=None):
            return (200 if feature.startswith(learn.ANALYSIS_PREFIX) else 1000), 1
        with patch.object(learn, '_syntax_weight_of', side_effect=weight):
            original = self.rank(item)
            for field in FIELDS:
                masked = deepcopy(item)
                masked['analysis']['spans'][0][field] = 'unknown'
                with self.subTest(field=field):
                    row = self.rank(masked)
                    self.assertLessEqual(row['score'], original['score'])
                    self.assertEqual(row.get('analysis_feature_capacity'), 2)

    def test_uncertain_merge_does_not_create_middle_positions_or_repeated_sequence(self):
        item = candidate([('method','affirmative','asserted'), ('method','affirmative','asserted'), ('result','affirmative','asserted')])
        complete = set(learn._analysis_features(item['analysis']))
        masked = deepcopy(item)
        masked['analysis']['spans'][1]['certainty'] = 'unknown'
        safe = set(learn._analysis_features(masked['analysis']))
        self.assertLessEqual(safe, complete)
        self.assertIn(learn.ANALYSIS_PREFIX + 'move_pos:result@last', safe)
        self.assertIn(learn.ANALYSIS_PREFIX + 'move_pos:method@first', safe)
        self.assertNotIn(learn.ANALYSIS_PREFIX + 'move_pos:method@middle', safe)
        self.assertNotIn(learn.ANALYSIS_PREFIX + 'move_seq:method>method>result', safe)

    def test_additional_unknowns_cannot_add_features_or_raise_scores(self):
        cases = [
            [('method','affirmative','asserted'), ('method','affirmative','asserted'), ('result','negative','asserted')],
            [('result','affirmative','asserted'), ('result','affirmative','hedged'), ('result','affirmative','asserted')],
            [('condition','affirmative','unknown'), ('interpretation','unknown','hedged'), ('limitation','affirmative','asserted')],
        ]
        def weight(conn, context, feature, now=None):
            return int(hashlib.sha256(feature.encode()).hexdigest()[:8], 16) % 3001, 1
        with patch.object(learn, '_syntax_weight_of', side_effect=weight):
            for signatures in cases:
                original = candidate(signatures)
                masks = [(i, field) for i, span in enumerate(original['analysis']['spans']) for field in FIELDS if span[field] != 'unknown']
                for first in [None, *masks]:
                    partial = deepcopy(original)
                    if first:
                        partial['analysis']['spans'][first[0]][first[1]] = 'unknown'
                    features = set(learn._analysis_features(partial['analysis']))
                    score = self.rank(partial)['score']
                    for i, field in masks:
                        masked = deepcopy(partial)
                        masked['analysis']['spans'][i][field] = 'unknown'
                        with self.subTest(signatures=signatures, first=first, next=(i,field)):
                            self.assertLessEqual(set(learn._analysis_features(masked['analysis'])), features)
                            self.assertLessEqual(self.rank(masked)['score'], score + 1e-6)

    def test_known_unaffected_feedback_survives_but_ambiguous_keys_are_not_rewarded(self):
        item = candidate([('method','affirmative','asserted'), ('method','affirmative','unknown'), ('result','affirmative','asserted')])
        file = self.root / 'choice.json'
        file.write_text(json.dumps({'round_id':'partial-choice','candidates':[item],'chosen':item['id'],'author_owned':True}, ensure_ascii=False))
        receipt = learn.choice_file(self.db, 'results', 'support', file)
        self.assertFalse(receipt['analysis_negative_feedback'])
        with closing(sqlite3.connect(self.db)) as c:
            keys = {row[0] for row in c.execute('SELECT feature FROM syntax_prefs')}
        self.assertIn(learn.ANALYSIS_PREFIX + 'move_pos:result@last', keys)
        self.assertNotIn(learn.ANALYSIS_PREFIX + 'move_pos:method@middle', keys)
        self.assertGreater(receipt.get('analysis_feature_capacity', 0), receipt['analysis_features'])

    def test_complete_normalization_and_unknown_split_boundaries_keep_their_contract(self):
        item = candidate([('method','affirmative','asserted'), ('method','affirmative','asserted'), ('result','negative','asserted')])
        features = set(learn._analysis_features(item['analysis']))
        expected = {'move_pos:method@first', 'signature_pos:method/affirmative/asserted@first',
                    'move_pos:result@last', 'signature_pos:result/negative/asserted@last',
                    'adjacent:method>result', 'move_seq:method>result'}
        self.assertEqual(features, {learn.ANALYSIS_PREFIX + f for f in expected})
        self.assertEqual(self.rank(item).get('analysis_feature_capacity'), len(features))
        original = deepcopy(item)
        original['analysis']['spans'][2]['polarity'] = 'unknown'
        merged = deepcopy(original)
        spans = merged['analysis']['spans']
        spans[0]['end'] = spans[1]['end']
        spans[0]['text'] = item['text'][spans[0]['start']:spans[0]['end']]
        spans.pop(1)
        self.assertEqual(learn._analysis_features(original['analysis']), learn._analysis_features(merged['analysis']))
        self.assertEqual(self.rank(original)['score'], self.rank(merged)['score'])
        self.assertEqual(self.rank(original).get('analysis_feature_capacity'), self.rank(merged).get('analysis_feature_capacity'))


if __name__ == '__main__': unittest.main()
