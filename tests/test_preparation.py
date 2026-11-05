"""Checked draft publication cannot fall through to ranking an older file."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


learn = load('preparation_engine', ROOT / 'library/learn.py')
adapter = load('preparation_adapter', ROOT / 'library/mcp_tools.py')
NOW = '2026-10-29T03:00:00+00:00'


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-preparation-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.inputs = self.root / 'candidate-inputs'
        self.inputs.mkdir()
        self.draft = self.inputs / 'draft.json'
        self.output = self.inputs / 'bound.json'
        self.db = str(self.root / 'preference.db')
        text = '测量结果**不**支持该解释。\r\n公式 $k_B$ 与 \\cite{A}；`literal` $(literal)。\n'
        self.data = {'round_id': 'prepare-round', 'text_format': 'markdown',
                     'learning_context': {'section': 'results', 'role': 'support'},
                     'edit_scope': {'sentence_ids': ['S001'], 'before_text': text, 'keep_original_id': 'keep'},
                     'candidates': [
                         {'id': 'keep', 'label': 'Keep original', 'text': text,
                          'analysis': {'units': []}, 'grammar': {'frames': []}},
                         {'id': 'alt', 'label': 'Light', 'text': '这组测量不足以支持该解释。',
                          'analysis': {'units': []}, 'grammar': {'frames': []}}]}
        self.write()
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)

    def write(self, data=None):
        self.draft.write_text(json.dumps(self.data if data is None else data, ensure_ascii=False))

    def prepare(self, **kwargs):
        return learn.prepare_file(self.db, 'results', 'support', self.draft, self.output, **kwargs)

    def dump(self):
        with sqlite3.connect(self.db) as conn:
            return list(conn.iterdump())

    def cli(self, *suffix):
        return subprocess.run([sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'),
                               'prepare-file', self.db, 'results', 'support', str(self.draft),
                               str(self.output), *suffix], capture_output=True, text=True, timeout=20)

    def test_exact_bind_and_rank_parity_preserves_literal_format_and_context(self):
        bound = learn.bind_candidate_data(self.data)
        other = self.inputs / 'ordinary-bind.json'
        learn.bind_file(self.draft, other)
        result = self.prepare()
        self.assertEqual(result['preparation_version'], 1)
        self.assertEqual(result['binding'], {'output': str(self.output), 'round_id': 'prepare-round',
                                             'candidates': 2, 'reused': False})
        self.assertEqual(self.output.read_bytes(), other.read_bytes())
        self.assertEqual(json.loads(self.output.read_text()), bound)
        self.assertEqual(result['candidates'], learn.rank_candidates(
            self.db, 'results', 'support', bound['candidates'], text_format='markdown'))
        self.assertEqual(self.draft.read_text(), json.dumps(self.data, ensure_ascii=False))

    def test_summary_is_output_only_and_identical_retry_preserves_file(self):
        first = self.prepare()
        before = self.output.read_bytes(), self.dump()
        os.utime(self.output, (1234, 1234))
        stamp = self.output.stat().st_mtime_ns
        retry = self.prepare(summary=True)
        self.assertTrue(retry['binding']['reused'])
        self.assertEqual(retry['candidates'], learn._rank_summary(first['candidates']))
        self.assertEqual((self.output.read_bytes(), self.dump()), before)
        self.assertEqual(self.output.stat().st_mtime_ns, stamp)
        self.assertNotIn('summary_version', json.loads(self.output.read_text())['candidates'][0])

    def test_existing_policy_backend_weights_and_receipts_do_not_change(self):
        for policy in ['legacy', 'adaptive-v1', 'adaptive-v2', 'adaptive-v3']:
            with self.subTest(policy=policy):
                learn.learning_policy(self.db, policy)
                learn.set_syntax_backend(self.db, 'off')
                decision = learn.bind_candidate_data(self.data)
                decision.update(round_id='seed-' + policy, chosen='keep', author_owned=True)
                path = self.inputs / 'seed.json'
                path.write_text(json.dumps(decision, ensure_ascii=False))
                learn.choice_file(self.db, 'results', 'support', path)
                before = self.dump()
                result = self.prepare()
                self.assertEqual(self.dump(), before)
                self.assertEqual(learn.learning_policy(self.db)['policy'], policy)
                self.assertTrue(all(row['syntax_backend'] == 'off' for row in result['candidates']))
                self.assertEqual(result['candidates'], learn.rank_candidates(
                    self.db, 'results', 'support', decision['candidates'], text_format='markdown'))

    def test_invalid_draft_context_and_summary_fail_before_publish_or_store(self):
        invalid = deepcopy(self.data)
        invalid['candidates'][0]['grammar']['frames'] = [{'kind': 'wrong'}]
        mismatch = deepcopy(self.data)
        mismatch['learning_context']['role'] = 'limitation'
        for data in [invalid, mismatch]:
            self.write(data)
            with patch.object(learn, 'rank_candidates') as rank, self.assertRaises(ValueError):
                self.prepare()
            rank.assert_not_called()
            self.assertFalse(self.output.exists())
            self.assertFalse(Path(self.db).exists())
        self.write()
        for section, role, summary in [(' results', 'support', False), ('', 'support', False),
                                       ('results', None, False), ('results', 'support', 'true'),
                                       ('results', 'support', 1), ('results', 'support', None)]:
            with self.subTest(section=section, role=role, summary=summary):
                with self.assertRaises(ValueError):
                    learn.prepare_file(self.db, section, role, self.draft, self.output, summary=summary)
                self.assertFalse(self.output.exists())
                self.assertFalse(Path(self.db).exists())

    def test_stale_output_collision_never_ranks_old_file_or_opens_store(self):
        learn.bind_file(self.draft, self.output)
        before = self.output.read_bytes(), self.output.stat().st_mtime_ns
        self.data['candidates'][1]['text'] = '修正后的候选。'
        self.write()
        with patch.object(learn, 'rank_candidates') as rank, patch.object(learn, '_open') as opened:
            with self.assertRaisesRegex(ValueError, 'different content'):
                self.prepare()
        rank.assert_not_called(); opened.assert_not_called()
        self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), before)
        self.assertFalse(Path(self.db).exists())

    def test_publication_failure_does_not_rank(self):
        with patch.object(learn, '_publish_bound_file', side_effect=OSError('cannot publish')):
            with patch.object(learn, 'rank_candidates') as rank, self.assertRaises(OSError):
                self.prepare()
        rank.assert_not_called()
        self.assertFalse(Path(self.db).exists())
        self.assertFalse(self.output.exists())

    def test_rank_failure_keeps_canonical_for_identical_retry(self):
        with patch.object(learn, 'rank_candidates', side_effect=ValueError('rank failed')):
            with self.assertRaisesRegex(ValueError, 'rank failed'):
                self.prepare()
        before = self.output.read_bytes(), self.output.stat().st_mtime_ns
        self.assertEqual(json.loads(before[0]), learn.bind_candidate_data(self.data))
        self.assertFalse(Path(self.db).exists())
        self.assertTrue(self.prepare()['binding']['reused'])
        self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), before)

    def test_rank_uses_once_bound_memory_not_reopened_file(self):
        with patch.object(learn, 'bind_candidate_data', wraps=learn.bind_candidate_data) as bind:
            with patch.object(learn, '_candidate_data', side_effect=AssertionError('must not reopen')):
                with patch.object(learn, 'rank_candidates', wraps=learn.rank_candidates) as rank:
                    self.prepare()
        bind.assert_called_once()
        self.assertEqual(rank.call_args.args[3], json.loads(self.output.read_text())['candidates'])
        self.assertEqual(rank.call_args.kwargs, {'text_format': 'markdown'})

    def test_ordinary_author_choice_and_replay_after_preparation(self):
        self.prepare(summary=True)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM choice_events').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM exemplars').fetchone()[0], 0)
        data = json.loads(self.output.read_text())
        data.update(chosen='keep', author_owned=True)
        self.output.write_text(json.dumps(data, ensure_ascii=False))
        receipt = learn.choice_file(self.db, 'results', 'support', self.output)
        before = self.dump(), self.output.read_bytes()
        self.assertEqual(learn.choice_file(self.db, 'results', 'support', self.output),
                         {**receipt, 'replayed': True})
        self.assertEqual((self.dump(), self.output.read_bytes()), before)
        self.assertEqual(learn.list_exemplars(self.db)[0]['text'], self.data['candidates'][0]['text'])
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.prepare()
        self.assertEqual((self.dump(), self.output.read_bytes()), before)

    def test_cli_success_summary_retry_and_conflict_failure(self):
        first = self.cli('--summary')
        self.assertEqual(first.returncode, 0, first.stderr)
        result = json.loads(first.stdout)
        self.assertTrue(all(row['summary_version'] == 1 for row in result['candidates']))
        before = self.dump(), self.output.read_bytes()
        retry = self.cli()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertTrue(json.loads(retry.stdout)['binding']['reused'])
        self.data['round_id'] = 'different-round'
        self.write()
        failed = self.cli()
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.stdout, '')
        self.assertIn('different content', failed.stderr)
        self.assertEqual((self.dump(), self.output.read_bytes()), before)

    def test_cli_bad_options_stop_before_publication_and_store(self):
        for suffix in [('--typo',), ('--summary', '--summary')]:
            failed = self.cli(*suffix)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(Path(self.db).exists())
            self.assertFalse(self.output.exists())

    def test_adapter_delegates_shared_core_and_stops_on_conflict(self):
        tools = adapter.WorkspaceTools(self.root)
        with patch.object(tools.engine, 'prepare_file', wraps=tools.engine.prepare_file) as core:
            result = tools.prepare('results', 'support', str(self.draft), str(self.output), summary=True)
        core.assert_called_once_with(self.db, 'results', 'support', str(self.draft), str(self.output), summary=True)
        self.assertEqual(result['binding']['round_id'], self.data['round_id'])
        before = self.dump(), self.output.read_bytes()
        self.data['round_id'] = 'different-round'; self.write()
        with patch.object(tools.engine, 'rank_candidates') as rank, self.assertRaises(ValueError):
            tools.prepare('results', 'support', str(self.draft), str(self.output))
        rank.assert_not_called()
        self.assertEqual((self.dump(), self.output.read_bytes()), before)

    def test_adapter_path_escape_output_restriction_and_invalid_context(self):
        tools = adapter.WorkspaceTools(self.root)
        outside = self.root.parent / (self.root.name + '-outside.json')
        for draft, output in [(str(self.draft), 'working.json'), (str(self.draft), 'candidate-inputs'),
                               (str(self.draft), str(outside)), (str(outside), str(self.output))]:
            with self.subTest(draft=draft, output=output), self.assertRaises(ValueError):
                tools.prepare('results', 'support', draft, output)
        for section, summary in [(' results', False), ('', False), ('results', 1), ('results', 'true')]:
            with self.assertRaises(ValueError):
                tools.prepare(section, 'support', str(self.draft), str(self.output), summary=summary)
        with self.assertRaisesRegex(ValueError, 'learning_context'):
            tools.prepare('methods', 'support', str(self.draft), str(self.output))
        self.assertFalse(Path(self.db).exists())
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
