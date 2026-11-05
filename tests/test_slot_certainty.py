"""Unresolved argument inventories must not teach complete slot templates."""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('slot_certainty_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)


def draft(*, complete=True, version=None, patient=True):
    text = '研究者测量了信号。'
    frame = {'kind': 'expression', 'text': text, 'form': 'verbal', 'voice': 'active',
             'slots_complete': complete, 'slots': [{'role': 'agent', 'text': '研究者'}, {'role': 'event', 'text': '测量'}]}
    if patient:
        frame['slots'].append({'role': 'patient', 'text': '信号'})
    grammar = {'frames': [frame]}
    if version is not None:
        grammar['version'] = version
    return {'round_id': 'slots-draft', 'candidates': [
        {'id': 'one', 'label': 'Neutral', 'text': text, 'analysis': {'units': []}, 'grammar': grammar}]}


class SlotCertaintyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-slot-certainty-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = str(self.root / 'preference.db')
        self.file = self.root / 'choice.json'
        clock = patch.object(learn, '_now_iso', return_value='2026-10-27T06:00:00+00:00')
        clock.start()
        self.addCleanup(clock.stop)

    def bind(self, **kwargs):
        try:
            return learn.bind_candidate_data(draft(**kwargs))['candidates'][0]
        except ValueError as error:
            self.fail('v6 slot certainty must bind: ' + str(error))

    def choose(self, items, chosen='one', round_id='choice', **extra):
        self.file.write_text(json.dumps({'round_id': round_id, 'candidates': items,
                                       'chosen': chosen, 'author_owned': True, **extra}))
        return learn.choice_file(self.db, 'methods', 'detail', self.file)

    def rows(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute('SELECT * FROM syntax_prefs ORDER BY feature').fetchall()

    def rank(self, item):
        return learn.rank_candidates(self.db, 'methods', 'detail', [item])[0]

    def test_v6_requires_boolean_for_each_expression_and_preserves_old_draft_defaults(self):
        item = self.bind()
        self.assertEqual(item['grammar']['version'], 6)
        self.assertIn(6, learn.engine_info()['input_versions']['grammar'])
        for value in (None, 0, 1, 'false', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                learn.bind_candidate_data(draft(complete=value))
        for version in (1, 2, 3, 4, 5):
            with self.subTest(version=version), self.assertRaises(ValueError):
                learn.bind_candidate_data(draft(version=version))
        missing = draft(version=6)
        del missing['candidates'][0]['grammar']['frames'][0]['slots_complete']
        with self.assertRaises(ValueError):
            learn.bind_candidate_data(missing)
        missing['candidates'][0]['grammar'].pop('version')
        self.assertEqual(learn.bind_candidate_data(missing)['candidates'][0]['grammar']['version'], 2)
        self.assertFalse(Path(self.db).exists())

    def test_explicit_partial_retains_known_form_voice_without_inventing_shape(self):
        item = self.bind(complete=False, patient=False)
        self.assertEqual(learn._grammar_status(item), 'partial')
        expected = {learn.GRAMMAR_PREFIX + 'form:verbal', learn.GRAMMAR_PREFIX + 'voice:active'}
        self.assertEqual(set(learn._grammar_features(item['grammar'])), expected)
        receipt = self.choose([item])
        self.assertFalse(receipt['grammar_negative_feedback'])
        self.assertFalse(receipt['argument_shape_negative_feedback'])
        self.assertEqual({r[1] for r in self.rows() if r[1].startswith(learn.GRAMMAR_PREFIX)}, expected)
        self.assertTrue(all(r[2] > 0 for r in self.rows() if r[1] in expected))

    def test_masking_role_inventory_does_not_match_learned_incomplete_template(self):
        # A legacy producer already taught this short template. Keep its row,
        # but do not let a newly declared partial inventory match it.
        legacy = draft(patient=False)
        del legacy['candidates'][0]['grammar']['frames'][0]['slots_complete']
        old = learn.bind_candidate_data(legacy)['candidates'][0]
        self.choose([old])
        complete = self.bind()
        partial = self.bind(complete=False, patient=False)
        a, b = self.rank(complete), self.rank(partial)
        self.assertLessEqual(b['grammar_score'], a['grammar_score'])
        self.assertTrue(all(not learn._is_argument_shape(row['feature']) for row in b['matched_grammar_features']))
        self.assertTrue(any(learn._is_argument_shape(row[1]) for row in self.rows()))

    def test_unknown_and_incomplete_share_zero_reservations_and_cannot_raise_scores(self):
        item = self.bind()
        self.choose([item])
        for form in ('verbal', 'unknown'):
            for voice in ('active', 'unknown'):
                complete = deepcopy(item)
                frame = complete['grammar']['frames'][0]
                frame.update(form=form, voice=voice)
                partial = deepcopy(complete)
                partial['grammar']['frames'][0]['slots_complete'] = False
                with self.subTest(form=form, voice=voice):
                    self.assertLessEqual(self.rank(partial)['grammar_score'], self.rank(complete)['grammar_score'])
                    counts = learn._grammar_unknown_counts(partial['grammar'])
                    self.assertEqual(counts['expression_slots'], 1)
                    self.assertEqual(counts['expression'], 1)

    def test_complete_v6_preserves_old_keys_scores_and_feedback(self):
        modern = self.bind()
        legacy = deepcopy(modern)
        legacy['grammar']['version'] = 5
        del legacy['grammar']['frames'][0]['slots_complete']
        self.assertEqual(learn._grammar_features(modern['grammar']), learn._grammar_features(legacy['grammar']))
        self.choose([legacy])
        self.assertEqual(self.rank(modern)['score'], self.rank(legacy)['score'])
        self.assertEqual(self.rank(modern)['grammar_status'], 'complete')
        self.assertTrue(self.choose([modern], round_id='modern')['grammar_negative_feedback'])

    def test_each_incomplete_frame_keeps_a_zero_contribution_beside_known_frames(self):
        item = self.bind()
        self.choose([item])
        data = draft()
        candidate = data['candidates'][0]
        for subject, event, patient in [('团队', '分析', '误差'), ('操作者', '记录', '位移')]:
            text = subject + event + '了' + patient + '。'
            candidate['text'] += text
            candidate['grammar']['frames'].append({
                'kind': 'expression', 'text': text, 'form': 'verbal', 'voice': 'active',
                'slots_complete': True,
                'slots': [{'role': role, 'text': fragment} for role, fragment in
                          [('agent', subject), ('event', event), ('patient', patient)]]})
        candidate = learn.bind_candidate_data(data)['candidates'][0]
        previous = self.rank(candidate)['grammar_score']
        for count in (1, 2):
            candidate['grammar']['frames'][count]['slots_complete'] = False
            current = self.rank(candidate)['grammar_score']
            self.assertLess(current, previous)
            self.assertEqual(learn._grammar_unknown_counts(candidate['grammar'])['expression_slots'], count)
            previous = current

    def test_partial_feedback_replay_optout_and_off_preserve_rows(self):
        full = self.bind()
        self.choose([full], round_id='seed')
        partial = self.bind(complete=False, patient=False)
        before = {r[1]: r for r in self.rows() if learn._is_argument_shape(r[1])}
        receipt = self.choose([partial], round_id='partial')
        self.assertEqual({r[1]: r for r in self.rows() if learn._is_argument_shape(r[1])}, before)
        rows = self.rows()
        self.assertEqual(self.choose([partial], round_id='partial'), {**receipt, 'replayed': True})
        self.assertEqual(self.rows(), rows)
        self.assertFalse(self.choose([partial], round_id='skip', learn_from_choice=False)['feedback_applied'])
        self.assertEqual(self.rows(), rows)
        learn.set_syntax_backend(self.db, 'off')
        self.assertEqual(self.rank(partial)['grammar_score'], 0)
        self.choose([partial], round_id='off')
        self.assertEqual(self.rows(), rows)

    def test_invalid_canonical_certainty_and_link_flag_fail_before_database(self):
        item = self.bind()
        for value in (1, None, 'true'):
            changed = deepcopy(item)
            changed['grammar']['frames'][0]['slots_complete'] = value
            self.file.write_text(json.dumps({'chosen': 'one', 'candidates': [changed]}))
            with self.subTest(value=value), self.assertRaises(ValueError):
                learn.choice_file(self.db, 'methods', 'detail', self.file)
            self.assertFalse(Path(self.db).exists())
        changed = deepcopy(item)
        frame = changed['grammar']['frames'][0]
        frame.pop('form'); frame.pop('voice')
        frame.update(kind='link', relation='unknown', slots=[])
        with self.assertRaises(ValueError):
            learn._validated_candidates([changed])


if __name__ == '__main__':
    unittest.main()
