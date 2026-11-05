"""Learn adjunct placement from real frame/slot evidence, without cue words."""
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
SPEC = importlib.util.spec_from_file_location('adjunct_learn', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learn)
PREFIX = 'syntax:grammar-v1:writing-model:adjunct_position:'


class AdjunctPositionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sonder-adjunct-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / 'preference.db')
        self.file = self.root / 'decision.json'
        self.candidates = json.loads((ROOT / 'tests/fixtures/grammar-adjunct-placement.json').read_text())['candidates']
        for function, value in (('_syntax_features', ([], 'heuristic')),
                                ('_now_iso', '2026-10-26T00:00:00+00:00')):
            patcher = patch.object(learn, function, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)

    def choose(self, candidates, chosen, event):
        self.file.write_text(json.dumps({'candidates': candidates, 'chosen': chosen,
                                        'author_owned': True, 'round_id': event}, ensure_ascii=False))
        return learn.choice_file(self.db, 'results', 'support', self.file)

    def rank(self, candidates, section='results'):
        return learn.rank_candidates(self.db, section, 'support', candidates)

    def features(self, candidate):
        candidate = learn._validated_candidates([candidate])[0]
        return {x for x in learn._grammar_features(candidate['grammar']) if x.startswith(PREFIX)}

    def snapshot(self):
        with closing(sqlite3.connect(self.db)) as c:
            return list(c.iterdump())

    def test_actual_fronted_and_embedded_conditions_transfer_reverse_and_stay_local(self):
        training, heldout = self.candidates[:2], self.candidates[2:]
        for i in range(3): self.choose(training, 'R05', f'fronted-{i}')
        rows = self.rank(heldout[::-1])
        by_id = {c['id']: c for c in rows}
        self.assertGreater(by_id['R07']['grammar_score'], by_id['R08']['grammar_score'])
        self.assertGreater(by_id['R07']['grammar_scores_by_family']['adjunct_position'], 0)
        self.assertEqual(self.features(training[0]), {PREFIX + 'condition:before'})
        self.assertEqual(self.features(training[1]), {PREFIX + 'condition:embedded'})
        for i in range(5): self.choose(heldout, 'R08', f'embedded-{i}')
        corrected = {c['id']: c for c in self.rank(heldout)}
        self.assertGreater(corrected['R08']['grammar_score'], corrected['R07']['grammar_score'])
        self.assertTrue(all(c['grammar_score'] == 0 for c in self.rank(heldout, section='discussion')))

    def test_existing_weights_scores_and_original_receipt_survive_feature_addition(self):
        training = self.candidates[:2]
        with patch.object(learn, '_grammar_adjunct_positions', return_value=[]):
            first = self.choose(training, 'R05', 'old-feedback')
            old_rank = self.rank(self.candidates[2:])
        before = self.snapshot()
        current_rank = self.rank(self.candidates[2:])
        self.assertEqual([(r['id'], r['score']) for r in current_rank],
                         [(r['id'], r['score']) for r in old_rank])
        self.assertTrue(all(r['grammar_scores_by_family']['adjunct_position'] == 0 for r in current_rank))
        self.assertEqual(self.choose(training, 'R05', 'old-feedback'), {**first, 'replayed': True})
        self.assertEqual(self.snapshot(), before)

    def test_missing_subject_unknown_predicate_or_subject_only_head_abstains(self):
        embedded = self.candidates[1]
        cases = []
        no_subject = deepcopy(embedded)
        matrix = no_subject['grammar']['frames'][0]
        matrix['slots'] = [s for s in matrix['slots'] if s['role'] != 'patient']
        cases.append(no_subject)
        unknown = deepcopy(embedded)
        unknown['grammar']['frames'][0]['form'] = 'unknown'
        cases.append(unknown)
        head_subject = deepcopy(embedded)
        link = next(f for f in head_subject['grammar']['frames'] if f['kind'] == 'link')
        head = next(s for s in link['slots'] if s['role'] == 'head')
        head.update(start=0, end=2, text='振幅')
        cases.append(head_subject)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertEqual(self.features(candidate), set())

    def test_conflicting_duplicate_event_scopes_do_not_invent_position(self):
        candidate = deepcopy(self.candidates[1])
        matrix = deepcopy(candidate['grammar']['frames'][0])
        matrix.update(start=11, end=15, text='保持稳定')
        matrix['slots'] = [s for s in matrix['slots'] if s['role'] != 'patient']
        candidate['grammar']['frames'].append(matrix)
        self.assertEqual(self.features(candidate), set())

    def test_unknown_matrix_form_does_not_resolve_conflicting_scopes(self):
        candidate = deepcopy(self.candidates[1])
        matrix = deepcopy(candidate['grammar']['frames'][0])
        matrix.update(start=11, end=15, text='保持稳定', form='unknown')
        matrix['slots'] = [s for s in matrix['slots'] if s['role'] != 'patient']
        candidate['grammar']['frames'].append(matrix)
        self.assertEqual(self.features(candidate), set())
        self.assertIsNone(learn._grammar_adjunct_positions(candidate['grammar'], require_complete=True))

    def test_unresolved_placement_is_not_negative_position_feedback(self):
        training = deepcopy(self.candidates[:2])
        self.choose(training, 'R05', 'learn-known-position')
        with closing(sqlite3.connect(self.db)) as c:
            before = c.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ?', (PREFIX + '%',)).fetchall()
        embedded = training[1]['grammar']['frames'][0]
        embedded['slots'] = [s for s in embedded['slots'] if s['role'] != 'patient']
        receipt = self.choose(training, 'R06', 'position-is-unknown')
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ?', (PREFIX + '%',)).fetchall(), before)
        self.assertTrue(receipt['grammar_negative_feedback'])
        self.assertFalse(receipt['adjunct_position_negative_feedback'])

    def test_front_and_postposed_cause_use_predicate_evidence_not_condition_keywords(self):
        for text, expected in (('由于温度恒定，振幅保持稳定。', 'before'),
                               ('振幅保持稳定，由于温度恒定。', 'after')):
            def span(fragment):
                start = text.index(fragment)
                return {'start': start, 'end': start + len(fragment), 'text': fragment}
            dependent = {**span('由于温度恒定'), 'role': 'dependent'}
            head = {**span('振幅保持稳定'), 'role': 'head'}
            matrix = {**span(text), 'kind': 'expression', 'form': 'verbal', 'voice': 'nonagentive',
                      'slots': [{**span(fragment), 'role': role} for role, fragment in
                                [('patient', '振幅'), ('event', '保持'), ('complement', '稳定')]]}
            link = {**span(text), 'kind': 'link', 'relation': 'cause', 'slots': [head, dependent]}
            c = {'id': 'cause', 'text': text, 'label': 'Equal label', 'grammar': {
                'version': 2, 'source': 'writing-model', 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'frames': [matrix, link]}}
            self.assertEqual(self.features(c), {PREFIX + 'cause:' + expected})
            c['grammar']['frames'][1]['relation'] = 'unknown'
            self.assertEqual(self.features(c), set())


if __name__ == '__main__':
    unittest.main()
