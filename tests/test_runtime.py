"""The installed Skill must reject an engine that silently lacks its protocol."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'scripts/check_runtime.py'
ENGINE = ROOT / 'library/learn.py'


class RuntimeTests(unittest.TestCase):
    def call(self, path, *args, cwd=None):
        return subprocess.run([sys.executable, '-B', '-I', str(path), *map(str, args)],
                              cwd=cwd, capture_output=True, text=True, timeout=20)

    def info(self):
        result = self.call(ENGINE, 'engine-info')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['engine'], 'sonder')
        self.assertEqual(data['info_version'], 1)
        return data

    def fixture(self, root, code):
        checker = root / 'scripts/check_runtime.py'
        checker.parent.mkdir(parents=True, exist_ok=True)
        # An assertion, not a setup/import error, records the old missing guard.
        self.assertTrue(CHECKER.is_file(), 'Skill runtime checker is missing')
        shutil.copy2(CHECKER, checker)
        engine = root / 'library/learn.py'
        engine.parent.mkdir(exist_ok=True)
        engine.write_text(code)
        return checker

    def test_engine_info_is_no_database_command_and_rejects_extra_arguments(self):
        with tempfile.TemporaryDirectory(prefix='sonder-runtime-') as td:
            folder = Path(td)
            result = self.call(ENGINE, 'engine-info', cwd=folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            info = json.loads(result.stdout)
            self.assertIn(2, info['input_versions']['grammar'])
            self.assertIn(3, info['input_versions']['grammar'])
            self.assertIn(4, info['input_versions']['grammar'])
            self.assertIn(1, info['input_versions']['analysis'])
            self.assertIn(2, info['input_versions']['analysis'])
            self.assertEqual(info['feature_versions']['analysis_topology'], 'v1')
            self.assertEqual(info['database_schema'], 9)
            self.assertEqual(info['context_key_version'], 2)
            self.assertIn('injective_context_keys', info['capabilities'])
            self.assertEqual(info['input_versions']['relation_coverage'], [1])
            self.assertEqual(info['feature_versions']['relations'], 'v1')
            self.assertIn('relation_coverage', info['capabilities'])
            self.assertIn('checked_candidate_preparation', info['capabilities'])
            self.assertIn('coextensive_grammar_frames', info['capabilities'])
            self.assertIn(7, info['input_versions']['grammar'])
            self.assertIn('relative_preference_learning', info['capabilities'])
            self.assertEqual(info.get('exemplar_index_version'), 1)
            self.assertIn('choice_receipts', info['capabilities'])
            self.assertIn('choice_receipt_export', info['capabilities'])
            self.assertIn('unanchored_grammar_guard', info['capabilities'])
            self.assertIn('independent_adjunct_feedback', info['capabilities'])
            self.assertEqual(info.get('receipt_export_version'), 1)
            self.assertIs(type(info.get('receipt_export_version')), int)
            self.assertIn('compact_ranking', info['capabilities'])
            self.assertIn('rank_summary_input_guard', info['capabilities'])
            self.assertIn('feedback_commit_time', info['capabilities'])
            self.assertIn('complete_phrase_features', info['capabilities'])
            self.assertIn('consistent_rank_snapshot', info['capabilities'])
            self.assertIn('formatting_text_continuity', info['capabilities'])
            self.assertIn('markdown_text_format', info['capabilities'])
            self.assertIn('exemplar_format', info['capabilities'])
            self.assertIn('syntax_fallback_diagnostics', info['capabilities'])
            self.assertIn('syntax_model_identity', info['capabilities'])
            self.assertIn('choice_id_guard', info['capabilities'])
            self.assertIn('grounded_grammar_precedence', info['capabilities'])
            self.assertIn('text_first_ranking', info['capabilities'])
            self.assertIn('choice_learning_control', info['capabilities'])
            self.assertIn('choice_context_guard', info['capabilities'])
            self.assertIn('analysis_uncertainty_guard', info['capabilities'])
            self.assertIn('phrase_literal_guard', info['capabilities'])
            self.assertIn('choice_edit_scope_guard', info['capabilities'])
            self.assertIn('independent_analysis_scoring', info['capabilities'])
            self.assertIn('grammar_link_order', info['capabilities'])
            self.assertIn('grammar_link_identity', info['capabilities'])
            self.assertIn('grammar_observation_identity', info['capabilities'])
            self.assertIn('grammar_binding_capacity', info['capabilities'])
            self.assertEqual(info['annotation_limits'], {
                'grammar_frames': 64, 'analysis_parts': 16, 'grammar_expression_slots': 5})
            self.assertEqual(info['scoring_version'], 2)
            self.assertEqual(info['supported_scoring_versions'], [2, 3, 4, 5])
            self.assertEqual(info['learning_policies'], ['legacy', 'adaptive-v1', 'adaptive-v2', 'adaptive-v3'])
            self.assertIn('adaptive_preference_learning', info['capabilities'])
            self.assertEqual(list(folder.iterdir()), [])
            sentinel = folder / 'never-open-this.db'
            sentinel.write_bytes(b'not a SQLite store\x00\xff')
            bad = self.call(ENGINE, 'engine-info', sentinel, cwd=folder)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('no arguments', bad.stderr)
            self.assertEqual(sentinel.read_bytes(), b'not a SQLite store\x00\xff')
            self.assertEqual(list(folder.iterdir()), [sentinel])

    def test_current_runtime_reports_own_absolute_engine_without_creating_a_store(self):
        with tempfile.TemporaryDirectory(prefix='sonder-runtime-') as td:
            folder = Path(td)
            self.assertTrue(CHECKER.is_file(), 'Skill runtime checker is missing')
            result = self.call(CHECKER, cwd=folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['engine_path'], str(ENGINE.resolve()))
            self.assertEqual(report['status'], 'compatible')
            self.assertEqual(report['engine_info'], self.info())
            self.assertEqual(list(folder.iterdir()), [])

    def test_missing_or_legacy_engine_fails_without_database_side_effects(self):
        with tempfile.TemporaryDirectory(prefix='sonder-runtime-') as td:
            root = Path(td) / 'mixed skill with spaces'
            checker = self.fixture(root, "import sys\nprint('unknown command', file=sys.stderr)\nsys.exit(1)\n")
            engine = root / 'library/learn.py'
            for present in (True, False):
                if not present:
                    engine.unlink()
                with self.subTest(present=present):
                    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                    result = self.call(checker, cwd=Path(td))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Sonder runtime mismatch', result.stderr)
                    self.assertEqual(result.stdout, '')
                    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                    self.assertEqual(after, before)

    def test_capability_check_does_not_enable_an_off_workspace(self):
        with tempfile.TemporaryDirectory(prefix='sonder-runtime-off-') as td:
            root = Path(td)
            db = root / 'preference.db'
            setting = self.call(ENGINE, 'syntax-backend', db, 'off')
            self.assertEqual(setting.returncode, 0, setting.stderr)
            before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
            result = self.call(CHECKER, cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}, before)
            file = root / 'candidate.json'
            text = '信号稳定。'
            file.write_text(json.dumps({'candidates': [{'id': 'a', 'label': 'Keep original', 'text': text,
                'grammar': {'version': 2, 'source': 'writing-model', 'frames': [],
                            'text_sha256': hashlib.sha256(text.encode()).hexdigest()}}]}, ensure_ascii=False))
            ranked = self.call(ENGINE, 'rank-file', db, 'results', 'support', file)
            self.assertEqual(ranked.returncode, 0, ranked.stderr)
            row = json.loads(ranked.stdout)[0]
            self.assertEqual(row['syntax_backend'], 'off')
            self.assertIsNone(row['grammar_feature_version'])
            self.assertEqual(row['grammar_score'], 0)

    def test_success_exit_with_missing_or_invalid_capabilities_is_rejected(self):
        info = self.info()
        bad_infos = [None, [], {}, {'engine': 'sonder'}]
        previous = deepcopy(info)
        previous['capabilities'].remove('injective_context_keys')
        bad_infos.append(previous)
        previous = deepcopy(info)
        del previous['context_key_version']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'].remove('coextensive_grammar_frames')
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['input_versions']['grammar'].remove(7)
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'].remove('checked_candidate_preparation')
        bad_infos.append(previous)
        for key, values in [('supported_scoring_versions', [None, [2], [3], [2, 3], [True, 3], '2,3']),
                            ('learning_policies', [None, ['legacy'], ['adaptive-v1'], ['legacy', 'adaptive-v1'], [True], 'adaptive-v1'])]:
            for value in values:
                invalid = deepcopy(info)
                invalid[key] = value
                bad_infos.append(invalid)
        invalid = deepcopy(info)
        invalid['capabilities'] = [name for name in invalid['capabilities'] if name != 'adaptive_preference_learning']
        bad_infos.append(invalid)
        invalid = deepcopy(info)
        invalid['capabilities'] = [name for name in invalid['capabilities'] if name != 'local_opposition_learning']
        bad_infos.append(invalid)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'unanchored_grammar_guard']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'independent_adjunct_feedback']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'].remove('compact_ranking')
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'].remove('rank_summary_input_guard')
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'].remove('feedback_commit_time')
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'].remove('complete_phrase_features')
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'consistent_rank_snapshot']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'formatting_text_continuity']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'markdown_text_format']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'exemplar_format']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'choice_receipt_export']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'grounded_grammar_precedence']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'text_first_ranking']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'choice_learning_control']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'choice_context_guard']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'analysis_uncertainty_guard']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'phrase_literal_guard']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'choice_edit_scope_guard']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'independent_analysis_scoring']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'grammar_link_order']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'grammar_link_identity']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'grammar_observation_identity']
        bad_infos.append(previous)
        previous = deepcopy(info)
        previous['capabilities'] = [name for name in previous['capabilities'] if name != 'grammar_binding_capacity']
        bad_infos.append(previous)
        for limits in (None, {}, {'grammar_frames': 16, 'analysis_parts': 16, 'grammar_expression_slots': 5},
                       {'grammar_frames': 64, 'analysis_parts': 16, 'grammar_expression_slots': 4},
                       {'grammar_frames': 64, 'analysis_parts': True, 'grammar_expression_slots': 5}):
            previous = deepcopy(info)
            previous['annotation_limits'] = limits
            bad_infos.append(previous)
        for key, value in (('engine', 'different-engine'), ('info_version', True),
                           ('context_key_version', None), ('context_key_version', True),
                           ('context_key_version', 1), ('context_key_version', '2'),
                           ('database_schema', 8),
                           ('receipt_export_version', None), ('receipt_export_version', True),
                           ('receipt_export_version', 2),
                           ('info_version', 2), ('database_schema', 3), ('database_schema', 4), ('database_schema', 5),
                           ('exemplar_index_version', None), ('exemplar_index_version', True), ('exemplar_index_version', 2),
                           ('scoring_version', None), ('scoring_version', 1), ('scoring_version', True),
                           ('capabilities', ['phrase_memory', 'author_exemplars']),
                           ('capabilities', ['literal_text_files', 'phrase_memory', 'author_exemplars', 'choice_receipts']),
                           ('capabilities', ['literal_text_files', 'phrase_memory', 'author_exemplars', 'choice_receipts', 'adjunct_position']),
                           ('capabilities', ['literal_text_files', 'phrase_memory', 'author_exemplars', 'choice_receipts', 'adjunct_position', 'annotation_binding']),
                           ('capabilities', ['literal_text_files', 'phrase_memory', 'author_exemplars', 'choice_receipts', 'adjunct_position', 'annotation_binding', 'syntax_fallback_diagnostics']),
                           ('capabilities', ['literal_text_files', 'phrase_memory', 'author_exemplars', 'choice_receipts', 'adjunct_position', 'annotation_binding', 'syntax_fallback_diagnostics', 'syntax_model_identity']),
                           ('input_versions', {'analysis': [1], 'grammar': [1]}),
                           ('input_versions', {'analysis': [1], 'grammar': [1, 2]}),
                           ('input_versions', {'analysis': [1], 'grammar': [1, 2, 3]}),
                           ('input_versions', {'analysis': [1], 'grammar': [1, 2, 3, 4]}),
                           ('input_versions', {'analysis': [1, 2], 'grammar': [1, 2, 3, 4]}),
                           ('input_versions', {'analysis': [1, 2], 'grammar': [1, 2, 3, 4, 5]}),
                           ('input_versions', {'analysis': [True], 'grammar': [1, 2]}),
                           ('input_versions', {'analysis': [1], 'grammar': '12'}),
                           ('feature_versions', {'syntax': 'v3', 'analysis': 'v1', 'grammar': 'v1'})):
            changed = deepcopy(info)
            changed[key] = value
            bad_infos.append(changed)
        with tempfile.TemporaryDirectory(prefix='sonder-runtime-') as td:
            root = Path(td) / 'mixed skill'
            for bad in bad_infos:
                with self.subTest(info=bad):
                    checker = self.fixture(root, f'print({json.dumps(bad)!r})\n')
                    result = self.call(checker, cwd=Path(td))
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('Sonder runtime mismatch', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)

    def test_malformed_output_and_timeout_report_actionable_mismatch(self):
        with tempfile.TemporaryDirectory(prefix='sonder-runtime-') as td:
            checker = self.fixture(Path(td), "print('this old engine returned success without JSON')\n")
            result = self.call(checker)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Sonder runtime mismatch', result.stderr)
        self.assertTrue(CHECKER.is_file())
        spec = importlib.util.spec_from_file_location('sonder_runtime', CHECKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(module.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['engine-info'], 10)):
            with self.assertRaisesRegex(ValueError, 'timed out'):
                module.check_runtime()


if __name__ == '__main__':
    unittest.main()
