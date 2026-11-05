"""Grounded comparison modifiers learn placement without lexical cue rules."""
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
spec = importlib.util.spec_from_file_location('comparative', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
POSITION = learn.GRAMMAR_PREFIX + 'adjunct_position:comparison:'


def draft(position, *, dependent='与基线相比', subject='改进方案的误差', event='降低', amount='8%', version=None):
    predicate = event + '了 ' + amount
    matrix = subject + predicate
    text = {'before': dependent + '，' + matrix + '。',
            'embedded': subject + dependent + predicate + '。',
            'after': matrix + '，' + dependent + '。'}[position]
    grammar = {'frames': [
        {'kind': 'expression', 'text': text if position == 'embedded' else matrix,
         'form': 'verbal', 'voice': 'nonagentive', 'slots': [
             {'role': 'patient', 'text': subject}, {'role': 'event', 'text': event},
             {'role': 'complement', 'text': amount}]},
        {'kind': 'link', 'text': text, 'relation': 'comparison', 'slots': [
             {'role': 'head', 'text': predicate}, {'role': 'dependent', 'text': dependent}]}]}
    if version is not None:
        grammar['version'] = version
    return {'round_id': 'comparison-' + position,
            'learning_context': {'section': 'results', 'role': 'support'},
            'candidates': [{'id': position, 'label': 'Neutral', 'text': text,
                            'analysis': {'spans': []}, 'grammar': grammar}]}


class ComparativeFrameTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='sonder-comparison-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = str(self.root / 'preference.db')
        self.file = self.root / 'decision.json'
        clock = patch.object(learn, '_now_iso', return_value='2026-10-27T01:00:00+00:00')
        clock.start()
        self.addCleanup(clock.stop)

    def bind(self, position, **kwargs):
        try:
            return learn.bind_candidate_data(draft(position, **kwargs))['candidates'][0]
        except ValueError as error:
            self.fail('comparative modifier must bind: ' + str(error))

    def choose(self, pool, chosen, event):
        self.file.write_text(json.dumps({'candidates': pool, 'chosen': chosen,
            'round_id': event, 'author_owned': True,
            'learning_context': {'section': 'results', 'role': 'support'}}, ensure_ascii=False))
        return learn.choice_file(self.db, 'results', 'support', self.file)

    def positions(self):
        with closing(sqlite3.connect(self.db)) as c:
            return c.execute('SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature', (POSITION + '%',)).fetchall()

    def test_comparison_requires_v4_and_is_selected_only_for_new_relation(self):
        candidate = self.bind('before')
        self.assertEqual(candidate['grammar']['version'], 4)
        self.assertIn(4, learn.engine_info()['input_versions']['grammar'])
        for version in (1, 2, 3):
            old = draft('before', version=version)
            if version == 1:
                old['candidates'][0]['grammar']['frames'][0]['slots'].pop()
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'relation'):
                learn.bind_candidate_data(old)
        self.assertFalse(Path(self.db).exists())

    def test_historical_omitted_versions_retry_with_identical_bytes(self):
        for relation, version in [('condition', 2), ('temporal', 3)]:
            old = draft('before')
            old['candidates'][0]['grammar']['frames'][1]['relation'] = relation
            explicit = deepcopy(old)
            explicit['candidates'][0]['grammar']['version'] = version
            expected = learn.bind_candidate_data(explicit)
            source = self.root / (relation + '-draft.json')
            target = self.root / (relation + '-bound.json')
            source.write_text(json.dumps(old, ensure_ascii=False))
            target.write_text(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
            before = target.read_bytes(), target.stat().st_mtime_ns
            self.assertTrue(learn.bind_file(source, target)['reused'])
            self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
        self.assertFalse(Path(self.db).exists())

    def test_positions_come_from_matrix_slots_not_dependent_cue_words(self):
        for dependent in ('与基线相比', '相较于初始设置'):
            for position in ('before', 'embedded', 'after'):
                with self.subTest(dependent=dependent, position=position):
                    candidate = self.bind(position, dependent=dependent)
                    features = learn._grammar_features(candidate['grammar'])
                    self.assertEqual({f for f in features if f.startswith(POSITION)}, {POSITION + position})
                    self.assertIn(learn.GRAMMAR_PREFIX + 'link:comparison', features)

    def test_author_feedback_transfers_with_only_position_memory_and_reverses(self):
        pool = [self.bind(p) for p in ('before', 'embedded')]
        held = [self.bind(p, dependent='相较于常规流程', subject='重建过程的耗时', event='缩短', amount='15%')
                for p in ('embedded', 'before')]
        for i in range(3):
            self.choose(pool, 'before', 'front-' + str(i))
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('DELETE FROM prefs')
            c.execute('DELETE FROM phrase_prefs')
            c.execute('DELETE FROM syntax_prefs WHERE feature NOT LIKE ?', (POSITION + '%',))
        rows = learn.rank_candidates(self.db, 'results', 'support', held)
        self.assertEqual(rows[0]['id'], 'before')
        self.assertGreater(rows[0]['score'], rows[1]['score'])
        self.assertTrue(all(r['phrase_score'] == r['label_weight'] == 0 for r in rows))
        self.assertTrue(all(r['score'] == 0 for r in learn.rank_candidates(self.db, 'methods', 'support', held)))
        for i in range(5):
            self.choose(held, 'embedded', 'embedded-' + str(i))
        corrected = learn.rank_candidates(self.db, 'results', 'support', held[::-1])
        self.assertEqual(corrected[0]['id'], 'embedded')
        self.assertGreater(corrected[0]['grammar_scores_by_family']['adjunct_position'], corrected[1]['grammar_scores_by_family']['adjunct_position'])

    def test_missing_matrix_evidence_cannot_reject_or_guess_a_position(self):
        pool = [self.bind(p) for p in ('before', 'embedded')]
        self.choose(pool, 'before', 'initial')
        before = self.positions()
        changed = deepcopy(pool)
        matrix = next(f for f in changed[1]['grammar']['frames'] if f['kind'] == 'expression')
        matrix['slots'] = [s for s in matrix['slots'] if s['role'] != 'patient']
        self.assertEqual(learn._grammar_adjunct_positions(changed[1]['grammar']), [])
        receipt = self.choose(changed, 'embedded', 'missing-patient')
        self.assertFalse(receipt['adjunct_position_negative_feedback'])
        self.assertEqual(self.positions(), before)
        unknown = deepcopy(pool)
        next(f for f in unknown[1]['grammar']['frames'] if f['kind'] == 'link')['relation'] = 'unknown'
        receipt = self.choose(unknown, 'embedded', 'unknown-relation')
        self.assertFalse(receipt['grammar_negative_feedback'])
        self.assertEqual(self.positions(), before)

    def test_other_relations_and_quoted_words_do_not_gain_comparison_keys(self):
        candidate = self.bind('before')
        self.choose([candidate], 'before', 'learn-comparison')
        for relation in ('condition', 'cause', 'temporal', 'concession'):
            modified = deepcopy(candidate)
            next(f for f in modified['grammar']['frames'] if f['kind'] == 'link')['relation'] = relation
            # Interpretation-only isolation probe, not a new semantic reading.
            row = learn.rank_candidates(self.db, 'results', 'support', [modified])[0]
            self.assertEqual(row['grammar_scores_by_family']['adjunct_position'], 0)
        no_link = deepcopy(candidate)
        no_link['grammar']['frames'] = []
        self.assertFalse(any(f.startswith(POSITION) for f in learn._grammar_features(no_link['grammar'])))
        # No word-level auto-detection: literal cue text alone is not a grammar key.
        literal = {'id': 'quoted', 'label': 'Neutral', 'text': '图中“相比”是标签名。'}
        self.assertEqual(learn.rank_candidates(self.db, 'results', 'support', [literal])[0]['grammar_score'], 0)


if __name__ == '__main__':
    unittest.main()
