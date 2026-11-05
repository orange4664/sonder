"""One ranking reads one preference snapshot, after slow feature extraction."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rank_snapshot', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
T0 = '2026-10-28T00:00:00+00:00'
T30 = '2026-11-27T00:00:00+00:00'
T60 = '2026-12-27T00:00:00+00:00'


class RankSnapshotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-rank-snapshot-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = str(self.root / 'preferences.db')
        self.clock = T0
        clock = patch.object(learn, '_now_iso', side_effect=lambda: self.clock)
        self.now = clock.start()
        self.addCleanup(clock.stop)
        text = '团队分析样本。'
        evidence = {'start': 0, 'end': len(text), 'text': text}
        identity = {'version': 1, 'source': 'writing-model',
                    'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
        item = {'id': 'first', 'label': 'Same', 'text': text,
                'analysis': {**identity, 'spans': [{**evidence, 'move': 'method',
                              'polarity': 'affirmative', 'certainty': 'asserted'}]},
                'grammar': {**identity, 'frames': [{**evidence, 'kind': 'expression',
                             'form': 'verbal', 'voice': 'active', 'slots': [
                                 {'start': 0, 'end': 2, 'text': '团队', 'role': 'agent'},
                                 {'start': 2, 'end': 4, 'text': '分析', 'role': 'event'},
                                 {'start': 4, 'end': 6, 'text': '样本', 'role': 'patient'}]}]}}
        self.candidates = [item, {**deepcopy(item), 'id': 'second'}]
        self.choose('seed')

    def choose(self, round_id):
        path = self.root / (round_id + '.json')
        path.write_text(json.dumps({'round_id': round_id, 'chosen': 'first',
                                   'candidates': self.candidates}, ensure_ascii=False))
        return learn.choice_file(self.db, 'methods', 'detail', path)

    def rank(self):
        return learn.rank_candidates(self.db, 'methods', 'detail', self.candidates)

    def double_weights(self):
        # This independent writer must fail immediately if a reader holds a
        # rollback-journal lock; it never waits for the test to release parsing.
        with closing(sqlite3.connect(self.db, timeout=0)) as conn, conn:
            for table in ('prefs', 'phrase_prefs', 'syntax_prefs'):
                conn.execute('UPDATE ' + table + ' SET weight=weight*2')

    def rank_during_write(self, writer, read_function, read_count=1):
        """Commit a real WAL write at a deterministic point in ranking reads."""
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        entered, finished = threading.Event(), threading.Event()
        errors = []
        reads = 0
        rank_thread = threading.get_ident()
        original = getattr(learn, read_function)

        def read(*args, **kwargs):
            nonlocal reads
            result = original(*args, **kwargs)
            if threading.get_ident() == rank_thread:
                reads += 1
                if reads == read_count:
                    entered.set()
                    if not finished.wait(10):
                        raise RuntimeError('concurrent WAL writer did not finish')
            return result

        def write():
            try:
                if not entered.wait(10):
                    raise RuntimeError('ranking did not reach the gated read')
                writer()
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=write, name='snapshot-writer')
        with patch.object(learn, read_function, side_effect=read):
            worker.start()
            try:
                rows = self.rank()
            finally:
                entered.set()
                worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertGreaterEqual(reads, read_count, 'writer must interleave with the requested read')
        self.assertTrue(finished.is_set())
        return rows

    def test_feedback_between_candidates_uses_one_snapshot(self):
        before = self.rank()
        syntax = learn._candidate_syntax_features(self.candidates[0], self.db)[0]
        self.assertTrue(syntax)
        # The last syntax read finishes candidate one's weight reads. A new
        # real choice commits before candidate two starts its preference reads.
        during = self.rank_during_write(lambda: self.choose('concurrent-choice'),
                                       '_syntax_weight_of', len(syntax))
        after = self.rank()
        self.assertGreater(after[0]['score'], before[0]['score'])
        self.assertEqual(during[0]['score'], during[1]['score'],
                         'identical candidates cannot straddle two feedback histories')
        self.assertEqual(during, before)
        self.assertEqual(after[0]['score'], after[1]['score'])

    def test_local_global_labels_phrase_and_structure_share_snapshot(self):
        learn.global_pref(self.db, 'Same', 1500)
        before = self.rank()
        self.assertTrue(before[0]['matched_features'])
        self.assertTrue(before[0]['matched_analysis_features'])
        self.assertTrue(before[0]['matched_grammar_features'])
        self.assertGreater(before[0]['base_syntax_score'], 0)
        # Read one local label, then atomically change both label scopes and
        # every text layer. Checking the full rows forbids mixed-layer snapshots.
        during = self.rank_during_write(self.double_weights, '_weight_of')
        after = self.rank()
        self.assertEqual(before[0]['label_weight'], 1500)
        self.assertEqual(after[0]['label_weight'], 3000)
        self.assertGreater(after[0]['grammar_score'], before[0]['grammar_score'])
        self.assertEqual(during, before)

    def test_slow_parser_does_not_hold_rollback_read_lock(self):
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        entered, release = threading.Event(), threading.Event()
        original = learn._candidate_syntax_features
        parsed, errors, rows = [], [], []

        def parse(candidate, *args, **kwargs):
            parsed.append(candidate['id'])
            if candidate['id'] == 'second':
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('test parser was not released')
            return original(candidate, *args, **kwargs)

        def rank():
            try:
                rows.extend(self.rank())
            except BaseException as error:
                errors.append(error)

        with patch.object(learn, '_candidate_syntax_features', side_effect=parse):
            worker = threading.Thread(target=rank, name='slow-ranking')
            worker.start()
            try:
                self.assertTrue(entered.wait(10))
                # timeout=0 avoids a lock-wait/release dependency in the test.
                self.double_weights()
            finally:
                release.set()
                worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(parsed, ['first', 'second'])
        self.assertEqual(rows, self.rank(), 'both candidates read after parsing finishes')

    def test_ranking_clock_is_sampled_once_after_all_extraction(self):
        original = learn._candidate_syntax_features
        parsed = []

        def parse(candidate, *args, **kwargs):
            parsed.append(candidate['id'])
            self.clock = T30 if len(parsed) == 1 else T60
            return original(candidate, *args, **kwargs)

        self.now.reset_mock()
        with patch.object(learn, '_candidate_syntax_features', side_effect=parse):
            rows = self.rank()
        self.assertEqual(parsed, ['first', 'second'])
        self.assertEqual(self.now.call_count, 1)
        for row in rows:
            self.assertEqual(row['label_weight'], 250)
            self.assertEqual(row['phrase_score'], 250)
            self.assertEqual({item['weight'] for item in row['matched_syntax_features']}, {250})

    def test_errors_close_connection_and_release_read_transaction(self):
        original_open = learn._open
        for function in ('_candidate_syntax_features', '_legacy_label_weights',
                         '_phrase_weight_of', '_syntax_weight_of'):
            with self.subTest(function=function):
                connections = []
                original = getattr(learn, function)

                def open_db(db):
                    conn = original_open(db)
                    connections.append(conn)  # Keep alive so GC cannot hide leaks.
                    self.addCleanup(conn.close)
                    return conn

                def fail(*args, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError('injected ranking failure')

                with patch.object(learn, '_open', side_effect=open_db), \
                        patch.object(learn, function, side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, 'injected ranking failure'):
                        self.rank()
                self.double_weights()  # Real COMMIT proves rollback read locks are gone.
                self.assertEqual(len(connections), 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connections[0].execute('SELECT 1')

    def test_success_closes_connection_and_preserves_database(self):
        with closing(sqlite3.connect(self.db)) as conn:
            before = list(conn.iterdump())
        connection = learn._open(self.db)
        self.addCleanup(connection.close)
        with patch.object(learn, '_open', return_value=connection):
            rows = self.rank()
        self.assertEqual([row['id'] for row in rows], ['first', 'second'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(list(conn.iterdump()), before)
        self.double_weights()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')


if __name__ == '__main__':
    unittest.main()
