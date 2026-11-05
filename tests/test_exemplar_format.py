"""Format-aware exemplar retrieval preserves author history and cache identity."""
from contextlib import closing
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('exemplar_format', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec); spec.loader.exec_module(learn)
NOW = '2026-10-28T08:00:00+00:00'
PLAIN = '测量结果不支持该机制。'
TEX = r'测量结果\emph{不}支持该机制。'
MD = 'pre**condition**'


class ExemplarFormatTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-exemplar-format-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.db = str(self.root/'preference.db')
        self.path = self.root/'choice.json'
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)

    def raw(self, sql, params=()):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql, params).fetchall()

    def dump(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def choice(self, event, text=MD, **extra):
        data = {'round_id': event, 'chosen': 'own', 'author_owned': True,
                'candidates': [{'id': 'own', 'label': 'Other', 'text': text}], **extra}
        self.path.write_text(json.dumps(data, ensure_ascii=False))
        return learn.choice_file(self.db, 'methods', 'detail', self.path)

    def require_mode(self):
        for function in (learn.store, learn.nearest):
            self.assertIn('text_format', inspect.signature(function).parameters)

    def make_old(self, *, version=4, modern=False, duplicate=False):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            extra = ', text_format TEXT, ngram_version INTEGER NOT NULL DEFAULT 0' if modern else ''
            conn.execute('CREATE TABLE exemplars(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, '
                         'text TEXT NOT NULL, ngram TEXT NOT NULL, created TEXT NOT NULL, last_at TEXT NOT NULL, '
                         'rewards INTEGER NOT NULL DEFAULT 0' + extra + ')')
            text = MD if modern else TEX
            conn.execute('INSERT INTO exemplars(id,kind,text,ngram,created,last_at,rewards) VALUES(7,?,?,?,?,?,4)',
                         ('author', text, learn._ngram_json(text), '2020-01-01 00:00:00', NOW))
            if modern:
                conn.execute("UPDATE exemplars SET text_format='markdown',ngram_version=1")
            if duplicate:
                conn.execute('INSERT INTO exemplars(id,kind,text,ngram,created,last_at,rewards) VALUES(9,?,?,?,?,?,3)',
                             ('author', text, '{}', '2019-01-01T00:00:00Z', '2026-10-28T07:00:00+00:00'))
            else:
                conn.execute('CREATE UNIQUE INDEX exemplars_kind_text ON exemplars(kind,text)')
            conn.execute(f'PRAGMA user_version={version}')

    def test_same_visible_tex_negation_ranks_above_opposite_claim(self):
        learn.store(self.db, 'author', '测量结果支持该机制。')
        learn.store(self.db, 'author', TEX)
        rows = learn.nearest(self.db, PLAIN)
        self.assertEqual(rows[0]['text'], TEX)
        self.assertEqual(rows[0]['sim'], 1.0)
        self.assertGreater(rows[0]['sim'], rows[1]['sim'])
        self.assertNotIn('text_format', rows[0])

    def test_explicit_markdown_query_and_store_use_visible_word_but_return_raw(self):
        self.require_mode()
        learn.store(self.db, 'author', MD, text_format='markdown')
        rows = learn.nearest(self.db, 'precondition')
        self.assertEqual((rows[0]['text'], rows[0].get('text_format'), rows[0]['sim']), (MD, 'markdown', 1.0))
        self.assertEqual(learn.nearest(self.db, 'pre*condition*', text_format='markdown')[0]['sim'], 1.0)
        self.assertEqual(learn.list_exemplars(self.db)[0]['text_format'], 'markdown')
        self.assertLess(learn.nearest(self.db, 'pre*condition*')[0]['sim'], 1.0)

    def test_v4_upgrade_changes_only_derived_cache_and_does_not_repeat(self):
        self.make_old()
        columns = 'id,kind,text,created,last_at,rewards'
        history = self.raw('SELECT '+columns+' FROM exemplars')
        learn.list_exemplars(self.db)
        self.assertEqual(self.raw('PRAGMA user_version'), [(learn.SCHEMA_VERSION,)])
        self.assertEqual(self.raw('SELECT '+columns+' FROM exemplars'), history)
        self.assertEqual(self.raw('SELECT text_format,ngram_version,ngram FROM exemplars'),
                         [(None, 1, learn._ngram_json(PLAIN))])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER no_reindex BEFORE UPDATE OF ngram ON exemplars BEGIN SELECT RAISE(ABORT,'repeated reindex'); END")
        before = self.dump()
        self.assertEqual(learn.nearest(self.db, PLAIN)[0]['sim'], 1.0)
        self.assertEqual(self.dump(), before)

    def test_v0_dedup_keeps_history_and_rebuilds_retained_markdown_mode(self):
        self.make_old(version=0, modern=True, duplicate=True)
        learn.list_exemplars(self.db)
        self.assertEqual(self.raw('PRAGMA user_version'), [(learn.SCHEMA_VERSION,)])
        self.assertEqual(self.raw('SELECT id,text_format,ngram_version,rewards,created,last_at FROM exemplars'),
                         [(7, 'markdown', 1, 7, '2019-01-01T00:00:00+00:00', NOW)])
        self.assertEqual(self.raw('SELECT ngram FROM exemplars'), [(learn._ngram_json('precondition'),)])

    def test_cache_failure_rolls_back_added_columns_and_schema(self):
        self.make_old()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER fail_cache BEFORE UPDATE OF ngram ON exemplars BEGIN SELECT RAISE(ABORT,'cache blocked'); END")
        before = self.dump()
        with self.assertRaisesRegex(sqlite3.DatabaseError, 'cache blocked'):
            learn.list_exemplars(self.db)
        self.assertEqual(self.dump(), before)
        self.assertNotIn('text_format', {row[1] for row in self.raw('PRAGMA table_info(exemplars)')})
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP TRIGGER fail_cache')
        self.assertEqual(learn.nearest(self.db, PLAIN)[0]['sim'], 1.0)

    def test_stale_epoch_rebuilds_but_future_epoch_is_never_downgraded(self):
        self.require_mode()
        learn.store(self.db, 'author', MD, text_format='markdown')
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE exemplars SET ngram='{}',ngram_version=0")
        self.assertEqual(learn.nearest(self.db, 'precondition')[0]['sim'], 1.0)
        self.assertEqual(self.raw('SELECT ngram_version FROM exemplars'), [(1,)])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE exemplars SET ngram_version=2')
        before = self.dump()
        with self.assertRaisesRegex(ValueError, 'index|cache'):
            learn.store(self.db, 'author', MD)
        self.assertEqual(self.dump(), before)

    def test_unversioned_future_index_is_rejected_before_legacy_merge(self):
        self.make_old(version=0, modern=True, duplicate=True)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE exemplars SET ngram_version=2 WHERE id=9')
        before = self.dump()
        with self.assertRaisesRegex(ValueError, 'index|cache'):
            learn.list_exemplars(self.db)
        self.assertEqual(self.dump(), before)

    def test_surface_and_schema_marker_failures_roll_back_entire_upgrade(self):
        connect = sqlite3.connect
        class FailVersion(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == f'PRAGMA user_version = {learn.SCHEMA_VERSION}':
                    raise RuntimeError('version blocked')
                return super().execute(sql, *args)
        for phase in ('surface', 'version'):
            with self.subTest(phase=phase):
                self.db = str(self.root/(phase+'.db'))
                self.make_old()
                before = self.dump()
                failure = (patch.object(learn, '_exemplar_surface', create=True,
                                        side_effect=RuntimeError('surface blocked')) if phase == 'surface'
                           else patch.object(learn.sqlite3, 'connect',
                                             side_effect=lambda db: connect(db, factory=FailVersion)))
                with failure, self.assertRaisesRegex(RuntimeError, phase+' blocked'):
                    learn.list_exemplars(self.db)
                self.assertEqual(self.dump(), before)

    def test_cached_counts_drive_both_document_frequency_and_vectors(self):
        learn.store(self.db, 'author', r'pre\emph{condition}')
        learn.store(self.db, 'author', 'condition')
        with patch.object(learn, '_ngram_counts', wraps=learn._ngram_counts) as counts:
            rows = {row['text']: row for row in learn.nearest(self.db, 'precondition')}
        self.assertEqual(rows[r'pre\emph{condition}']['sim'], 1.0)
        self.assertEqual(rows['condition']['sim'], 0.5756)
        self.assertEqual([call.args[0] for call in counts.call_args_list], ['precondition'])

    def test_same_raw_confirmation_updates_mode_without_duplicate_or_created_change(self):
        self.require_mode()
        learn.store(self.db, 'author', MD)
        original = self.raw('SELECT id,created FROM exemplars')[0]
        learn.store(self.db, 'author', MD, text_format='markdown')
        self.assertEqual(self.raw('SELECT id,created FROM exemplars'), [original])
        self.assertEqual(self.raw('SELECT text_format,rewards FROM exemplars'), [('markdown', 2)])
        learn.store(self.db, 'author', MD)
        self.assertEqual(self.raw('SELECT text_format,rewards FROM exemplars'), [(None, 3)])
        learn.store(self.db, 'author', 'precondition')
        self.assertEqual(len(learn.list_exemplars(self.db)), 2)

    def test_old_receipt_and_optout_never_overwrite_new_exemplar_interpretation(self):
        legacy = self.choice('legacy')
        marked = self.choice('new-markdown', text_format='markdown')
        rows = learn.list_exemplars(self.db)
        self.assertEqual(rows[0].get('text_format'), 'markdown')
        self.assertEqual(rows[0]['rewards'], 2)
        before = self.dump()
        self.assertEqual(self.choice('legacy'), {**legacy, 'replayed': True})
        self.assertEqual(self.choice('new-markdown', text_format='markdown'), {**marked, 'replayed': True})
        self.assertEqual(self.dump(), before)
        row_before = self.raw('SELECT * FROM exemplars')
        self.choice('optout', learn_from_choice=False)
        self.assertEqual(self.raw('SELECT * FROM exemplars'), row_before)

    def test_invalid_mode_and_empty_surface_do_not_create_meaningless_example(self):
        self.require_mode()
        for value in ('tex', False, [], ''):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'text_format'):
                learn.store(self.db, 'author', TEX, text_format=value)
        for text in (r'\emph{}', r'\textbf{!!!}'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'match|empty'):
                learn.store(self.db, 'author', text)
        self.assertFalse(Path(self.db).exists())
        self.assertEqual(learn.nearest(self.db, r'\emph{}'), [])
        self.assertFalse(Path(self.db).exists())
        with self.assertRaisesRegex(ValueError, 'match|empty'):
            self.choice('empty-author', text=r'\emph{}')
        self.assertEqual(self.raw('SELECT COUNT(*) FROM exemplars'), [(0,)])
        self.assertEqual(self.raw('SELECT COUNT(*) FROM choice_events'), [(0,)])
        for table in ('prefs', 'phrase_prefs', 'syntax_prefs'):
            self.assertEqual(self.raw('SELECT COUNT(*) FROM '+table), [(0,)])

    def test_optional_backends_receive_surface_lists_and_return_raw(self):
        self.require_mode()
        literals = r' $x=12$ \cite{Li2024}'
        raw = MD + literals
        learn.store(self.db, 'author', raw, text_format='markdown')
        class Matrix:
            shape = (2, 2)
            def __getitem__(self, index): return [1.0, 0.0]
        for backend in ('sklearn', 'embed'):
            learn.set_backend(self.db, backend)
            result = Matrix() if backend == 'sklearn' else [[1.0, 0.0], [1.0, 0.0]]
            with self.subTest(backend=backend), patch.object(learn, '_'+backend+'_vecs', return_value=result) as vectors, \
                    patch.object(learn, '_cosine_sparse', return_value=1.0):
                row = learn.nearest(self.db, 'pre*condition*'+literals, text_format='markdown')[0]
            self.assertEqual(vectors.call_args.args[0], ['precondition'+literals, 'precondition'+literals])
            self.assertEqual((row['text'], row['sim']), (raw, 1.0))
        self.assertEqual(self.raw('SELECT ngram FROM exemplars'), [(learn._ngram_json('precondition'+literals),)])

    def test_empty_legacy_surface_and_optional_backend_errors_are_bounded(self):
        self.make_old()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE exemplars SET text=?', (r'\emph{}',))
        learn.list_exemplars(self.db)
        learn.set_backend(self.db, 'embed')
        with patch.object(learn, '_embed_vecs', side_effect=AssertionError('empty examples must not load a model')):
            rows = learn.nearest(self.db, 'query')
        self.assertEqual(rows[0]['sim'], 0)
        self.assertEqual(rows[0]['text'], r'\emph{}')
        learn.store(self.db, 'author', 'q')
        learn.set_backend(self.db, 'sklearn')
        with patch.object(learn, '_sklearn_vecs', side_effect=ValueError('empty vocabulary; perhaps documents only contain stop words')):
            self.assertTrue(all(row['sim'] == 0 for row in learn.nearest(self.db, 'q')))
        before = self.dump()
        with patch.object(learn, '_sklearn_vecs', side_effect=RuntimeError('backend failed')):
            with self.assertRaisesRegex(RuntimeError, 'backend failed'):
                learn.nearest(self.db, 'query')
        self.assertEqual(self.dump(), before)

    def test_cli_tail_format_and_legacy_text_positions(self):
        def call(*args):
            return subprocess.run([sys.executable, '-B', '-I', str(ROOT/'library/learn.py'), *map(str, args)],
                                  capture_output=True, text=True)
        stored = call('store', self.db, 'author', MD, '--text-format', 'markdown')
        self.assertEqual(stored.returncode, 0, stored.stderr)
        for suffix in (['--text-format', 'markdown'], [1, '--text-format', 'markdown']):
            result = call('nearest', self.db, 'pre*condition*', *suffix)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]['sim'], 1.0)
        query = self.root/'query.txt'; query.write_text('pre*condition*')
        result = call('nearest-file', self.db, query, '--text-format', 'markdown')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]['text_format'], 'markdown')
        self.assertEqual(call('store', self.db, 'author', '--text-format').returncode, 0)
        self.assertIn('--text-format', [row['text'] for row in learn.list_exemplars(self.db)])
        before = self.dump()
        for tail in (['--text-format'], ['--text-format', 'tex'], ['--text-format', 'markdown', '--text-format', 'markdown'], ['extra']):
            with self.subTest(tail=tail):
                self.assertNotEqual(call('store-file', self.db, 'author', self.root/'missing', *tail).returncode, 0)
                self.assertEqual(self.dump(), before)


if __name__ == '__main__': unittest.main()
