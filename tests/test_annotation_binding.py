"""Literal fragment binding is deterministic, scoped, and never overwrites work."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'library/learn.py'
SPEC = importlib.util.spec_from_file_location('binding_learn', ENGINE)
learn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learn)


def draft():
    text = '信号稳定；信号减弱[1]。'
    return {'round_id': 'fragment-round-1', 'candidates': [{
        'id': 'keep', 'label': 'Keep original', 'text': text,
        'analysis': {'spans': [
            {'text': '信号稳定', 'move': 'result', 'polarity': 'affirmative', 'certainty': 'asserted'},
            {'text': '信号减弱[1]', 'move': 'result', 'polarity': 'affirmative', 'certainty': 'asserted'}]},
        'grammar': {'frames': [
            {'kind': 'expression', 'text': '信号稳定', 'form': 'adjectival', 'voice': 'nonagentive',
             'slots': [{'role': 'patient', 'text': '信号'}, {'role': 'event', 'text': '稳定'}]},
            {'kind': 'expression', 'text': '信号减弱[1]', 'form': 'verbal', 'voice': 'nonagentive',
             'slots': [{'role': 'patient', 'text': '信号'}, {'role': 'event', 'text': '减弱'}]}]},
    }]}


class AnnotationBindingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sonder-binding-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.input = self.root / 'draft.json'
        self.output = self.root / 'bound.json'

    def bind(self, data):
        self.assertTrue(hasattr(learn, 'bind_candidate_data'), 'No shared annotation binder exists')
        return learn.bind_candidate_data(data)

    def write(self, data=None):
        self.input.write_text(json.dumps(data or draft(), ensure_ascii=False))

    def cli(self, input_path=None, output_path=None):
        return subprocess.run([sys.executable, '-B', '-I', str(ENGINE), 'bind-file',
                               str(input_path or self.input), str(output_path or self.output)],
                              cwd=self.root, capture_output=True, text=True, timeout=20)

    def test_slots_bind_inside_their_frame_and_output_matches_existing_protocol(self):
        data = draft(); before = deepcopy(data)
        result = self.bind(data)
        self.assertEqual(data, before)
        candidate = result['candidates'][0]
        self.assertIn('text_sha256', candidate['analysis'])
        self.assertEqual(candidate['grammar']['version'], 2)
        slots = candidate['grammar']['frames'][1]['slots']
        self.assertEqual(slots[0], {'role': 'patient', 'text': '信号', 'start': 5, 'end': 7})
        self.assertEqual(candidate['analysis']['text_sha256'], hashlib.sha256(candidate['text'].encode()).hexdigest())
        self.assertEqual(learn._validated_candidates([candidate]), [candidate])
        self.assertEqual(self.bind(result), result)

    def test_repeated_and_overlapping_fragments_require_an_explicit_occurrence(self):
        data = draft()
        data['candidates'][0]['analysis']['spans'] = [{'text': '信号', 'move': 'unknown', 'polarity': 'unknown', 'certainty': 'unknown'}]
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.bind(data)
        self.assertTrue(hasattr(learn, '_bind_evidence'))
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            learn._bind_evidence('aaaa', {'text': 'aa'})
        self.assertEqual(learn._bind_evidence('aaaa', {'text': 'aa', 'occurrence': 2}),
                         {'text': 'aa', 'start': 1, 'end': 3})
        repeated = {'round_id': 'repeated', 'candidates': [{'id': 'own', 'text': '信号稳定，信号稳定。',
            'analysis': {'spans': []}, 'grammar': {'frames': [
                {'kind': 'expression', 'text': '信号稳定', 'occurrence': i,
                 'form': 'adjectival', 'voice': 'nonagentive',
                 'slots': [{'role': 'patient', 'text': '信号'}, {'role': 'event', 'text': '稳定'}]}
                for i in (1, 2)]}}]}
        frames = self.bind(repeated)['candidates'][0]['grammar']['frames']
        self.assertEqual([f['start'] for f in frames], [0, 5])
        self.assertEqual(frames[1]['slots'][0]['start'], 5)

    def test_ambiguous_slot_identifies_the_candidate_frame_and_slot(self):
        data = draft()
        text = '操作员设定条件：压力低于设定上限。'
        data['candidates'].append({'id': 'bold', 'text': text,
            'analysis': {'units': [{'move': 'method', 'polarity': 'affirmative',
                'certainty': 'asserted', 'parts': [{'text': text}]}]},
            'grammar': {'frames': [{'kind': 'expression', 'text': text,
                'form': 'verbal', 'voice': 'active', 'slots_complete': False,
                'slots': [{'role': 'event', 'text': '设定'}]}]}})
        before = deepcopy(data)
        with self.assertRaises(ValueError) as caught:
            self.bind(data)
        self.assertIn('candidates[1].grammar.frames[0].slots[0]', str(caught.exception))
        self.assertIn('ambiguous binding fragment', str(caught.exception))
        self.assertEqual(data, before)
        data['candidates'][1]['grammar']['frames'][0]['slots'][0]['occurrence'] = 1
        bound = self.bind(data)
        self.assertEqual(bound['candidates'][1]['grammar']['frames'][0]['slots'][0]['start'], 3)

    def test_ambiguous_analysis_part_identifies_its_input_path(self):
        data = draft()
        text = '信号稳定，信号稳定。'
        data['candidates'].append({'id': 'other', 'text': text,
            'analysis': {'units': [{'move': 'unknown', 'polarity': 'unknown',
                'certainty': 'unknown', 'parts': [
                    {'text': '信号稳定', 'occurrence': 1}, {'text': '信号稳定'}]}]},
            'grammar': {'frames': []}})
        with self.assertRaises(ValueError) as caught:
            self.bind(data)
        self.assertIn('candidates[1].analysis.units[0].parts[1]', str(caught.exception))
        self.assertIn('ambiguous binding fragment', str(caught.exception))
        data['candidates'][1]['analysis']['units'][0]['parts'][1]['occurrence'] = 2
        bound = self.bind(data)
        self.assertEqual([p['start'] for p in bound['candidates'][1]['analysis']['units'][0]['parts']], [0, 5])

    def test_duplicate_frames_identify_both_input_indexes_without_publishing(self):
        data = draft()
        other = deepcopy(data['candidates'][0])
        other['id'] = 'other'
        other['grammar']['frames'].append(deepcopy(other['grammar']['frames'][0]))
        data['candidates'].append(other)
        self.write(data)
        result = self.cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('candidates[1].grammar', result.stderr)
        self.assertIn('duplicate frame kind/range', result.stderr)
        self.assertIn('frames[2]', result.stderr)
        self.assertIn('frames[0]', result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('*.db')))
        self.assertEqual(json.loads(self.input.read_text()), data)

    def test_supplied_coordinates_hashes_and_occurrences_are_not_silently_corrected(self):
        for changes in ({'start': 1}, {'start': 0, 'end': 2}, {'occurrence': True},
                        {'occurrence': 0}, {'occurrence': 3}, {'start': 0, 'occurrence': 1}):
            data = draft()
            data['candidates'][0]['analysis']['spans'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.bind(data)
        data = draft(); data['candidates'][0]['grammar']['text_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'text_sha256'):
            self.bind(data)

    def test_draft_contract_rejects_missing_typo_or_invalid_semantics_before_output(self):
        cases = []
        missing = draft(); del missing['candidates'][0]['grammar']; cases.append(missing)
        typo = draft(); typo['candidates'][0]['grammer'] = typo['candidates'][0].pop('grammar'); cases.append(typo)
        no_id = draft(); del no_id['round_id']; cases.append(no_id)
        wrong = draft(); wrong['candidates'][0]['grammar']['frames'][0]['form'] = 'made-up'; cases.append(wrong)
        for data in cases:
            self.write(data)
            result = self.cli()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.output.exists())
            self.assertFalse(list(self.root.glob('*.db')))
            self.assertFalse(list(self.root.glob('.sonder-bind-*')))
            self.assertEqual(json.loads(self.input.read_text()), data)

    def test_literal_text_abstention_and_actual_rank_choice_replay(self):
        text = '我们测得 $R=\\rho L/A$。\r\n“信号”未变[2]。 `literal` $(literal)\n'
        data = {'round_id': 'literal-binding', 'candidates': [{'id': 'own', 'label': 'Other', 'text': text,
                'analysis': {'spans': []}, 'grammar': {'frames': []}}], 'chosen': 'own', 'author_owned': True}
        self.write(data)
        result = self.cli(); self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(self.root.glob('*.db')))
        bound = json.loads(self.output.read_text())
        self.assertEqual(bound['candidates'][0]['text'], text)
        self.assertEqual(bound['candidates'][0]['grammar']['frames'], [])
        db = str(self.root / 'preferences.db')
        first = learn.choice_file(db, 'results', 'support', self.output)
        self.assertTrue(first['idempotent'])
        replay = learn.choice_file(db, 'results', 'support', self.output)
        self.assertTrue(replay['replayed'])
        self.assertEqual(learn.list_exemplars(db)[0]['text'], text)

    def test_identical_retry_preserves_output_bytes_mtime_and_round_id(self):
        self.write()
        first = self.cli(); self.assertEqual(first.returncode, 0, first.stderr)
        content = self.output.read_bytes()
        os.utime(self.output, (1234, 1234)); stamp = self.output.stat().st_mtime_ns
        retry = self.cli(); self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertFalse(json.loads(first.stdout)['reused'])
        self.assertTrue(json.loads(retry.stdout)['reused'])
        self.assertEqual(self.output.read_bytes(), content)
        self.assertEqual(self.output.stat().st_mtime_ns, stamp)
        self.assertEqual(json.loads(content)['round_id'], 'fragment-round-1')

    def test_different_existing_output_and_in_place_draft_are_never_overwritten(self):
        self.write(); original = self.input.read_bytes()
        self.output.write_bytes(b'Existing manuscript or decision\r\n')
        result = self.cli(); self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b'Existing manuscript or decision\r\n')
        same = self.cli(output_path=self.input); self.assertNotEqual(same.returncode, 0)
        self.assertEqual(self.input.read_bytes(), original)

    def test_concurrent_same_and_conflicting_publications_keep_one_complete_file(self):
        self.write()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.cli(), range(4)))
        self.assertTrue(all(p.returncode == 0 for p in results), [p.stderr for p in results])
        self.assertEqual(sum(not json.loads(p.stdout)['reused'] for p in results), 1)
        self.output.unlink()
        other = self.root / 'other.json'; changed = draft(); changed['round_id'] = 'different-round'
        other.write_text(json.dumps(changed, ensure_ascii=False))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda file: self.cli(input_path=file), [self.input, other]))
        self.assertEqual(sorted(p.returncode == 0 for p in results), [False, True])
        self.assertIn(json.loads(self.output.read_text())['round_id'], ['fragment-round-1', 'different-round'])
        self.assertFalse(list(self.root.glob('.sonder-bind-*')))

    def test_publish_failure_leaves_input_and_existing_work_untouched(self):
        self.assertTrue(hasattr(learn, 'bind_file'))
        self.write(); before = self.input.read_bytes()
        with patch.object(learn.os, 'link', side_effect=OSError('simulated publish failure')):
            with self.assertRaises(OSError):
                learn.bind_file(self.input, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.input.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.sonder-bind-*')))


if __name__ == '__main__':
    unittest.main()
