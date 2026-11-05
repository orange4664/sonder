"""A saved writing round retains its context through blueprint changes."""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('context_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
sys.path.insert(0, str(ROOT / 'library'))
from mcp_tools import WorkspaceTools


class ChoiceContextTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='sonder-context-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = self.root / 'preference.db'
        (self.root / 'candidate-inputs').mkdir()
        self.file = self.root / 'candidate-inputs/round.json'
        self.context = {'section': 'methods', 'role': 'hedge'}
        self.data = {'round_id': 'context-S001', 'learning_context': self.context,
                     'chosen': 'own', 'author_owned': True,
                     'candidates': [{'id': 'own', 'label': 'Other', 'text': '基线漂移仍需进一步检查。'},
                                    {'id': 'alt', 'label': 'Light', 'text': '基线漂移需要进一步检查。'}]}

    def save(self, data=None):
        self.file.write_text(json.dumps(self.data if data is None else data, ensure_ascii=False))

    def choice(self, role='hedge'):
        return learn.choice_file(str(self.db), 'methods', role, self.file)

    def dump(self):
        with closing(sqlite3.connect(self.db)) as c:
            return list(c.iterdump())

    def cli(self, command, role):
        return subprocess.run([sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'),
                               command, str(self.db), 'methods', role, str(self.file)],
                              capture_output=True, text=True, timeout=30)

    def test_invalid_context_or_candidate_placement_fails_before_database(self):
        invalid = [None, 'methods::hedge', [], {}, {'section': 'methods'},
                   {**self.context, 'extra': 1}, {**self.context, 'role': False},
                   {**self.context, 'role': ''}, {**self.context, 'role': ' hedge'}]
        for context in invalid:
            with self.subTest(context=context):
                self.save({**self.data, 'learning_context': context})
                with self.assertRaisesRegex(ValueError, 'learning_context'):
                    self.choice()
                self.assertFalse(self.db.exists())
        misplaced = deepcopy(self.data)
        misplaced.pop('learning_context')
        misplaced['candidates'][0]['learning_context'] = self.context
        self.save(misplaced)
        with self.assertRaisesRegex(ValueError, 'file root'):
            self.choice()
        self.assertFalse(self.db.exists())

    def test_binding_preserves_literal_context_and_retry_bytes(self):
        draft = deepcopy(self.data)
        for c in draft['candidates']:
            c.update(analysis={'spans': []}, grammar={'frames': []})
        self.save(draft)
        output = self.file.with_name('bound.json')
        try:
            first = learn.bind_file(self.file, output)
        except ValueError as error:
            self.fail('binder must accept saved context: ' + str(error))
        bound = json.loads(output.read_text())
        self.assertEqual(bound['learning_context'], self.context)
        before = output.read_bytes(), output.stat().st_mtime_ns
        second = learn.bind_file(self.file, output)
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
        self.assertFalse(self.db.exists())

    def test_wrong_context_rejected_for_pending_learning_and_editorial_only(self):
        for learning in (True, False):
            with self.subTest(learning=learning):
                self.save({**self.data, 'learn_from_choice': learning})
                with self.assertRaisesRegex(ValueError, 'learning_context'):
                    self.choice('limitation')
                self.assertFalse(self.db.exists())
        self.save()
        receipt = self.choice()
        self.assertEqual(receipt.get('learning_context'), self.context)
        before = self.dump()
        with self.assertRaisesRegex(ValueError, 'learning_context'):
            self.choice('limitation')
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.choice(), {**receipt, 'replayed': True})
        self.assertEqual(self.dump(), before)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT DISTINCT context FROM phrase_prefs').fetchall(), [('methods::hedge',)])

    def test_cli_and_mcp_rank_and_choice_use_saved_context_before_store_access(self):
        self.save()
        for command in ('rank-file', 'choice-file'):
            with self.subTest(command=command):
                p = self.cli(command, 'limitation')
                self.assertNotEqual(p.returncode, 0, 'wrong-context CLI command must fail')
                self.assertIn('learning_context', p.stderr)
                self.assertFalse(self.db.exists())
        service = WorkspaceTools(self.root)
        for method in ('rank', 'record_choice'):
            with self.subTest(method=method):
                args = ['methods', 'limitation', 'candidate-inputs/round.json']
                if method == 'record_choice':
                    args.append(self.data['round_id'])
                with self.assertRaisesRegex(ValueError, 'learning_context'):
                    getattr(service, method)(*args)
                self.assertFalse(self.db.exists())
        self.assertEqual(self.cli('rank-file', 'hedge').returncode, 0)
        self.assertEqual(len(service.rank('methods', 'hedge', 'candidate-inputs/round.json')['candidates']), 2)

    def test_legacy_context_addition_preserves_hash_and_old_receipt(self):
        legacy = {k: v for k, v in self.data.items() if k != 'learning_context'}
        self.save(legacy)
        first = self.choice()
        self.assertEqual(first.get('learning_context'), self.context)
        old = {k: v for k, v in first.items() if k != 'learning_context'}
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('UPDATE choice_events SET result_json=?', (json.dumps(old),))
        before = self.dump()
        self.save()  # Accurate original context supplied from historical evidence.
        self.assertEqual(self.choice(), {**old, 'replayed': True})
        self.assertEqual(self.dump(), before)
        changed = {**self.data, 'learning_context': {'section': 'methods', 'role': 'limitation'}}
        self.save(changed)
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.choice('limitation')
        self.assertEqual(self.dump(), before)

    def test_editorial_receipt_records_context_without_learning(self):
        self.save({**self.data, 'learn_from_choice': False})
        receipt = self.choice()
        self.assertEqual(receipt.get('learning_context'), self.context)
        self.assertFalse(receipt['feedback_applied'])
        with closing(sqlite3.connect(self.db)) as c:
            for table in ('prefs', 'phrase_prefs', 'syntax_prefs', 'exemplars'):
                self.assertEqual(c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
