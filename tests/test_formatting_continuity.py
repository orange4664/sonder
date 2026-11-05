"""Supported text formatting must not turn retained wording into rejection."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('formatting_continuity', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)
PLAIN = '测量结果不支持该机制。'
FORMATTED = r'测量结果\emph{不}支持该机制。'
WRAPPERS = ('emph', 'textbf', 'textit', 'textmd', 'textnormal', 'textrm',
            'textsf', 'texttt', 'textup', 'textsl', 'textsc', 'underline')
NOW = '2026-10-28T04:00:00+00:00'
WRITER_PLAIN = r'测量结果不支持该机制，但不能排除其他解释（见图~\ref{fig:comparison}）。'
WRITER_FORMATTED = WRITER_PLAIN.replace('不支持', r'\emph{不}支持')
CONTRAST_START = 'syntax:v3:heuristic:cue_pos:contrast@start'


class FormattingContinuityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-formatting-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = str(self.root / 'preference.db')
        self.path = self.root / 'decision.json'
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        learn.set_syntax_backend(self.db, 'off')

    def choose(self, round_id, chosen='formatted', candidates=None):
        if candidates is None:
            candidates = [{'id': 'plain', 'label': 'Neutral', 'text': PLAIN},
                          {'id': 'formatted', 'label': 'Neutral', 'text': FORMATTED}]
        self.path.write_text(json.dumps({'round_id': round_id, 'chosen': chosen,
                                        'author_owned': True, 'candidates': candidates}, ensure_ascii=False))
        return learn.choice_file(self.db, 'results', 'support', self.path)

    def weights(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return {row[0]: row[1:] for row in conn.execute(
                'SELECT feature,weight,picks,last_at FROM phrase_prefs')}

    def dump(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def writer_base_candidates(self):
        # Use the actual writer's text and isolate its base-syntax family.
        # Empty host annotations suppress lexical academic/template guesses;
        # these are controlled inputs, not claims about annotation quality.
        result = []
        for key, text in (('plain', WRITER_PLAIN), ('formatted', WRITER_FORMATTED)):
            identity = {'version': 1, 'source': 'writing-model',
                        'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
            result.append({'id': key, 'label': 'Neutral', 'text': text,
                           'analysis': {**identity, 'spans': []},
                           'grammar': {**identity, 'frames': []}})
        return result

    def syntax_weights(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return {row[0]: row[1:] for row in conn.execute(
                'SELECT feature,weight,picks,last_at FROM syntax_prefs')}

    def test_supported_wrappers_preserve_cjk_boundaries_and_latin_words(self):
        for command in WRAPPERS:
            for plain, formatted in (
                    (PLAIN, '测量结果\\' + command + '{不}支持该机制。'),
                    ('We precondition samples.', 'We pre\\' + command + '{condition} samples.')):
                with self.subTest(command=command, plain=plain):
                    self.assertEqual(learn._phrase_features(formatted), learn._phrase_features(plain))

    def test_nested_wrappers_and_parameter_whitespace_remain_contiguous(self):
        cases = [
            (r'测量\textbf{结果\emph{不}支}持该机制。', PLAIN),
            (r'pre\emph{con\textbf{di}tion}', 'precondition'),
            ('测量结果\\emph \n\t{不}支持该机制。', PLAIN),
            (r'pre\emph{}condition', 'precondition'),
            (r'测量\emph{结果}\textbf{不}支持该机制。', PLAIN),
        ]
        for formatted, plain in cases:
            with self.subTest(formatted=formatted):
                self.assertEqual(learn._phrase_features(formatted), learn._phrase_features(plain))

    def test_format_only_choice_reinforces_shared_phrases_instead_of_rejecting(self):
        self.choose('seed', chosen='plain')
        self.assertEqual(self.weights()['cjk3:不支持'], (1000, 1, NOW))
        receipt = self.choose('formatted-approval')
        expected = set(learn._phrase_features(PLAIN))
        self.assertEqual(set(self.weights()), expected)
        for feature in expected:
            with self.subTest(feature=feature):
                self.assertEqual(self.weights()[feature], (2000, 2, NOW))
        self.assertEqual(receipt['features'], len(expected))
        rows = learn.rank_candidates(self.db, 'results', 'support', [
            {'id': 'plain', 'label': 'Neutral', 'text': PLAIN},
            {'id': 'formatted', 'label': 'Neutral', 'text': FORMATTED}])
        self.assertEqual([row['phrase_score'] for row in rows], [2000, 2000])

    def test_rank_annotations_and_author_exemplar_keep_exact_raw_text(self):
        text = FORMATTED + '\r\n'
        digest = hashlib.sha256(text.encode()).hexdigest()
        event = text.index('支持')
        item = {'id': 'formatted', 'label': 'Other', 'text': text,
                'analysis': {'version': 1, 'source': 'writing-model', 'text_sha256': digest,
                             'spans': [{'start': 0, 'end': len(text), 'text': text,
                                        'move': 'result', 'polarity': 'negative', 'certainty': 'asserted'}]},
                'grammar': {'version': 1, 'source': 'writing-model', 'text_sha256': digest,
                            'frames': [{'start': 0, 'end': len(text), 'text': text,
                                        'kind': 'expression', 'form': 'verbal', 'voice': 'active',
                                        'slots': [{'start': event, 'end': event + 2,
                                                   'text': '支持', 'role': 'event'}]}]}}
        before = deepcopy(item)
        self.choose('exact-raw', candidates=[item])
        self.assertEqual(learn.list_exemplars(self.db, 'author')[0]['text'], text)
        row = learn.rank_candidates(self.db, 'results', 'support', [item])[0]
        for key in ('text', 'analysis', 'grammar'):
            self.assertEqual(row[key], before[key])
        self.assertEqual(item, before)
        self.assertEqual(row['phrase_feature_count'], len(learn._phrase_features(PLAIN)))

    def test_escaped_delimiters_and_control_symbols_are_not_wrapper_boundaries(self):
        cases = [(r'前\emph{端\}内}后', r'前端\}内后'),
                 (r'pre\emph{con\{di\}tion}post', r'precon\{di\}tionpost'),
                 (r'pre\emph{con\$dition}post', r'precon\$ditionpost'),
                 (r'甲\emph{乙\%丙}丁', r'甲乙\%丙丁')]
        for formatted, plain in cases:
            with self.subTest(formatted=formatted):
                self.assertEqual(learn._phrase_features(formatted), learn._phrase_features(plain))
        escaped = r'pre\\emph{condition}post'
        self.assertEqual(learn._phrase_prose(escaped), escaped)
        self.assertNotIn('word:preconditionpost', learn._phrase_features(escaped))

    def test_unknown_starred_and_malformed_arguments_remain_conservative(self):
        cases = [r'pre\unknown{condition}post retain', r'pre\Emph{condition}post retain',
                 r'pre\emph*{condition}post retain', r'pre\emph[style]{condition}post retain',
                 r'pre\emph{condition post retain', r'pre\emph condition post retain',
                 r'pre\emph{con\textbf{di}tion post retain',
                 'pre\\emph{condition%}\npost} retain', r'pre\emph{condition%post} retain']
        for text in cases:
            with self.subTest(text=text):
                features = learn._phrase_features(text)
                self.assertIn('word:retain', features)
                self.assertIn('word:pre', features)
                self.assertNotIn('word:preconditionpost', features)

    def test_scientific_literals_inside_wrappers_still_separate_and_never_leak(self):
        literals = [r'$secret\emph{hidden}$', r'\(secret\emph{hidden}\)',
                    r'\begin{equation}secret\emph{hidden}\end{equation}',
                    r'\citep[see]{secret\emph{hidden}}', r'\ref{secret\emph{hidden}}',
                    r'\url{https://secret.test/\emph{hidden}}', '12']
        for literal in literals:
            with self.subTest(literal=literal):
                self.assertEqual(learn._phrase_features('甲\\emph{' + literal + '}乙'), [])
                self.assertEqual(learn._phrase_features(literal), [])
        self.assertEqual(learn._phrase_features(r'pre\emph{\cite{secret}}condition'),
                         ['word:condition', 'word:pre'])
        self.assertEqual(learn._phrase_features(r'甲\emph{乙$secret$丙}丁'),
                         ['cjk2:丙丁', 'cjk2:甲乙'])

    def test_old_formatting_receipt_replays_without_repair_or_backfill(self):
        self.choose('seed', chosen='plain')
        # Recreate the old command-name mask for these two fixed input texts.
        # The saved historical decision is intentionally wrong and immutable.
        with patch.object(learn, '_phrase_prose', side_effect=lambda text: text.replace(r'\emph', ' ' * 5)):
            old = self.choose('old-formatted-choice')
        self.assertEqual(self.weights()['cjk3:不支持'], (750, 1, NOW))
        before = self.dump()
        self.assertEqual(learn.choice_file(self.db, 'results', 'support', self.path),
                         {**old, 'replayed': True})
        self.assertEqual(self.dump(), before)
        new = self.choose('new-formatted-approval')
        self.assertGreater(new['features'], old['features'])
        self.assertEqual(self.weights()['cjk3:不支持'], (1750, 2, NOW))

    def test_actual_sentence_heuristic_features_ignore_formatting_offsets(self):
        learn.set_syntax_backend(self.db, 'heuristic')
        plain, backend = learn._syntax_features(WRITER_PLAIN, self.db)
        self.assertEqual(backend, 'heuristic')
        self.assertIn(CONTRAST_START, plain)
        for command in WRAPPERS:
            formatted = WRITER_PLAIN.replace('不支持', '\\' + command + '{不}支持')
            with self.subTest(command=command):
                self.assertEqual(learn._syntax_features(formatted, self.db), (plain, backend))

    def test_format_only_choice_keeps_existing_base_cue_positive(self):
        learn.set_syntax_backend(self.db, 'heuristic')
        pool = self.writer_base_candidates()
        self.choose('base-seed', chosen='plain', candidates=pool)
        before = self.syntax_weights()
        self.assertEqual(before[CONTRAST_START], (1500, 1, NOW))
        self.choose('base-formatted', candidates=pool)
        after = self.syntax_weights()
        self.assertEqual(after[CONTRAST_START], (3000, 2, NOW))
        self.assertEqual(set(after), set(before), 'formatting must not invent a new cue position')
        self.assertTrue(all(row == (3000, 2, NOW) for row in after.values()))
        ranked = learn.rank_candidates(self.db, 'results', 'support', pool)
        self.assertEqual(ranked[0]['matched_syntax_features'], ranked[1]['matched_syntax_features'])
        self.assertEqual(ranked[0]['score'], ranked[1]['score'])

    def test_stanza_adapter_receives_visible_formatting_but_unchanged_literals(self):
        learn.set_syntax_backend(self.db, 'stanza', str(self.root), 'adapter-fixture')
        literal = (r'$\emph{noise}$ \(\textbf{power}\) '
                   r'\begin{equation}\emph{value}\end{equation} '
                   r'\citep{Key\emph{2024}} \ref{eq:\textbf{key}} '
                   r'\url{https://example.test/\emph{target}}')
        text = r'pre\emph{con\textbf{di}tion} ' + literal
        visible = 'precondition ' + literal
        received = []

        def parser(value):
            received.append(value)
            token = SimpleNamespace(text=value, start_char=0, words=[SimpleNamespace(
                id=1, text=value, upos='NOUN', head=0, deprel='root')])
            return SimpleNamespace(sentences=[SimpleNamespace(tokens=[token])])

        parser._sonder_model_version = 'adapter-fixture'
        parser._sonder_model_fingerprint = 'a' * 64
        diagnostics = {}
        with patch.object(learn, '_stanza_pipeline', return_value=parser):
            features, backend = learn._syntax_features(text, self.db, diagnostics)
            plain, _ = learn._syntax_features(visible, self.db)
        self.assertEqual(received, [visible, visible])
        self.assertEqual(features, plain)
        self.assertEqual(backend, 'stanza')
        self.assertEqual(diagnostics, {'requested_backend': 'stanza', 'fallback_reason': None,
                                      'model_version': 'adapter-fixture', 'model_fingerprint': 'a' * 64})

    def test_failed_stanza_uses_same_visible_surface_and_keeps_diagnostics(self):
        learn.set_syntax_backend(self.db, 'stanza', str(self.root), 'adapter-fixture')
        diagnostics = {}
        with patch.object(learn, '_stanza_syntax_features',
                          side_effect=learn.SyntaxBackendUnavailable('fixture parser unavailable')) as parser:
            features, backend = learn._syntax_features(WRITER_FORMATTED, self.db, diagnostics)
        self.assertEqual(parser.call_args.args[0], WRITER_PLAIN)
        self.assertEqual(features, learn._heuristic_syntax_features(WRITER_PLAIN))
        self.assertEqual(backend, 'heuristic-fallback')
        self.assertEqual(diagnostics, {'requested_backend': 'stanza',
                                      'fallback_reason': 'fixture parser unavailable',
                                      'model_version': None, 'model_fingerprint': None})

    def test_syntax_keeps_unsupported_and_escaped_controls_unchanged(self):
        learn.set_syntax_backend(self.db, 'stanza', str(self.root), 'adapter-fixture')
        cases = [r'pre\unknown{condition}post', r'pre\Emph{condition}post',
                 r'pre\emph*{condition}post', r'pre\emph[style]{condition}post',
                 r'pre\emph{condition post retain', r'pre\\emph{condition}post',
                 'pre\\emph{condition%}\npost} retain']
        for text in cases:
            with self.subTest(text=text), patch.object(learn, '_stanza_syntax_features',
                                                     return_value=['fixture-key']) as parser:
                learn._syntax_features(text, self.db)
                self.assertEqual(parser.call_args.args[0], text)

    def test_old_raw_syntax_receipt_replays_without_repairing_intermediate_weights(self):
        learn.set_syntax_backend(self.db, 'heuristic')
        pool = self.writer_base_candidates()
        self.choose('base-seed', chosen='plain', candidates=pool)

        def old_raw_syntax(text, db, diagnostics=None):
            if diagnostics is not None:
                diagnostics.update(requested_backend='heuristic', fallback_reason=None,
                                   model_version=None, model_fingerprint=None)
            return learn._heuristic_syntax_features(text), 'heuristic'

        with patch.object(learn, '_syntax_features', side_effect=old_raw_syntax):
            old = self.choose('intermediate-format-only-choice', candidates=pool)
        self.assertEqual(self.syntax_weights()[CONTRAST_START], (1250, 1, NOW))
        before = self.dump()
        with patch.object(learn, '_candidate_syntax_features', side_effect=AssertionError('replay must not reparse')):
            self.assertEqual(learn.choice_file(self.db, 'results', 'support', self.path),
                             {**old, 'replayed': True})
        self.assertEqual(self.dump(), before)
        self.choose('new-visible-approval', candidates=pool)
        self.assertEqual(self.syntax_weights()[CONTRAST_START], (2750, 2, NOW))
        self.assertEqual(self.syntax_weights()['syntax:v3:heuristic:cue_pos:contrast@middle'],
                         (1500, 1, NOW), 'historical mistaken keys are not backfilled or deleted')


if __name__ == '__main__':
    unittest.main()
