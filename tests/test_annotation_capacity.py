"""Draft binding and canonical validation share bounded grammar capacities."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('capacity_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learn)


def five_roles(version=6):
    text = 'The team presented the sample to the panel as intact.'
    frame = {'kind': 'expression', 'text': text, 'form': 'verbal', 'voice': 'active',
             'slots': [{'role': role, 'text': word} for role, word in [
                 ('agent', 'The team'), ('event', 'presented'), ('patient', 'the sample'),
                 ('recipient', 'the panel'), ('complement', 'as intact')]]}
    if version >= 6:
        frame['slots_complete'] = True
    return {'round_id': 'capacity-five-roles', 'candidates': [{
        'id': 'own', 'text': text, 'analysis': {'units': []},
        'grammar': {'version': version, 'frames': [frame]}}]}


def canonical(data):
    """Independent exact fixture; do not use the binder being tested."""
    result = deepcopy(data)
    for candidate in result['candidates']:
        text = candidate['text']
        for kind in ['analysis', 'grammar']:
            value = candidate[kind]
            value.setdefault('version', 2)
            value.update(source='writing-model', text_sha256=hashlib.sha256(text.encode()).hexdigest())
            for frame in value.get('frames', []):
                frame['start'] = text.index(frame['text'])
                frame['end'] = frame['start'] + len(frame['text'])
                for slot in frame['slots']:
                    slot['start'] = text.index(slot['text'], frame['start'], frame['end'])
                    slot['end'] = slot['start'] + len(slot['text'])
    return result


def many_frames(count):
    clauses = [f'样本{i}稳定' for i in range(count)]
    text = '；'.join(clauses) + '。'
    return {'round_id': 'capacity-many-frames', 'candidates': [{
        'id': 'keep', 'text': text, 'analysis': {'units': []},
        'grammar': {'version': 6, 'frames': [
            {'kind': 'expression', 'text': clause, 'form': 'adjectival',
             'voice': 'nonagentive', 'slots_complete': True,
             'slots': [{'role': 'patient', 'text': f'样本{i}'},
                       {'role': 'event', 'text': '稳定'}]}
            for i, clause in enumerate(clauses)]}}]}


class AnnotationCapacityTests(unittest.TestCase):
    def bind(self, data):
        try:
            return learn.bind_candidate_data(data)
        except ValueError as error:
            self.fail(f'A valid supported grammar must bind: {error}')

    def test_all_five_roles_roundtrip_through_both_entry_points(self):
        for version in [5, 6]:
            with self.subTest(version=version):
                draft = five_roles(version)
                direct, candidates = learn._validated_candidate_data(canonical(draft))
                direct = {**direct, 'candidates': candidates}
                bound = self.bind(draft)
                self.assertEqual(bound, direct)
                self.assertEqual(self.bind(bound), bound)
                frame = bound['candidates'][0]['grammar']['frames'][0]
                self.assertEqual(len(frame['slots']), 5)
                self.assertIn(learn.GRAMMAR_PREFIX + 'expression_slots:verbal:agent>event>patient>recipient>complement',
                              learn._grammar_features(bound['candidates'][0]['grammar']))

    def test_role_expansion_keeps_version_kind_and_duplicate_guards(self):
        for version in [1, 2, 3, 4]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                learn.bind_candidate_data(five_roles(version))
        for invalid in ['duplicate', 'link', 'version']:
            data = five_roles()
            frame = data['candidates'][0]['grammar']['frames'][0]
            if invalid == 'duplicate':
                frame['slots'][4]['role'] = 'patient'
            elif invalid == 'link':
                frame.clear()
                frame.update(kind='link', text=data['candidates'][0]['text'], relation='complement',
                             slots=[{'role': 'head', 'text': 'presented'}, {'role': 'dependent', 'text': 'as intact'},
                                    {'role': 'recipient', 'text': 'the panel'}])
            else:
                data['candidates'][0]['grammar']['version'] = '6'
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                learn.bind_candidate_data(data)

    def test_sixty_four_frames_bind_without_truncation_and_sixty_five_fail(self):
        for count in [17, 26, 64]:
            data = many_frames(count)
            bound = self.bind(data)
            direct, candidates = learn._validated_candidate_data(canonical(data))
            direct = {**direct, 'candidates': candidates}
            self.assertEqual(bound, direct)
            self.assertEqual(len(bound['candidates'][0]['grammar']['frames']), count)
        for data in [many_frames(65), canonical(many_frames(65))]:
            with self.assertRaisesRegex(ValueError, 'at most 64'):
                learn.bind_candidate_data(data)
        with self.assertRaisesRegex(ValueError, 'at most 64'):
            learn._validated_candidate_data(canonical(many_frames(65)))

    def test_cli_capacity_error_is_atomic_and_successful_retry_is_identical(self):
        with tempfile.TemporaryDirectory(prefix='sonder-capacity-') as td:
            root = Path(td); source = root/'draft.json'; output = root/'candidate.json'
            command = [sys.executable, '-B', '-I', str(ROOT/'library/learn.py'), 'bind-file', str(source), str(output)]
            source.write_text(json.dumps(many_frames(65), ensure_ascii=False))
            failed = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob('*.db')))
            source.write_text(json.dumps(five_roles()))
            first = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(first.returncode, 0, first.stderr)
            saved = output.read_bytes(); stamp = output.stat().st_mtime_ns
            retry = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertTrue(json.loads(retry.stdout)['reused'])
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(output.stat().st_mtime_ns, stamp)
            self.assertFalse(list(root.glob('*.db')))


if __name__ == '__main__':
    unittest.main()
