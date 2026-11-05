"""Supplied but unresolved predicate evidence must not create confident attachment."""
from copy import deepcopy
from contextlib import closing
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
spec = importlib.util.spec_from_file_location('unanchored_grammar_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/grammar-adjunct-placement.json').read_text())
NOW = '2026-10-28T03:00:00+00:00'
POSITION = learn.GRAMMAR_PREFIX + 'adjunct_position:'


def ambiguous(*, unanchored=False, partial_head=False):
    item = deepcopy(FIXTURE['candidates'][1])
    item['grammar']['version'] = 6
    for frame in item['grammar']['frames']:
        if frame['kind'] == 'expression':
            frame['slots_complete'] = True
    extra = deepcopy(item['grammar']['frames'][0])
    extra.update(start=11, end=15, text='保持稳定')
    extra['slots'] = [s for s in extra['slots'] if s['role'] != 'patient']
    if unanchored:
        extra.update(form='unknown', voice='unknown', slots_complete=False, slots=[])
    if partial_head:
        extra['slots'] = [dict(role='event', start=11, end=15, text='保持稳定')]
        head = next(s for f in item['grammar']['frames'] if f['kind'] == 'link'
                    for s in f['slots'] if s['role'] == 'head')
        head.update(start=11, end=13, text='保持')
    item['grammar']['frames'].append(extra)
    return learn._validated_candidates([item])[0]


def separator_only_overlap(separator):
    text = '温度稳定后，' + separator + '设备启动。'
    dependent = '温度稳定后'
    head = ('，' if not separator else separator) + '设备启动'
    draft = dict(round_id='binding-only', candidates=[dict(id='one', text=text,
        analysis=dict(version=2, units=[]),
        grammar=dict(version=6, frames=[
            dict(kind='expression', text='温度稳定后，' + separator,
                 form='unknown', voice='unknown', slots_complete=False, slots=[]),
            dict(kind='expression', text='设备启动。', form='verbal', voice='nonagentive', slots_complete=True,
                 slots=[dict(role='patient', text='设备'), dict(role='event', text='启动')]),
            dict(kind='link', text=text, relation='temporal',
                 slots=[dict(role='dependent', text=dependent), dict(role='head', text=head)]),
        ]))])
    return learn.bind_candidate_data(draft)['candidates'][0]


class UnanchoredGrammarTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-unanchored-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def choose(self, db, candidates, chosen, identity):
        path = self.root / (identity + '.json')
        path.write_text(json.dumps(dict(round_id=identity, candidates=candidates, chosen=chosen,
            author_owned=True, learning_context=dict(section='methods', role='detail')), ensure_ascii=False))
        return learn.choice_file(str(db), 'methods', 'detail', path), path

    def rank(self, db, candidate):
        return learn.rank_candidates(str(db), 'methods', 'detail', [candidate])[0]

    def rows(self, db, query, params=()):
        with closing(sqlite3.connect(db)) as conn:
            return conn.execute(query, params).fetchall()

    def dump(self, db):
        with closing(sqlite3.connect(db)) as conn:
            return list(conn.iterdump())

    def test_clearing_uncertain_predicate_anchor_cannot_resolve_scope_conflict(self):
        before, masked = ambiguous(), ambiguous(unanchored=True)
        self.assertEqual(learn._grammar_adjunct_positions(before['grammar']), [])
        self.assertEqual(learn._grammar_adjunct_positions(masked['grammar']), [])
        self.assertIsNone(learn._grammar_adjunct_positions(masked['grammar'], require_complete=True))

    def test_supported_legacy_versions_keep_unanchored_unknown_evidence(self):
        for version in [1, 2, 3, 4, 5, 6]:
            item = ambiguous(unanchored=True)
            item['grammar']['version'] = version
            for frame in item['grammar']['frames']:
                if version < 6:
                    frame.pop('slots_complete', None)
                if version == 1:
                    frame['slots'] = [s for s in frame['slots'] if s['role'] != 'complement']
            item = learn._validated_candidates([item])[0]
            with self.subTest(version=version):
                self.assertEqual(learn._grammar_adjunct_positions(item['grammar']), [])

    def test_less_known_evidence_cannot_raise_actual_learned_scores(self):
        pool = deepcopy(FIXTURE['candidates'][:2])
        for policy in ['legacy', 'adaptive-v1']:
            db = self.root / (policy + '.db')
            learn.learning_policy(str(db), policy)
            for i in range(3):
                self.choose(db, pool, pool[1]['id'], policy + str(i))
            before, masked = self.rank(db, ambiguous()), self.rank(db, ambiguous(unanchored=True))
            with self.subTest(policy=policy):
                self.assertLessEqual(masked['score'], before['score'])
                self.assertEqual(masked['grammar_scores_by_family']['adjunct_position'], 0)
                self.assertFalse(any(x['feature'].startswith(POSITION) for x in masked['matched_grammar_features']))

    def test_unknown_head_overlap_is_not_learned_as_positive_position(self):
        for policy in ['legacy', 'adaptive-v1']:
            db = self.root / (policy + '-uncertain.db')
            learn.learning_policy(str(db), policy)
            candidate = ambiguous(unanchored=True)
            self.choose(db, [candidate], candidate['id'], policy + '-uncertain')
            self.assertEqual(self.rows(db, 'SELECT feature FROM syntax_prefs WHERE feature LIKE ?', (POSITION + '%',)), [])
            self.assertEqual(self.rows(db, 'SELECT feature FROM adaptive_weights WHERE feature LIKE ?', ('syntax|' + POSITION + '%',)), [])

    def test_unknown_dependent_or_other_anchored_predicate_does_not_block_known_head(self):
        original = deepcopy(FIXTURE['candidates'][1])
        variants = []
        for retain_anchor in [False, True]:
            item = deepcopy(original)
            dependent = next(f for f in item['grammar']['frames'] if f['kind']=='expression' and f['start']==4)
            dependent.update(form='unknown', voice='unknown')
            if not retain_anchor:
                dependent['slots'] = []
            variants.append(item)
        for item in variants:
            item = learn._validated_candidates([item])[0]
            self.assertEqual(learn._grammar_adjunct_positions(item['grammar']), ['adjunct_position:condition:embedded'])

    def test_touching_separator_or_whitespace_does_not_create_uncertainty(self):
        for separator in ['', ' ', '\t']:
            with self.subTest(separator=separator):
                item = separator_only_overlap(separator)
                self.assertEqual(learn._grammar_adjunct_positions(item['grammar']), ['adjunct_position:temporal:before'])

    def test_partial_predicate_overlap_abstains_instead_of_dropping_a_competitor(self):
        item = ambiguous(partial_head=True)
        self.assertEqual(learn._grammar_status(item), 'complete')
        self.assertEqual(learn._grammar_adjunct_positions(item['grammar']), [])
        self.assertIsNone(learn._grammar_adjunct_positions(item['grammar'], require_complete=True))

    def test_unresolved_head_does_not_reject_position_history(self):
        db = self.root / 'partial-comparison.db'
        learn.learning_policy(str(db), 'adaptive-v1')
        pool = deepcopy(FIXTURE['candidates'][:2])
        self.choose(db, pool, pool[0]['id'], 'first-fronted')
        compat_before = self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature', (POSITION + '%',))
        compare_before = self.rows(db, "SELECT * FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ? ORDER BY feature", ('syntax|' + POSITION + '%',))
        unresolved = ambiguous(partial_head=True)
        result, _ = self.choose(db, [pool[0], unresolved], unresolved['id'], 'uncertain-new-choice')
        self.assertFalse(result['adjunct_position_negative_feedback'])
        self.assertEqual(self.rows(db, 'SELECT * FROM syntax_prefs WHERE feature LIKE ? ORDER BY feature', (POSITION + '%',)), compat_before)
        self.assertEqual(self.rows(db, "SELECT * FROM adaptive_weights WHERE channel='comparison' AND feature LIKE ? ORDER BY feature", ('syntax|' + POSITION + '%',)), compare_before)

    def test_historical_receipt_replays_without_reinterpreting_old_false_key(self):
        db = self.root / 'old-event.db'
        learn.learning_policy(str(db), 'adaptive-v1')
        item = ambiguous(unanchored=True)
        with patch.object(learn, '_grammar_adjunct_positions', return_value=['adjunct_position:condition:embedded']):
            first, path = self.choose(db, [item], item['id'], 'historical')
        before = self.dump(db)
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('replay must not reparse')):
            replay = learn.choice_file(str(db), 'methods', 'detail', path)
        self.assertEqual(replay, {**first, 'replayed':True})
        self.assertEqual(self.dump(db), before)


if __name__ == '__main__':
    unittest.main()
