"""Compact transport must preserve ranking, diagnostics and saved decisions."""
from copy import deepcopy
import importlib.util
import json
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


learn = load('summary_engine', ROOT / 'library/learn.py')
adapter = load('summary_adapter', ROOT / 'library/mcp_tools.py')
OMITTED = {'analysis', 'grammar', 'matched_features', 'matched_syntax_features',
           'matched_analysis_features', 'matched_grammar_features', 'matched_analysis_topology_features'}
MATCHES = {'phrase': 'matched_features', 'syntax': 'matched_syntax_features',
           'analysis': 'matched_analysis_features', 'grammar': 'matched_grammar_features',
           'analysis_topology': 'matched_analysis_topology_features'}
NOW = '2026-10-28T00:00:00+00:00'


class RankSummaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-summary-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / 'preference.db'
        self.path = self.root / 'candidates.json'
        raw = {'round_id': 'summary-round', 'learning_context': {'section': 'results', 'role': 'support'},
               'candidates': [{'id': 'keep', 'label': 'Keep original', 'text': '样本稳定。\r\n公式 $k_B$ 与 \\cite{A} 保持原样。',
                               'analysis': {'units': []}, 'grammar': {'frames': []}},
                              {'id': 'alt', 'label': 'Other', 'text': '误差降低。',
                               'analysis': {'units': []}, 'grammar': {'frames': []}}]}
        self.data = learn.bind_candidate_data(raw)
        self.data.update(chosen='keep', author_owned=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False))
        clock = patch.object(learn, '_now_iso', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)

    def dump(self):
        with sqlite3.connect(self.db) as c:
            return list(c.iterdump())

    def assert_projection(self, full, compact):
        self.assertEqual(len(full), len(compact))
        for a, b in zip(full, compact):
            self.assertEqual(b.get('summary_version'), 1, 'summary output must be explicit')
            self.assertEqual(b.get('omitted_fields'), sorted(OMITTED.intersection(a)))
            self.assertEqual(b.get('matched_feature_counts'), {name: len(a.get(key, [])) for name, key in MATCHES.items()})
            self.assertTrue(OMITTED.isdisjoint(b))
            self.assertEqual({k: v for k,v in b.items() if k not in ['summary_version', 'omitted_fields', 'matched_feature_counts']},
                             {k: v for k,v in a.items() if k not in OMITTED})

    def test_projection_preserves_learned_rank_exact_text_and_diagnostics(self):
        self.assertTrue(hasattr(learn, '_rank_summary'), 'compact output helper missing')
        receipt = learn.choice_file(str(self.db), 'results', 'support', self.path)
        state = self.dump(); source = self.path.read_bytes()
        full = learn.rank_candidates(str(self.db), 'results', 'support', self.data['candidates'][::-1])
        saved = deepcopy(full)
        compact = learn._rank_summary(full)
        self.assert_projection(full, compact)
        self.assertEqual(full, saved)
        self.assertEqual(compact[0]['text'], self.data['candidates'][0]['text'])
        self.assertGreater(compact[0]['matched_feature_counts']['phrase'], 0)
        self.assertEqual(self.path.read_bytes(), source)
        self.assertEqual(self.dump(), state)
        self.assertEqual(learn.choice_file(str(self.db), 'results', 'support', self.path), {**receipt, 'replayed': True})
        self.assertEqual(self.dump(), state)

    def test_cli_opt_in_keeps_default_full_and_rejects_unknown_flags_before_store(self):
        base = [sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'), 'rank-file', str(self.db), 'results', 'support', str(self.path)]
        compact = subprocess.run(base + ['--summary'], capture_output=True, text=True, timeout=20)
        self.assertEqual(compact.returncode, 0, compact.stderr)
        state = self.dump()
        full = subprocess.run(base, capture_output=True, text=True, timeout=20)
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assert_projection(json.loads(full.stdout), json.loads(compact.stdout))
        self.assertEqual(self.dump(), state)
        for suffix in [['--typo'], ['--summary', '--summary']]:
            absent = self.root / ('absent-' + str(len(suffix)) + '.db')
            argv = base.copy(); argv[5] = str(absent)
            failed = subprocess.run(argv + suffix, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(absent.exists())

    def test_mcp_summary_uses_same_projection_and_default_full(self):
        tools = adapter.WorkspaceTools(self.root)
        import inspect
        self.assertIn('summary', inspect.signature(tools.rank).parameters, 'MCP summary parameter missing')
        with patch.object(tools.engine, '_now_iso', return_value=NOW):
            tools.record_choice('results', 'support', str(self.path), 'summary-round')
            state = self.dump()
            full = tools.rank('results', 'support', str(self.path))['candidates']
            compact = tools.rank('results', 'support', str(self.path), summary=True)['candidates']
            self.assert_projection(full, compact)
            self.assertEqual(self.dump(), state)
        for bad in ['true', 1, None]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tools.rank('results', 'support', str(self.path), summary=bad)

    def test_summary_preserves_off_and_fallback_status(self):
        self.assertTrue(hasattr(learn, '_rank_summary'), 'compact output helper missing')
        for backend in ['off', 'stanza']:
            learn.set_syntax_backend(str(self.db), backend, str(self.root / 'missing-model'))
            if backend == 'stanza':
                with patch.object(learn, '_stanza_pipeline', side_effect=learn.SyntaxBackendUnavailable('missing local model')):
                    full = learn.rank_candidates(str(self.db), 'results', 'support', self.data['candidates'])
            else:
                full = learn.rank_candidates(str(self.db), 'results', 'support', self.data['candidates'])
            self.assert_projection(full, learn._rank_summary(full))
            self.assertEqual(full[0]['syntax_backend'], 'off' if backend == 'off' else 'heuristic-fallback')

    def test_summary_rows_rejected_before_rank_or_choice_store_access(self):
        full = learn.rank_candidates(str(self.db), 'results', 'support', self.data['candidates'])
        summary = learn._rank_summary(full)
        for marker in ['summary_version', 'omitted_fields', 'matched_feature_counts']:
            for intent in [True, False]:
                with self.subTest(marker=marker, learning=intent):
                    bad = deepcopy(self.data)
                    bad['learn_from_choice'] = intent
                    # Any one retained output marker identifies a transport view.
                    bad['candidates'] = [{k:v for k,v in row.items() if k not in ['summary_version', 'omitted_fields', 'matched_feature_counts'] or k == marker} for row in summary]
                    self.path.write_text(json.dumps(bad, ensure_ascii=False))
                    absent = self.root / (marker + str(intent) + '.db')
                    with patch.object(learn, '_open', side_effect=AssertionError('must reject before opening store')):
                        with self.assertRaisesRegex(ValueError, 'summary.*output'):
                            learn.rank_candidates(str(absent), 'results', 'support', bad['candidates'])
                        with self.assertRaisesRegex(ValueError, 'summary.*output'):
                            learn.choice_file(str(absent), 'results', 'support', self.path)
                    self.assertFalse(absent.exists())

    def test_summary_cli_and_adapter_reject_without_creating_store(self):
        full = learn.rank_candidates(str(self.db), 'results', 'support', self.data['candidates'])
        bad = {**self.data, 'candidates': learn._rank_summary(full)}
        self.path.write_text(json.dumps(bad, ensure_ascii=False))
        for command, suffix in [('rank-file', []), ('rank-file', ['--summary']), ('choice-file', [])]:
            absent = self.root / ('cli-' + command + str(len(suffix)) + '.db')
            p = subprocess.run([sys.executable, '-B', '-I', str(ROOT / 'library/learn.py'), command,
                                str(absent), 'results', 'support', str(self.path), *suffix],
                               capture_output=True, text=True, timeout=20)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn('summary', p.stderr)
            self.assertFalse(absent.exists())
        tools = adapter.WorkspaceTools(self.root)
        state = self.dump()
        for mode in [False, True]:
            with self.assertRaisesRegex(ValueError, 'summary.*output'):
                tools.rank('results', 'support', str(self.path), summary=mode)
        with self.assertRaisesRegex(ValueError, 'summary.*output'):
            tools.record_choice('results', 'support', str(self.path), self.data['round_id'])
        self.assertEqual(self.dump(), state)

    def test_default_full_rows_and_plain_legacy_candidates_still_work(self):
        full = learn.rank_candidates(str(self.db), 'results', 'support', self.data['candidates'])
        baseline_hash = learn._choice_payload_hash('results', 'support', self.data['candidates'], 'keep', True,
                                                  edit_scope=self.data.get('edit_scope'))
        envelope = {**self.data, 'candidates': full, 'metadata': {'summary_version': 'host metadata, not a row'}}
        canonical, pool = learn._validated_candidate_data(envelope)
        self.assertEqual(pool, self.data['candidates'])
        self.assertEqual(learn._choice_payload_hash('results', 'support', pool, 'keep', True,
                                                  edit_scope=canonical.get('edit_scope')), baseline_hash)
        self.path.write_text(json.dumps(envelope, ensure_ascii=False))
        receipt = learn.choice_file(str(self.db), 'results', 'support', self.path)
        state = self.dump()
        bad = {**self.data, 'candidates': learn._rank_summary(full)}
        self.path.write_text(json.dumps(bad, ensure_ascii=False))
        with self.assertRaisesRegex(ValueError, 'summary.*output'):
            learn.choice_file(str(self.db), 'results', 'support', self.path)
        self.assertEqual(self.dump(), state)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False))
        self.assertEqual(learn.choice_file(str(self.db), 'results', 'support', self.path), {**receipt, 'replayed': True})
        plain = [{'id':'plain','label':'P','text':'样品稳定。'}]
        self.assertEqual(learn._validated_candidates(plain), plain)


if __name__ == '__main__':
    unittest.main()
