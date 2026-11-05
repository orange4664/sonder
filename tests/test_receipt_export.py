"""Durable receipts are exact database evidence, never a second feedback ledger."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('receipt_export_engine', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)


class ReceiptExportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='sonder-receipt-export-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.db = self.root / 'preference.db'
        self.file = self.root / 'candidate.json'
        self.receipts = self.root / 'choice-receipts'
        self.receipts.mkdir()
        self.output = self.receipts / '回执 with spaces.json'
        self.data = {
            'round_id': 'receipt-S001', 'chosen': 'own', 'author_owned': True,
            'learning_context': {'section': 'results', 'role': 'support'},
            'candidates': [
                {'id': 'own', 'label': 'Other', 'text': '实验记录将异常波形标记为待复核。'},
                {'id': 'alt', 'label': 'Light', 'text': '实验记录把异常波形标记为待复核。'},
            ],
        }
        self.save()

    def save(self, data=None):
        self.file.write_text(json.dumps(self.data if data is None else data, ensure_ascii=False))

    def export(self, output=None, **kwargs):
        helper = getattr(learn, 'choice_file_export', None)
        self.assertTrue(callable(helper), 'shared choice receipt export API is missing')
        return helper(str(self.db), 'results', 'support', self.file,
                      self.output if output is None else output, **kwargs)

    def plain(self):
        return learn.choice_file(str(self.db), 'results', 'support', self.file)

    def dump(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def envelope(self):
        with closing(sqlite3.connect(self.db)) as conn:
            row = conn.execute('SELECT round_id,payload_hash,created,result_json FROM choice_events '
                               'WHERE round_id=?', (self.data['round_id'],)).fetchone()
        self.assertIsNotNone(row)
        return {'receipt_export_version': 1, 'round_id': row[0], 'payload_hash': row[1],
                'created': row[2], 'result': json.loads(row[3])}

    def canonical(self, envelope):
        return (json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')

    def cli(self, *tail, discard_stdout=False):
        return subprocess.run(
            [sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'), 'choice-file',
             str(self.db), 'results', 'support', str(self.file), *map(str, tail)],
            stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=30)

    def test_normal_export_is_original_receipt_and_retry_has_no_feedback_or_file_writes(self):
        result = self.export()
        envelope = self.envelope()
        self.assertEqual(json.loads(self.output.read_text()), envelope)
        self.assertEqual(self.output.read_bytes(), self.canonical(envelope))
        self.assertEqual(result, {**envelope['result'], 'receipt_file': str(self.output),
                                  'receipt_file_reused': False})
        self.assertFalse(result['replayed'])
        self.assertFalse(envelope['result']['replayed'])
        before = self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('replay extracted features')):
            retry = self.export()
        self.assertEqual(retry, {**result, 'replayed': True, 'receipt_file_reused': True})
        self.assertEqual((self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns), before)
        self.assertEqual(self.plain(), {**envelope['result'], 'replayed': True})
        self.assertNotIn('receipt_file', envelope['result'])

    def test_optout_export_preserves_empty_learning_tables_and_exact_original_result(self):
        self.save({**self.data, 'learn_from_choice': False})
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('opt-out extracted features')):
            result = self.export()
        self.assertFalse(result['feedback_applied'])
        self.assertFalse(result['author_exemplar'])
        self.assertEqual(json.loads(self.output.read_text()), self.envelope())
        with closing(sqlite3.connect(self.db)) as conn:
            for table in ('prefs', 'phrase_prefs', 'syntax_prefs', 'exemplars', 'seen'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        before = self.dump()
        self.assertTrue(self.export()['replayed'])
        self.assertEqual(self.dump(), before)

    def test_old_receipt_exports_exactly_without_backfill_or_replayed_projection(self):
        original = self.plain()
        legacy = {k: v for k, v in original.items()
                  if k not in ('feedback_applied', 'learning_context', 'heuristic_template_active')}
        legacy['old_diagnostic'] = {'retained': ['literal', 1, False]}
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE choice_events SET result_json=?', (json.dumps(legacy, ensure_ascii=False),))
        before = self.dump()
        result = self.export()
        self.assertEqual(json.loads(self.output.read_text())['result'], legacy)
        self.assertEqual(result, {**legacy, 'replayed': True, 'receipt_file': str(self.output),
                                  'receipt_file_reused': False})
        self.assertEqual(self.dump(), before)

    def test_default_api_cli_shape_and_lost_stdout_recovery(self):
        first = self.cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        original = json.loads(first.stdout)
        self.assertNotIn('receipt_file', original)
        before = self.dump()
        lost = self.cli('--receipt-file', self.output, discard_stdout=True)
        self.assertEqual(lost.returncode, 0, lost.stderr)
        self.assertTrue(self.output.is_file(), 'successful export must create its receipt file')
        saved = self.output.read_bytes(), self.output.stat().st_mtime_ns
        retry = self.cli('--receipt-file', self.output)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(json.loads(retry.stdout), {**original, 'replayed': True,
                                                  'receipt_file': str(self.output), 'receipt_file_reused': True})
        self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), saved)
        self.assertEqual(self.dump(), before)

    def test_first_export_cli_survives_lost_stdout_with_one_event(self):
        lost = self.cli('--receipt-file', self.output, discard_stdout=True)
        self.assertEqual(lost.returncode, 0, lost.stderr)
        self.assertTrue(self.output.is_file(), 'successful export must survive lost stdout')
        before = self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns
        retry = self.cli('--receipt-file', self.output)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        result = json.loads(retry.stdout)
        self.assertTrue(result['replayed'])
        self.assertTrue(result['receipt_file_reused'])
        self.assertEqual((self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns), before)

    def test_cli_rejects_invalid_tail_before_creating_database(self):
        for tail in [('--receipt-file',), ('--receipt-file', self.output, 'extra'),
                     ('--receipt', self.output), ('garbage',),
                     ('--receipt-file', self.output, '--receipt-file', self.output)]:
            with self.subTest(tail=tail):
                result = self.cli(*tail)
                self.assertNotEqual(result.returncode, 0, 'unknown choice-file arguments must fail')
                self.assertIn('--receipt-file', result.stderr)
                self.assertFalse(self.db.exists())
                self.assertFalse(self.output.exists())

    def test_invalid_choice_identity_and_context_fail_before_store_access(self):
        invalid = [({k: v for k, v in self.data.items() if k != 'round_id'}, {}),
                   ({k: v for k, v in self.data.items() if k != 'chosen'}, {}),
                   ({**self.data, 'chosen': 'absent'}, {}),
                   ({**self.data, 'round_id': ' '}, {}),
                   ({**self.data, 'learn_from_choice': 'false'}, {}),
                   ({**self.data, 'learning_context': {'section': 'methods', 'role': 'support'}}, {}),
                   (self.data, {'expected_round_id': 'wrong-id'})]
        for data, kwargs in invalid:
            with self.subTest(data=data, kwargs=kwargs):
                self.save(data)
                with self.assertRaises(ValueError):
                    self.export(**kwargs)
                self.assertFalse(self.db.exists())
                self.assertFalse(self.output.exists())

    def test_output_paths_and_conflicts_fail_without_overwriting_or_feedback(self):
        external = self.root / 'unmanaged.txt'
        external.write_bytes(b'private unmanaged content')
        link = self.receipts / 'linked.json'
        link.symlink_to(external)
        dangling = self.receipts / 'dangling.json'
        dangling.symlink_to(self.root / 'absent-target')
        for output in [self.root / 'missing/receipt.json', self.receipts, self.file, self.db,
                       external, link, dangling, '']:
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.export(output=output)
            self.assertFalse(self.db.exists())
        self.assertEqual(external.read_bytes(), b'private unmanaged content')
        self.assertEqual(json.loads(self.file.read_text()), self.data)
        self.assertTrue(dangling.is_symlink())

    def test_output_cannot_occupy_missing_database_sqlite_or_engine_control_paths(self):
        reserved = [self.db, *(Path(str(self.db) + suffix) for suffix in ('-wal', '-shm', '-journal')),
                    *(self.root / name for name in (learn.BACKEND_FILE, learn.SYNTAX_BACKEND_FILE,
                                                     learn.SYNTAX_MODEL_FILE, learn.SYNTAX_MODEL_VERSION_FILE))]
        for output in reserved:
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'receipt'):
                self.export(output=output)
            self.assertFalse(self.db.exists())
            self.assertFalse(output.exists())

    def test_existing_envelope_cannot_substitute_for_missing_database_event(self):
        self.plain()
        self.output.write_bytes(self.canonical(self.envelope()))
        self.db.unlink()
        before = self.output.read_bytes(), self.output.stat().st_mtime_ns
        with self.assertRaisesRegex(ValueError, 'receipt'):
            self.export()
        self.assertFalse(self.db.exists(), 'a sidecar alone must not fabricate a committed database event')
        self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), before)

    def test_existing_wrong_result_or_version_is_rejected_without_core_feedback(self):
        self.plain()
        original = self.envelope()
        before = self.dump()
        for change in [{'result': {**original['result'], 'chosen': 'alt'}},
                       {'result': {**original['result'], 'replayed': True}},
                       {'result': {**original['result'], 'features': float(original['result']['features'])}},
                       {'receipt_export_version': True}, {'payload_hash': 'wrong'}, {'created': 'wrong'}]:
            self.output.write_bytes(self.canonical({**original, **change}))
            content = self.output.read_bytes(), self.output.stat().st_mtime_ns
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'receipt'):
                self.export()
            self.assertEqual(self.dump(), before)
            self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), content)
        # Even an exact file cannot bypass the core's saved decision identity.
        self.output.write_bytes(self.canonical(original))
        self.save({**self.data, 'chosen': 'alt'})
        with self.assertRaisesRegex(ValueError, 'round_id'):
            self.export()
        self.assertEqual(self.dump(), before)

    def test_existing_matching_file_still_runs_core_choice_checks(self):
        self.export()
        before = self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns
        with patch.object(learn, '_choice_data', side_effect=ValueError('core decision check refused')):
            with self.assertRaisesRegex(ValueError, 'core decision check refused'):
                self.export()
        self.assertEqual((self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns), before)

    def test_publication_failure_after_commit_is_explicit_and_retry_is_once_only(self):
        self.assertTrue(hasattr(learn, '_publish_receipt_file'), 'receipt publisher is missing')
        with patch.object(learn, '_publish_receipt_file', side_effect=OSError('simulated full disk')):
            with self.assertRaisesRegex(ValueError, 'may already be recorded.*original.*round_id'):
                self.export()
        self.assertFalse(self.output.exists())
        before = self.dump()
        result = self.export()
        self.assertTrue(result['replayed'])
        self.assertFalse(result['receipt_file_reused'])
        self.assertEqual(self.dump(), before)
        self.assertEqual(json.loads(self.output.read_text()), self.envelope())

    def test_failure_after_publication_retains_file_and_retry_reuses_it(self):
        self.assertTrue(hasattr(learn, '_publish_receipt_file'), 'receipt publisher is missing')
        publish = learn._publish_receipt_file

        def fail_after_publish(*args):
            publish(*args)
            raise OSError('simulated response loss after publication')

        with patch.object(learn, '_publish_receipt_file', side_effect=fail_after_publish):
            with self.assertRaisesRegex(ValueError, 'may already be recorded.*round_id'):
                self.export()
        before = self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns
        self.assertTrue(self.export()['receipt_file_reused'])
        self.assertEqual((self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns), before)

    def test_directory_sync_failure_is_recoverable_and_retry_flushes_existing_file(self):
        self.assertTrue(hasattr(learn, '_sync_receipt_directory'), 'durable receipt directory sync is missing')
        with patch.object(learn, '_sync_receipt_directory', side_effect=OSError('simulated directory sync failure')):
            with self.assertRaisesRegex(ValueError, 'may already be recorded.*round_id'):
                self.export()
        before = self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns
        with patch.object(learn, '_sync_receipt_directory', wraps=learn._sync_receipt_directory) as sync:
            result = self.export()
        self.assertTrue(result['receipt_file_reused'])
        self.assertEqual(sync.call_count, 1, 'retry must finish directory durability after a partial export')
        self.assertEqual((self.dump(), self.output.read_bytes(), self.output.stat().st_mtime_ns), before)

    def test_publication_and_retry_fsync_complete_file_and_directory(self):
        sync = os.fsync
        synced = []

        def observe(descriptor):
            mode = os.fstat(descriptor).st_mode
            synced.append('file' if stat.S_ISREG(mode) else 'directory' if stat.S_ISDIR(mode) else 'other')
            sync(descriptor)

        with patch.object(learn.os, 'fsync', side_effect=observe):
            self.export()
        self.assertEqual(synced, ['file', 'directory'])
        synced.clear()
        with patch.object(learn.os, 'fsync', side_effect=observe):
            self.assertTrue(self.export()['receipt_file_reused'])
        self.assertEqual(synced, ['file', 'directory'])

    def test_postcommit_mismatched_database_hash_or_result_cannot_publish_success(self):
        self.assertTrue(hasattr(learn, '_choice_data'), 'single-snapshot feedback core is missing')
        original = learn._choice_data
        for column, value in [('payload_hash', 'wrong-decision'), ('result_json', '{"chosen":"alt"}')]:
            with self.subTest(column=column):
                if self.db.exists():
                    self.db.unlink()

                def corrupt_after_commit(*args, **kwargs):
                    result = original(*args, **kwargs)
                    with closing(sqlite3.connect(self.db)) as conn, conn:
                        conn.execute('UPDATE choice_events SET ' + column + '=?', (value,))
                    return result

                with patch.object(learn, '_choice_data', side_effect=corrupt_after_commit):
                    with self.assertRaisesRegex(ValueError, 'may already be recorded.*round_id'):
                        self.export()
                self.assertFalse(self.output.exists())
                with closing(sqlite3.connect(self.db)) as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0], 1)
                    self.assertEqual(conn.execute('SELECT SUM(picks) FROM prefs').fetchone()[0], 1)

    def test_concurrent_same_id_publishes_one_complete_file_and_one_feedback_event(self):
        self.assertTrue(hasattr(learn, '_publish_receipt_file'), 'receipt publisher is missing')
        learn.list_exemplars(str(self.db))
        barrier = threading.Barrier(4, timeout=10)
        publish = learn._publish_receipt_file

        def simultaneous_publish(*args):
            barrier.wait()
            return publish(*args)

        with patch.object(learn, '_publish_receipt_file', side_effect=simultaneous_publish), \
                ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.export(), range(4)))
        self.assertEqual(sum(not item['replayed'] for item in results), 1)
        self.assertEqual(sum(not item['receipt_file_reused'] for item in results), 1)
        self.assertEqual(json.loads(self.output.read_text()), self.envelope())
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT SUM(picks) FROM prefs').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT SUM(rewards) FROM exemplars').fetchone()[0], 1)
        self.assertEqual(list(self.receipts.iterdir()), [self.output])

    def test_racing_conflicting_file_is_preserved_and_committed_choice_remains_retryable(self):
        self.assertTrue(hasattr(learn, '_publish_receipt_file'), 'receipt publisher is missing')
        publish = learn._publish_receipt_file

        def competitor(*args):
            self.output.write_bytes(b'unrelated file won publication')
            return publish(*args)

        with patch.object(learn, '_publish_receipt_file', side_effect=competitor):
            with self.assertRaisesRegex(ValueError, 'may already be recorded.*round_id'):
                self.export()
        self.assertEqual(self.output.read_bytes(), b'unrelated file won publication')
        before = self.dump()
        recovered = self.export(output=self.receipts / 'recovered.json')
        self.assertTrue(recovered['replayed'])
        self.assertEqual(self.dump(), before)
        self.assertEqual(list(self.receipts.glob('.sonder-*')), [])

    def test_single_candidate_snapshot_survives_file_change_after_read(self):
        original = learn._candidate_data

        def read_then_replace(path):
            snapshot = original(path)
            self.save({**self.data, 'round_id': 'unapproved-replacement', 'chosen': 'alt'})
            return snapshot

        with patch.object(learn, '_candidate_data', side_effect=read_then_replace) as reader:
            try:
                result = self.export(expected_round_id=self.data['round_id'])
            except ValueError as error:
                self.fail('the already validated saved snapshot must remain accepted: ' + str(error))
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(result['round_id'], self.data['round_id'])
        self.assertEqual(result['chosen'], 'own')
        self.assertEqual(json.loads(self.output.read_text()), self.envelope())
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT round_id FROM choice_events').fetchall(), [(self.data['round_id'],)])


if __name__ == '__main__':
    unittest.main()
