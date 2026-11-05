"""Explicit Markdown affects extraction, never raw decisions or legacy inputs."""
from contextlib import closing
from copy import deepcopy
import hashlib
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
spec = importlib.util.spec_from_file_location('markdown_format', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
PLAIN = r'测量结果不支持该机制，但不能排除其他解释（见图~\ref{fig:comparison}）。'
MARKED = PLAIN.replace('不支持', '**不**支持')
NOW = '2026-10-28T06:00:00+00:00'


class MarkdownFormatTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-markdown-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = str(self.root / 'preference.db')
        self.path = self.root / 'decision.json'
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.pool = [{'id': 'plain', 'label': 'Equal', 'text': PLAIN},
                     {'id': 'md', 'label': 'Equal', 'text': MARKED}]

    def payload(self, event='one', chosen='md', **extra):
        return {'round_id': event, 'chosen': chosen, 'author_owned': True,
                'candidates': deepcopy(self.pool), **extra}

    def choose(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False))
        return learn.choice_file(self.db, 'results', 'support', self.path)

    def dump(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def view(self, text):
        self.assertIn('text_format', inspect.signature(learn._text_feature_views).parameters)
        return learn._text_feature_views(text, text_format='markdown')[1]

    def test_commonmark_delimiter_runs_flanking_and_rule_of_three(self):
        # Expected delimiter interpretations are frozen from local Pandoc's
        # CommonMark oracle; this is deliberately not a block renderer.
        cases = [('测量结果**不**支持。', '测量结果不支持。'),
                 ('***不支持***。', '不支持。'), ('pre**condition**', 'precondition'),
                 ('a*b*c', 'abc'), ('a***b**c*', 'abc'), ('a**b*c***', 'abc'),
                 ('*foo **bar** baz*', 'foo bar baz'), ('***foo** bar*', 'foo bar'),
                 ('snake_case_name', 'snake_case_name'), ('不_支持_机制', '不_支持_机制'),
                 ('_foo_', 'foo'), ('__foo__', 'foo'), ('___foo___', 'foo'),
                 ('€_支持_€', '€支持€'), ('€__支持__€', '€支持€'), ('€___支持___€', '€支持€'),
                 ('a**b*c**d', 'ab*cd'), ('a***b*c**d', 'abcd'),
                 ('* foo*', '* foo*'), ('*foo *', '*foo *'),
                 (r'前\*内\*后', '前*内*后'), (r'前\_内\_后', '前_内_后')]
        cases.append((r'\[a*b*c', '[abc'))
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(self.view(source), expected)

    def test_opaque_code_math_destinations_html_and_tex_literals_keep_markers(self):
        cases = [('`**不**支持`', '`**不**支持`'),
                 ('``a`*b*c``', '``a`*b*c``'),
                 (r'$a*b*c$ **结果**', r'$a*b*c$ 结果'),
                 (r'\(a*b*c\) **结果**', r'\(a*b*c\) 结果'),
                 (r'\cite{a*b*c} **结果**', r'\cite{a*b*c} 结果'),
                 ('[**不**支持](https://example.test/a*b*c)', '[不支持](https://example.test/a*b*c)'),
                 ('<span title="a*b*c">**不**支持</span>', '<span title="a*b*c">不支持</span>'),
                 ('<!-- a*b*c --> **结果**', '<!-- a*b*c --> 结果'),
                 ('<foo*bar*baz@example.com>', '<foo*bar*baz@example.com>'),
                 ('[a*b](c*d)', '[a*b](c*d)'),
                 ('*[**label**](url)*', '[label](url)'),
                 ('*outside [inside*](url)', '*outside [inside*](url)')]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(self.view(source), expected)
        self.assertIn('text_format', inspect.signature(learn._phrase_features).parameters)
        self.assertEqual(learn._phrase_features(r'甲**$secret$**乙', text_format='markdown'), [])

    def test_default_is_unchanged_and_markdown_opt_in_is_explicit(self):
        self.assertEqual(learn._text_feature_views('a*b*c')[1], 'a*b*c')
        self.assertEqual(learn._text_feature_views('snake_case_name')[1], 'snake_case_name')
        self.assertEqual(self.view('a*b*c'), 'abc')
        self.assertEqual(self.view(r'前\emph{段}**后**'), '前段后')
        self.assertEqual(learn._text_feature_views(r'前\emph{段}**后**')[1], '前段**后**')

    def test_root_and_candidate_placement_are_validated_before_database(self):
        for value in (None, False, True, 1, [], {}, '', 'Markdown', ' markdown', 'tex', 'plain'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'text_format'):
                learn._validated_candidate_data(self.payload(text_format=value))
        candidate = {**self.pool[0], 'text_format': 'markdown'}
        with self.assertRaisesRegex(ValueError, 'root'):
            learn._validated_candidates([candidate])
        self.assertFalse(Path(self.db).exists())

    def test_direct_rank_validates_keyword_before_open_and_keeps_rows_unmarked(self):
        self.assertIn('text_format', inspect.signature(learn.rank_candidates).parameters)
        for value in (False, [], {}, '', 'tex', 'Markdown'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'text_format'):
                learn.rank_candidates(self.db, 'results', 'support', self.pool, text_format=value)
        self.assertFalse(Path(self.db).exists())
        rows = learn.rank_candidates(self.db, 'results', 'support', self.pool, text_format='markdown')
        self.assertTrue(all('text_format' not in row for row in rows))
        self.assertEqual(rows[0]['phrase_feature_count'], rows[1]['phrase_feature_count'])

    def test_real_choice_reinforces_shared_phrase_and_base_syntax(self):
        self.choose(self.payload('seed', 'plain'))
        receipt = self.choose(self.payload('markdown-choice', text_format='markdown'))
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT weight,picks FROM phrase_prefs WHERE feature='cjk3:不支持'").fetchone(), (2000, 2))
            self.assertEqual(conn.execute("SELECT weight,picks FROM syntax_prefs WHERE feature='syntax:v3:heuristic:cue_pos:contrast@start'").fetchone(), (3000, 2))
        self.assertEqual(receipt.get('text_format'), 'markdown')
        rows = learn.rank_candidates(self.db, 'results', 'support', self.pool, text_format='markdown')
        self.assertEqual(rows[0]['score'], rows[1]['score'])
        self.assertEqual(learn.list_exemplars(self.db, 'author')[0]['text'], MARKED)

    def test_binder_preserves_mode_raw_text_hash_and_offsets(self):
        draft = self.payload(text_format='markdown')
        for item in draft['candidates']:
            item['analysis'] = {'spans': [{'text': item['text'], 'move': 'result',
                                         'polarity': 'negative', 'certainty': 'asserted'}]}
            item['grammar'] = {'frames': []}
        try:
            bound = learn.bind_candidate_data(draft)
        except ValueError as error:
            self.fail(f'Markdown draft rejected: {error}')
        self.assertEqual(bound['text_format'], 'markdown')
        item = bound['candidates'][1]
        self.assertEqual(item['text'], MARKED)
        self.assertEqual(item['analysis']['text_sha256'], hashlib.sha256(MARKED.encode()).hexdigest())
        self.assertEqual(item['analysis']['spans'][0]['end'], len(MARKED))
        self.path.write_text(json.dumps(draft, ensure_ascii=False))
        output = self.root / 'bound.json'
        learn.bind_file(self.path, output)
        before = output.read_bytes()
        self.assertTrue(learn.bind_file(self.path, output)['reused'])
        self.assertEqual(output.read_bytes(), before)

    def test_cli_full_and_summary_forward_saved_format(self):
        self.choose(self.payload('seed', 'plain'))
        self.path.write_text(json.dumps(self.payload('pending', text_format='markdown'), ensure_ascii=False))
        for summary in (False, True):
            with self.subTest(summary=summary):
                result = subprocess.run([sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'),
                    'rank-file', self.db, 'results', 'support', str(self.path),
                    *(['--summary'] if summary else [])], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = json.loads(result.stdout)
                self.assertEqual(rows[0]['score'], rows[1]['score'])
                self.assertEqual(rows[0]['phrase_feature_count'], rows[1]['phrase_feature_count'])
                if summary:
                    self.assertEqual(rows[0]['matched_feature_counts'], rows[1]['matched_feature_counts'])
                else:
                    self.assertEqual(rows[0]['matched_features'], rows[1]['matched_features'])
                self.assertTrue(all('text_format' not in row for row in rows))

    def test_mode_is_immutable_in_normal_and_optout_decisions(self):
        for optout in (False, True):
            for marked in (False, True):
                with self.subTest(optout=optout, marked=marked):
                    data = self.payload(f'identity-{optout}-{marked}', learn_from_choice=not optout)
                    if marked: data['text_format'] = 'markdown'
                    receipt = self.choose(data)
                    self.assertEqual(receipt.get('text_format'), 'markdown' if marked else None)
                    state = self.dump()
                    changed = deepcopy(data)
                    if marked: changed.pop('text_format')
                    else: changed['text_format'] = 'markdown'
                    with self.assertRaisesRegex(ValueError, 'different decision'):
                        self.choose(changed)
                    self.assertEqual(self.dump(), state)
                    with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('replay parsed')):
                        self.assertEqual(self.choose(data), {**receipt, 'replayed': True})

    def test_legacy_hash_and_old_wrong_feedback_are_not_rewritten(self):
        candidates = learn._validated_candidates(self.pool)
        original = {'version': 1, 'section': 'results', 'role': 'support',
                    'candidates': sorted(candidates, key=lambda x: x['id']),
                    'chosen': 'md', 'author_owned': True}
        expected = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True,
                                            separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(learn._choice_payload_hash('results', 'support', candidates, 'md', True), expected)
        self.choose(self.payload('legacy-seed', 'plain'))
        old_data = self.payload('legacy-markup')
        old = self.choose(old_data)
        self.assertNotIn('text_format', old)
        state = self.dump()
        self.assertEqual(self.choose(old_data), {**old, 'replayed': True})
        self.assertEqual(self.dump(), state)
        self.choose(self.payload('new-markdown', text_format='markdown'))
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT weight,picks FROM phrase_prefs WHERE feature='cjk3:不支持'").fetchone(), (1750, 2))

    def test_stanza_and_fallback_receive_markdown_surface_and_preserve_literals(self):
        self.assertIn('text_format', inspect.signature(learn._syntax_features).parameters)
        learn.set_syntax_backend(self.db, 'stanza', str(self.root), 'fixture')
        text = r'pre**condition** $a*b*c$ \cite{a*b*c}'
        expected = r'precondition $a*b*c$ \cite{a*b*c}'
        with patch.object(learn, '_stanza_syntax_features', return_value=['fixture']) as parser:
            self.assertEqual(learn._syntax_features(text, self.db, text_format='markdown'), (['fixture'], 'stanza'))
            self.assertEqual(parser.call_args.args[0], expected)
        diagnostics = {}
        with patch.object(learn, '_stanza_syntax_features', side_effect=learn.SyntaxBackendUnavailable('fixture unavailable')):
            features, backend = learn._syntax_features(text, self.db, diagnostics, text_format='markdown')
        self.assertEqual(features, learn._heuristic_syntax_features(expected))
        self.assertEqual(backend, 'heuristic-fallback')
        self.assertEqual(diagnostics['fallback_reason'], 'fixture unavailable')


if __name__ == '__main__': unittest.main()
