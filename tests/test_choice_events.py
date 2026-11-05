"""Feedback delivery can repeat; an author's decision must not be counted twice."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'library/learn.py'
SPEC = importlib.util.spec_from_file_location('choice_event_learn', ENGINE)
learn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learn)
NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


class ChoiceEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sonder-choice-events-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = str(self.root / 'preference.db')
        self.path = self.root / 'choice.json'
        self.data = {
            'round_id': 'S002-decision-1',
            'candidates': [
                {'id': 'own', 'label': 'Other', 'text': '我们通过实验测量流量，结果表明样品稳定。'},
                {'id': 'reverse', 'label': 'B', 'text': '结果表明样品稳定，我们通过实验测量流量。'},
            ],
            'chosen': 'own', 'author_owned': True,
        }

    def submit(self, data=None, section='results', role='support'):
        self.path.write_text(json.dumps(self.data if data is None else data, ensure_ascii=False))
        return learn.choice_file(self.db, section, role, self.path)

    def snapshot(self, include_events=True):
        tables = ['prefs', 'phrase_prefs', 'syntax_prefs', 'exemplars', 'seen']
        if include_events:
            tables.append('choice_events')
        with closing(sqlite3.connect(self.db)) as conn:
            return {table: conn.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
                    for table in tables}

    def test_expected_round_id_is_checked_before_any_store_access(self):
        self.assertIn('expected_round_id',inspect.signature(learn.choice_file).parameters)
        cases=[({k:v for k,v in self.data.items() if k!='round_id'},'expected'),
               (self.data,'different'),(self.data,''),(self.data,True)]
        for data,expected in cases:
            self.path.write_text(json.dumps(data,ensure_ascii=False))
            with self.subTest(expected=expected),self.assertRaisesRegex(ValueError,'round_id'):
                learn.choice_file(self.db,'results','support',self.path,expected_round_id=expected)
            self.assertFalse(Path(self.db).exists())
        self.path.write_text(json.dumps(self.data,ensure_ascii=False))
        result=learn.choice_file(self.db,'results','support',self.path,expected_round_id=self.data['round_id'])
        before=self.snapshot()
        self.assertEqual(learn.choice_file(self.db,'results','support',self.path,expected_round_id=self.data['round_id']),{**result,'replayed':True})
        self.assertEqual(self.snapshot(),before)

    def test_expected_id_uses_the_validated_file_snapshot(self):
        self.assertIn('expected_round_id',inspect.signature(learn.choice_file).parameters)
        self.path.write_text(json.dumps(self.data,ensure_ascii=False))
        changed={**self.data,'round_id':'a-different-saved-decision'}
        with patch.object(learn,'_candidate_data',return_value=learn._validated_candidate_data(changed)),self.assertRaisesRegex(ValueError,'round_id'):
            learn.choice_file(self.db,'results','support',self.path,expected_round_id=self.data['round_id'])
        self.assertFalse(Path(self.db).exists())

    def test_same_decision_replay_keeps_all_history_and_timestamps_unchanged(self):
        with patch.object(learn, '_now_iso', return_value=NOW.isoformat()):
            first = self.submit()
        before = self.snapshot()
        # Reordering displayed candidates and adding notes do not change a decision.
        retry = {**self.data, 'candidates': self.data['candidates'][::-1], 'note': 'retry'}
        with patch.object(learn, '_now_iso', return_value=(NOW + timedelta(days=90)).isoformat()), \
             patch.object(learn, '_syntax_features', side_effect=AssertionError('must not reparse a replay')):
            second = self.submit(retry)
        self.assertFalse(first['replayed'])
        self.assertTrue(first['idempotent'])
        self.assertEqual(first['round_id'], self.data['round_id'])
        self.assertEqual(second, {**first, 'replayed': True})
        self.assertEqual(self.snapshot(), before)

    def test_same_text_on_a_new_decision_still_reinforces(self):
        first = self.submit()
        second = self.submit({**self.data, 'round_id': 'S003-decision-1'})
        self.assertFalse(first['replayed'])
        self.assertFalse(second['replayed'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT picks FROM prefs').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT rewards FROM exemplars').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0], 2)

    def test_reusing_id_with_changed_decision_fails_without_mutation(self):
        self.submit()
        before = self.snapshot()
        changed_text = json.loads(json.dumps(self.data))
        changed_text['candidates'][0]['text'] += '仍需验证。'
        changed_label = json.loads(json.dumps(self.data))
        changed_label['candidates'][0]['label'] = 'New label'
        cases = [({**self.data, 'chosen': 'reverse'}, 'results', 'support'),
                 ({**self.data, 'author_owned': False}, 'results', 'support'),
                 (changed_text, 'results', 'support'),
                 (changed_label, 'results', 'support'),
                 (self.data, 'methods', 'support'),
                 (self.data, 'results', 'topic')]
        for data, section, role in cases:
            with self.subTest(section=section, role=role, data=data):
                with self.assertRaisesRegex(ValueError, 'round_id'):
                    self.submit(data, section, role)
                self.assertEqual(self.snapshot(), before)

    def test_invalid_round_ids_fail_before_opening_database(self):
        for value in (None, False, 0, [], '', ' ', ' trailing ', 'x' * 201):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'round_id'):
                    self.submit({**self.data, 'round_id': value})
                self.assertFalse(Path(self.db).exists())

    def test_legacy_no_id_remains_explicitly_non_idempotent(self):
        data = {key: value for key, value in self.data.items() if key != 'round_id'}
        for _ in range(2):
            result = self.submit(data)
            self.assertIsNone(result['round_id'])
            self.assertFalse(result['idempotent'])
            self.assertFalse(result['replayed'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT picks FROM prefs').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0], 0)

    def test_receipt_failure_rolls_back_weights_and_exemplar_then_retry_succeeds(self):
        learn.list_exemplars(self.db)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON choice_events "
                         "BEGIN SELECT RAISE(ABORT, 'receipt write failed'); END")
        before = self.snapshot()
        with self.assertRaises(sqlite3.DatabaseError):
            self.submit()
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('DROP TRIGGER fail_receipt')
        self.assertFalse(self.submit()['replayed'])
        self.assertTrue(self.submit()['replayed'])

    def test_two_simultaneous_deliveries_recheck_receipt_under_write_lock(self):
        learn.list_exemplars(self.db)
        self.path.write_text(json.dumps(self.data))
        barrier = threading.Barrier(2, timeout=5)
        original = learn._syntax_features

        def extraction(text, db, diagnostics=None):
            # Both callers have passed the optimistic receipt lookup before either writes.
            if text == self.data['candidates'][0]['text']:
                barrier.wait()
            return original(text, db, diagnostics=diagnostics)

        with patch.object(learn, '_syntax_features', side_effect=extraction), \
             ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(learn.choice_file, self.db, 'results', 'support', self.path)
                       for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(r['replayed'] for r in results), [False, True])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT picks FROM prefs').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT rewards FROM exemplars').fetchone()[0], 1)

    def test_fresh_processes_share_the_receipt(self):
        self.path.write_text(json.dumps(self.data))
        cmd = [sys.executable, '-I', str(ENGINE), 'choice-file', self.db,
               'results', 'support', str(self.path)]
        processes = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(4)]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(not r['replayed'] for r in results), 1)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT picks FROM prefs').fetchone()[0], 1)

    def test_prune_does_not_erase_receipts_or_resurrect_old_feedback(self):
        with patch.object(learn, '_now_iso', return_value=NOW.isoformat()):
            self.submit()
        with patch.object(learn, '_now_iso', return_value=(NOW + timedelta(days=400)).isoformat()):
            learn.prune(self.db)
            before = self.snapshot()
            self.assertEqual(before['phrase_prefs'], [])
            self.assertEqual(before['syntax_prefs'], [])
            self.assertEqual(before['exemplars'], [])
            self.assertTrue(self.submit()['replayed'])
        self.assertEqual(self.snapshot(), before)

    def test_v3_upgrade_adds_receipts_without_reinventing_past_decisions(self):
        self.submit({key: value for key, value in self.data.items() if key != 'round_id'})
        old = self.snapshot(include_events=False)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP TABLE choice_events')
            conn.execute('PRAGMA user_version=3')
        learn.list_exemplars(self.db)
        self.assertEqual(self.snapshot(include_events=False), old)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], learn.SCHEMA_VERSION)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0], 0)
        self.assertFalse(self.submit()['replayed'])

    def test_v3_receipt_schema_failure_preserves_version_and_previous_rows(self):
        self.submit({key: value for key, value in self.data.items() if key != 'round_id'})
        before = self.snapshot(include_events=False)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP TABLE choice_events')
            conn.execute('PRAGMA user_version=3')
        real_connect = sqlite3.connect

        def reject_new_table(db):
            connection = real_connect(db)
            connection.set_authorizer(lambda action, first, *rest:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE
                and first == 'choice_events' else sqlite3.SQLITE_OK)
            return connection

        with patch.object(learn.sqlite3, 'connect', side_effect=reject_new_table):
            with self.assertRaises(sqlite3.DatabaseError):
                learn.list_exemplars(self.db)
        self.assertEqual(self.snapshot(include_events=False), before)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='choice_events'").fetchone()[0], 0)

    def test_conflicting_concurrent_decisions_cannot_both_commit(self):
        learn.list_exemplars(self.db)
        self.path.write_text(json.dumps(self.data))
        second = self.root / 'conflicting.json'
        second.write_text(json.dumps({**self.data, 'chosen': 'reverse'}))
        barrier = threading.Barrier(2, timeout=5)
        original = learn._syntax_features

        def extraction(text, db, diagnostics=None):
            if text == self.data['candidates'][0]['text']:
                barrier.wait()
            return original(text, db, diagnostics=diagnostics)

        results, conflicts = [], []
        with patch.object(learn, '_syntax_features', side_effect=extraction), \
             ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(learn.choice_file, self.db, 'results', 'support', path)
                       for path in (self.path, second)]
            for future in futures:
                try:
                    results.append(future.result(timeout=10))
                except ValueError as error:
                    conflicts.append(str(error))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertIn('round_id', conflicts[0])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT SUM(picks) FROM prefs').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
