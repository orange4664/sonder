"""Candidates compare one complete source span; retries retain that identity."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scope_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
sys.path.insert(0, str(ROOT / 'library'))
from mcp_tools import WorkspaceTools


class EditScopeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='sonder-scope-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = self.root / 'preference.db'
        (self.root / 'candidate-inputs').mkdir()
        self.file = self.root / 'candidate-inputs/round.json'
        self.first = '研究者测量了电压[1]。'
        self.neighbor = '研究者记录了电流[2]。'
        self.before = self.first + self.neighbor
        self.scope = {'sentence_ids': ['S001', 'S002'], 'before_text': self.before,
                      'keep_original_id': 'keep'}
        self.data = {'round_id': 'scope-S001-S002',
                     'learning_context': {'section': 'methods', 'role': 'method'},
                     'edit_scope': self.scope, 'chosen': 'keep', 'author_owned': True,
                     'candidates': [
                         {'id': 'keep', 'label': 'Unchanged', 'text': self.before},
                         {'id': 'bold', 'label': 'Merge', 'text': '研究者测量了电压[1]，记录了电流[2]。'},
                         {'id': 'light', 'label': 'Light', 'text': '研究者已测量电压[1]。' + self.neighbor}]}

    def save(self, data=None):
        self.file.write_text(json.dumps(self.data if data is None else data, ensure_ascii=False))

    def choice(self):
        return learn.choice_file(str(self.db), 'methods', 'method', self.file)

    def dump(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def test_invalid_scope_fails_before_database(self):
        invalid = [None, [], '', {}, {'sentence_ids': ['S001']},
                   {**self.scope, 'extra': 1}]
        for ids in ([], 'S001', [None], [''], [' S001'], ['S001 '], ['S001', 'S001'], [1]):
            invalid.append({**self.scope, 'sentence_ids': ids})
        for before in (None, '', ' \n ', 1):
            invalid.append({**self.scope, 'before_text': before})
        for keep in (None, '', ' keep', 1, 'missing', 'bold'):
            invalid.append({**self.scope, 'keep_original_id': keep})
        for scope in invalid:
            with self.subTest(scope=scope):
                self.save({**self.data, 'edit_scope': scope})
                with self.assertRaisesRegex(ValueError, 'edit_scope'):
                    self.choice()
                self.assertFalse(self.db.exists())
        data = deepcopy(self.data)
        data.pop('round_id')
        self.save(data)
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.choice()
        self.assertFalse(self.db.exists())

    def test_exact_baseline_root_only_and_all_file_entrypoints(self):
        # Citation and whitespace normalization must never hide a different span.
        for before in (self.before + '\n', self.before.replace('[1]', '[9]')):
            self.save({**self.data, 'edit_scope': {**self.scope, 'before_text': before}})
            service = WorkspaceTools(self.root)
            for method in ('rank', 'record_choice'):
                args = ['methods', 'method', 'candidate-inputs/round.json']
                if method == 'record_choice':
                    args.append(self.data['round_id'])
                with self.assertRaisesRegex(ValueError, 'edit_scope'):
                    getattr(service, method)(*args)
                self.assertFalse(self.db.exists())
            for command in ('rank-file', 'choice-file'):
                p = subprocess.run([sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'),
                                    command, str(self.db), 'methods', 'method', str(self.file)],
                                   capture_output=True, text=True, timeout=30)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn('edit_scope', p.stderr)
                self.assertFalse(self.db.exists())
        data = deepcopy(self.data)
        data['candidates'][0]['edit_scope'] = data.pop('edit_scope')
        self.save(data)
        with self.assertRaisesRegex(ValueError, 'file root'):
            self.choice()
        self.assertFalse(self.db.exists())

    def test_binding_preserves_scope_literal_order_and_retry_bytes(self):
        draft = deepcopy(self.data)
        literal = self.first + '\n  ' + self.neighbor + '\n'
        draft['edit_scope']['before_text'] = literal
        draft['edit_scope']['sentence_ids'] = ['S009', 'S002']
        draft['candidates'][0]['text'] = literal
        for candidate in draft['candidates']:
            candidate.update(analysis={'spans': []}, grammar={'frames': []})
        self.save(draft)
        output = self.file.with_name('bound.json')
        try:
            first = learn.bind_file(self.file, output)
        except ValueError as error:
            self.fail('binder must accept edit_scope: ' + str(error))
        bound = json.loads(output.read_text())
        self.assertEqual(bound['edit_scope'], draft['edit_scope'])
        before = output.read_bytes(), output.stat().st_mtime_ns
        self.assertFalse(first['reused'])
        self.assertTrue(learn.bind_file(self.file, output)['reused'])
        self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
        self.assertFalse(self.db.exists())

    def test_normal_and_editorial_receipts_replay_with_scope(self):
        for learning in (True, False):
            self.db = self.root / f'{learning}.db'
            self.save({**self.data, 'learn_from_choice': learning})
            receipt = self.choice()
            self.assertEqual(receipt.get('edit_scope'), self.scope)
            self.assertEqual(receipt['feedback_applied'], learning)
            before = self.dump()
            self.assertEqual(self.choice(), {**receipt, 'replayed': True})
            self.assertEqual(self.dump(), before)
            if not learning:
                with closing(sqlite3.connect(self.db)) as conn:
                    for table in ('prefs', 'phrase_prefs', 'syntax_prefs', 'exemplars'):
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_scope_identity_cannot_change_after_commit(self):
        data = deepcopy(self.data)
        data['edit_scope'].pop('keep_original_id')  # optional, no label inference
        for learning in (True, False):
            self.db = self.root / f'identity-{learning}.db'
            data['learn_from_choice'] = learning
            self.save(data)
            self.choice()
            before = self.dump()
            for scope in ({**data['edit_scope'], 'sentence_ids': ['S002', 'S001']},
                          {**data['edit_scope'], 'sentence_ids': ['S003', 'S004']},
                          {**data['edit_scope'], 'before_text': self.before + '\n'},
                          {**data['edit_scope'], 'keep_original_id': 'keep'}):
                self.save({**data, 'edit_scope': scope})
                with self.assertRaisesRegex(ValueError, 'round_id'):
                    self.choice()
                self.assertEqual(self.dump(), before)
            unscoped = {k: v for k, v in data.items() if k != 'edit_scope'}
            self.save(unscoped)
            with self.assertRaisesRegex(ValueError, 'round_id'):
                self.choice()
            self.assertEqual(self.dump(), before)

    def test_legacy_absent_scope_hash_and_receipt_remain_exact(self):
        legacy = {k: v for k, v in self.data.items() if k != 'edit_scope'}
        self.save(legacy)
        receipt = self.choice()
        self.assertNotIn('edit_scope', receipt)
        payload = {'version': 1, 'section': 'methods', 'role': 'method',
                   'candidates': sorted(legacy['candidates'], key=lambda c: c['id']),
                   'chosen': 'keep', 'author_owned': True}
        expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                            separators=(',', ':')).encode()).hexdigest()
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT payload_hash FROM choice_events').fetchone()[0], expected)
        before = self.dump()
        self.assertEqual(self.choice(), {**receipt, 'replayed': True})
        self.assertEqual(self.dump(), before)
        self.save()  # Never retrofit a committed historical decision.
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.choice()
        self.assertEqual(self.dump(), before)

    def test_full_scope_keep_does_not_reject_untouched_neighbor(self):
        with patch.object(learn, '_now_iso', return_value='2026-10-27T00:00:00+00:00'):
            self.save({'round_id': 'seed', 'chosen': 'neighbor', 'candidates': [
                {'id': 'neighbor', 'label': 'Other', 'text': self.neighbor}]})
            self.choice()
            keys = {'cjk2:录了', 'cjk2:电流', 'cjk2:记录', 'cjk3:了电流', 'cjk3:录了电', 'cjk3:记录了'}
            def weights():
                with closing(sqlite3.connect(self.db)) as conn:
                    return {key: value for key, value in conn.execute('SELECT feature,weight FROM phrase_prefs') if key in keys}
            before = weights()
            self.assertEqual(set(before), keys)
            self.save()
            self.choice()
            after = weights()
            self.assertTrue(all(after[key] >= before[key] for key in keys), (before, after))
            snapshot = self.dump()
            self.choice()
            self.assertEqual(self.dump(), snapshot)


if __name__ == '__main__':
    unittest.main()
