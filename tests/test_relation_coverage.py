"""Canonical relation inventories bind evidence and participate in choice identity."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('relation_protocol_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec); spec.loader.exec_module(learn)
FIXTURE = json.loads((ROOT / 'tests/fixtures/relation-learning.json').read_text())


def covered(sid='T001'):
    item = FIXTURE['pools'][sid]; data = deepcopy(item['data'])
    for candidate in data['candidates']:
        value = item['coverage'].get(candidate['id'])
        if value is not None:
            candidate['relation_coverage'] = deepcopy(value)
    return data


class RelationCoverageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-coverage-')
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.db = str(self.root / 'preference.db')

    def test_head_kind_types_are_input_errors_in_canonical_and_draft_paths(self):
        for value in ([], {}, ['event'], {'kind': 'event'}, None, True, 1):
            for mode in ('canonical', 'draft'):
                with self.subTest(value=value, mode=mode):
                    data = covered(); data['round_id'] = 'bad-head-type'
                    data['candidates'][0]['relation_coverage']['links'][0]['head_kind'] = value
                    if mode == 'draft':
                        for candidate in data['candidates']:
                            candidate['relation_coverage'].pop('text_sha256')
                            candidate['relation_coverage'].pop('grammar_sha256')
                    before = deepcopy(data)
                    with patch.object(learn, '_open', side_effect=AssertionError('no store access')):
                        with self.assertRaisesRegex(ValueError, r'candidates\[0\].relation_coverage.*head_kind'):
                            (learn._validated_candidate_data if mode == 'canonical' else learn.bind_candidate_data)(data)
                    self.assertEqual(data, before)
                    self.assertFalse(Path(self.db).exists())

    def test_cli_invalid_head_kind_has_no_traceback_or_store_and_corrected_retry_succeeds(self):
        data = covered(); data['round_id'] = 'recover-head-type'
        path = self.root / 'candidates.json'
        link = data['candidates'][0]['relation_coverage']['links'][0]
        original = link['head_kind']; link['head_kind'] = ['event']
        path.write_text(json.dumps(data))
        argv = [sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'),
                'rank-file', self.db, 'methods', 'detail', str(path)]
        rejected = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn('Traceback', rejected.stderr)
        self.assertIn('head_kind', rejected.stderr)
        self.assertIn('candidates[0].relation_coverage', rejected.stderr)
        self.assertFalse(Path(self.db).exists())
        link['head_kind'] = original; path.write_text(json.dumps(data))
        accepted = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertTrue(all(row['score'] == 0 for row in json.loads(accepted.stdout)))

    def test_canonical_field_survives_shared_validation(self):
        data = covered()
        actual = learn._validated_candidates(data['candidates'])
        self.assertEqual(actual, data['candidates'])

    def test_draft_frame_indices_remap_after_sorting(self):
        canonical = covered(); draft = deepcopy(canonical)
        for candidate in draft['candidates']:
            frames = candidate['grammar']['frames']; count = len(frames)
            candidate['grammar']['frames'] = list(reversed(frames))
            coverage = candidate['relation_coverage']
            coverage.pop('text_sha256'); coverage.pop('grammar_sha256')
            for entry in coverage['expressions']:
                entry['frame'] = count - 1 - entry['frame']
                entry['incoming_links'] = [count - 1 - index for index in entry['incoming_links']]
            for entry in coverage['links']:
                entry['frame'] = count - 1 - entry['frame']
                if 'head_expression' in entry:
                    entry['head_expression'] = count - 1 - entry['head_expression']
        self.assertEqual(learn.bind_candidate_data(draft), canonical)

    def test_bad_coverage_fingerprint_fails_before_store(self):
        data = covered(); data['candidates'][0]['relation_coverage']['text_sha256'] = '0' * 64
        with patch.object(learn, '_open', side_effect=AssertionError('no database access')):
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                learn.rank_candidates(self.db, 'methods', 'detail', data['candidates'])
        self.assertFalse(Path(self.db).exists())

    def test_same_text_conflicting_coverage_rejected(self):
        data = covered(); duplicate = deepcopy(data['candidates'][0]); duplicate['id'] = 'copy'
        duplicate['relation_coverage']['expressions'][0]['incoming_complete'] = False
        with self.assertRaisesRegex(ValueError, 'identical candidate text'):
            learn._validated_candidates([data['candidates'][0], duplicate])

    def test_coverage_changes_conflict_with_saved_receipt(self):
        data = covered(); data.update(round_id='coverage-decision', chosen='a', author_owned=False)
        path = self.root / 'choice.json'; path.write_text(json.dumps(data))
        receipt = learn.choice_file(self.db, 'methods', 'detail', str(path))
        self.assertFalse(receipt['replayed'])
        self.assertTrue(learn.choice_file(self.db, 'methods', 'detail', str(path))['replayed'])
        data['candidates'][0]['relation_coverage']['expressions'][0]['incoming_complete'] = False
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'different decision'):
            learn.choice_file(self.db, 'methods', 'detail', str(path))

    def test_absent_field_keeps_original_hash_and_shape(self):
        candidates = learn._validated_candidates(deepcopy(FIXTURE['pools']['T001']['data']['candidates']))
        expected = {'version': 1, 'section': 'methods', 'role': 'detail', 'candidates': sorted(candidates, key=lambda row: row['id']), 'chosen': 'a', 'author_owned': False}
        digest = hashlib.sha256(json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(learn._choice_payload_hash('methods', 'detail', candidates, 'a', False), digest)
        self.assertTrue(all('relation_coverage' not in row for row in candidates))

    def test_incomplete_inventory_and_missing_declaration_are_not_none(self):
        data = covered(); candidate = data['candidates'][0]
        candidate['relation_coverage']['expressions'] = []
        validated = learn._validated_candidates([candidate])[0]
        vector, info = learn._relations.project(learn._grammar_observations, validated, scale=learn.SYNTAX_SCALE)
        self.assertEqual(vector, {}); self.assertFalse(info['fully_known'])
        candidate.pop('relation_coverage')
        vector, info = learn._relations.project(learn._grammar_observations, candidate, scale=learn.SYNTAX_SCALE)
        self.assertEqual(vector, {}); self.assertFalse(info['fully_known'])

    def test_cross_target_and_duplicate_indices_are_rejected(self):
        for mutation in ['wrong_target', 'duplicate']:
            data = covered(); declaration = data['candidates'][0]['relation_coverage']
            if mutation == 'wrong_target':
                declaration['links'][0]['head_expression'] = 0
            else:
                declaration['expressions'].append(deepcopy(declaration['expressions'][0]))
            with self.assertRaises(ValueError):
                learn._validated_candidates(data['candidates'])


if __name__ == '__main__':
    unittest.main()
